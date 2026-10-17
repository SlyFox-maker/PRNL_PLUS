"""Matrix DM integration for per-session link recovery commands and notices."""
import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import qrcode

from .db import AppSetting, DB, Session
from .security import decrypt, encrypt


class MatrixIntegration:
    def __init__(self, runtime: Path, runner):
        self.state_file = runtime / "matrix-session.enc"
        self.avatar_cache = runtime / "matrix-avatars"
        self.avatar_cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.runner = runner
        self.config = None
        self.sync_task = None
        self.sync_error = ""
        self.recovery_tasks = {}
        self.notification_tasks = set()
        self.notification_locks = {}
        self.command_locks = {}
        self.room_locks = {}
        self.stopping = False

    def load(self):
        if self.state_file.exists():
            self.config = decrypt(self.state_file.read_text())

    @property
    def enabled(self):
        return bool(self.config and self.config.get("enabled", True))

    async def set_enabled(self, enabled):
        if not self.config:
            raise ValueError("Сначала подключите Matrix аккаунт в настройках")
        if self.enabled == enabled:
            return self.status()
        if not enabled:
            await self.stop()
        self.config["enabled"] = enabled
        if enabled:
            # Commands written while the bot was paused must not execute on resume.
            self.config["since"] = ""
            self.config["seen_events"] = []
        self.sync_error = ""
        self._persist()
        if enabled:
            await self.start()
        return self.status()

    def _persist(self):
        if not self.config:
            self.state_file.unlink(missing_ok=True)
            return
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(encrypt(self.config))
        temp.chmod(0o600)
        os.replace(temp, self.state_file)

    @staticmethod
    def normalize_homeserver(value):
        try:
            parsed = urlsplit(value.strip())
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            if parsed.query or parsed.fragment:
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
            return value.strip().rstrip("/")
        except (ValueError, AttributeError):
            raise ValueError("Укажите адрес Matrix homeserver, например https://matrix.example.org или http://192.168.1.10:8008") from None

    async def login(self, homeserver, username, password):
        homeserver = self.normalize_homeserver(homeserver)
        if not username.strip() or not password:
            raise ValueError("Введите Matrix логин и пароль")
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(homeserver + "/_matrix/client/v3/login", json={
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": username.strip()},
                    "password": password,
                    "initial_device_display_name": "PRNL link assistant",
                })
            if response.status_code >= 400:
                raise ValueError("Matrix не принял логин и пароль. Проверьте homeserver и данные аккаунта.")
            data = response.json()
            if not data.get("user_id") or not data.get("access_token"):
                raise ValueError("Matrix ответил без access token")
        except httpx.HTTPError:
            raise ValueError("Не удалось подключиться к Matrix homeserver") from None
        previous = self.config
        await self.stop()
        self.config = {"homeserver": homeserver, "user_id": data["user_id"],
                       "access_token": data["access_token"], "device_id": data.get("device_id", ""),
                       "since": "", "seen_events": []}
        self.sync_error = ""
        try:
            self._persist()
            await self.start()
        except Exception:
            self.config = previous
            self._persist()
            await self.start()
            raise
        return self.status()

    async def logout(self):
        await self.stop()
        if self.config:
            try:
                await self._request("POST", "/_matrix/client/v3/logout", json={})
            except Exception:
                pass
        self.config = None
        self.sync_error = ""
        self._persist()
        return self.status()

    def status(self):
        return {"configured": bool(self.config), "enabled": self.enabled,
                "user_id": self.config.get("user_id", "") if self.config else "",
                "homeserver": self.config.get("homeserver", "") if self.config else "",
                "sync_running": bool(self.sync_task and not self.sync_task.done()), "error": self.sync_error}

    def get_greeting(self):
        with DB() as db:
            setting = db.get(AppSetting, "matrix_greeting")
            return setting.value if setting else ("Привет! Я бот PRNL. Буду присылать сюда актуальную ссылку и QR-код звонка для сессии «{session}».\n\n"
                                                  "Для стабильного подключения выставь в настройках приложения DNS: 8.8.8.8 и 1.1.1.1.\n\n"
                                                  "Напишите !help для списка команд или !refresh, чтобы обновить ссылку.")

    def set_greeting(self, greeting):
        value = greeting.strip()[:500]
        with DB.begin() as db:
            setting = db.get(AppSetting, "matrix_greeting")
            if setting:
                setting.value = value
            else:
                db.add(AppSetting(key="matrix_greeting", value=value))
        return value

    async def _request(self, method, path, *, json_body=None, params=None, content=None, content_type=None, timeout=15):
        if not self.config:
            raise ValueError("Сначала подключите Matrix аккаунт в настройках")
        headers = {"Authorization": "Bearer " + self.config["access_token"]}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, self.config["homeserver"] + path, headers=headers,
                                                json=json_body, params=params, content=content)
        except httpx.HTTPError:
            raise ValueError("Не удалось связаться с Matrix homeserver") from None
        if response.status_code >= 400:
            if response.status_code == 401:
                self.sync_error = "Matrix access token истёк или отозван. Подключите аккаунт заново."
                raise ValueError(self.sync_error)
            raise ValueError(f"Matrix запрос завершился ошибкой HTTP {response.status_code}")
        return response

    async def search_users(self, query):
        if not self.config:
            raise ValueError("Сначала подключите Matrix аккаунт в настройках")
        term = query.strip()
        if len(term) < 2:
            return []
        response = await self._request("POST", "/_matrix/client/v3/user_directory/search",
                                       json_body={"search_term": term, "limit": 12})
        results = response.json().get("results", [])
        users = [{"user_id": item.get("user_id", ""), "display_name": item.get("display_name") or item.get("user_id", ""),
                  "has_avatar": bool(item.get("avatar_url"))}
                 for item in results if isinstance(item.get("user_id"), str)]
        # Homeservers can hide valid accounts from their searchable directory.
        # An exact Matrix ID can still be resolved through the profile endpoint.
        if re.fullmatch(r"@[^:\s]{1,255}:[^\s]{1,255}", term) and not any(user["user_id"] == term for user in users):
            try:
                profile = await self._request("GET", "/_matrix/client/v3/profile/" + quote(term, safe=""))
            except ValueError as exc:
                if str(exc) not in ("Matrix запрос завершился ошибкой HTTP 403", "Matrix запрос завершился ошибкой HTTP 404"):
                    raise
            else:
                data = profile.json()
                users.insert(0, {"user_id": term, "display_name": data.get("displayname") or term,
                                 "has_avatar": bool(data.get("avatar_url"))})
        return users[:12]

    async def avatar(self, user_id):
        if not self.config or not re.fullmatch(r"@[^:\s]{1,255}:[^\s]{1,255}", user_id):
            raise ValueError("Некорректный Matrix user ID")
        response = await self._request("GET", "/_matrix/client/v3/profile/" + quote(user_id, safe=""))
        uri = response.json().get("avatar_url", "")
        if not isinstance(uri, str) or not uri.startswith("mxc://"):
            raise ValueError("У пользователя нет доступной аватарки")
        server_media = uri[6:].split("/", 1)
        if len(server_media) != 2:
            raise ValueError("Некорректный Matrix media URI")
        media_server, media_id = server_media
        path = f"/_matrix/client/v1/media/thumbnail/{quote(media_server, safe='')}" \
               f"/{quote(media_id, safe='')}"
        image = await self._request("GET", path, params={"width": "48", "height": "48", "method": "crop"})
        mime = image.headers.get("content-type", "image/png").split(";", 1)[0]
        if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif") or len(image.content) > 2 * 1024 * 1024:
            raise ValueError("Matrix вернул неподдерживаемый или слишком большой файл аватарки")
        return image.content, mime

    def _avatar_dir(self, sid):
        # Session IDs are external path parameters; never use them as file paths.
        key = hashlib.sha256(sid.encode("utf-8")).hexdigest()
        return self.avatar_cache / key

    def clear_session_avatar(self, sid):
        shutil.rmtree(self._avatar_dir(sid), ignore_errors=True)

    async def session_avatar(self, sid):
        with DB() as db:
            session = db.get(Session, sid)
            if not session or not session.matrix_user_id:
                raise ValueError("Для сессии не назначен Matrix-пользователь")
            user_id = session.matrix_user_id
        directory = self._avatar_dir(sid)
        image_file, mime_file = directory / "avatar", directory / "mime"
        if image_file.is_file() and mime_file.is_file():
            return image_file.read_bytes(), mime_file.read_text().strip()
        content, mime = await self.avatar(user_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        image_tmp, mime_tmp = directory / "avatar.tmp", directory / "mime.tmp"
        image_tmp.write_bytes(content)
        mime_tmp.write_text(mime)
        image_tmp.chmod(0o600)
        mime_tmp.chmod(0o600)
        os.replace(image_tmp, image_file)
        os.replace(mime_tmp, mime_file)
        return content, mime

    async def set_session_user(self, sid, user_id, display_name=""):
        if user_id and not self.config:
            raise ValueError("Сначала подключите Matrix аккаунт в настройках")
        if user_id and not re.fullmatch(r"@[^:\s]{1,255}:[^\s]{1,255}", user_id):
            raise ValueError("Некорректный Matrix user ID")
        with DB.begin() as db:
            session = db.get(Session, sid)
            if not session:
                raise ValueError("Сессия не найдена")
            previous_user_id = session.matrix_user_id
            session.matrix_user_id = user_id
            session.matrix_display_name = display_name[:255] if user_id else ""
            session.matrix_has_avatar = False
            if not user_id or user_id != previous_user_id:
                session.matrix_room_id = ""
                session.matrix_greeting_sent = False
        if user_id != previous_user_id:
            self.clear_session_avatar(sid)

    async def _ensure_room(self, sid):
        # Serialize first-contact setup so concurrent bind and link actions cannot
        # create duplicate rooms or send the configured greeting twice.
        lock = self.room_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            with DB() as db:
                session = db.get(Session, sid)
                if not session or not session.matrix_user_id:
                    raise ValueError("Matrix получатель для сессии не назначен")
                room_id = session.matrix_room_id
                user_id, display_name = session.matrix_user_id, session.matrix_display_name
                session_name = session.name
                greeted = session.matrix_greeting_sent
            if not room_id:
                response = await self._request("POST", "/_matrix/client/v3/createRoom", json_body={
                    "invite": [user_id], "is_direct": True, "preset": "trusted_private_chat",
                    "name": f"PRNL · {session_name}"[:100],
                })
                room_id = response.json().get("room_id")
                if not room_id:
                    raise ValueError("Matrix не вернул ID личной комнаты")
                with DB.begin() as db:
                    session = db.get(Session, sid)
                    if not session or session.matrix_user_id != user_id:
                        raise ValueError("Matrix получатель сессии изменился во время создания комнаты")
                    session.matrix_room_id = room_id
                    greeted = session.matrix_greeting_sent
            if not greeted:
                greeting = self.get_greeting().replace("{session}", session_name)
                try:
                    if greeting:
                        await self._send_with_retries(room_id, {"msgtype": "m.text", "body": greeting})
                    with DB.begin() as db:
                        session = db.get(Session, sid)
                        if session and session.matrix_user_id == user_id and session.matrix_room_id == room_id:
                            session.matrix_greeting_sent = True
                except Exception as exc:
                    raise ValueError(f"Комната Matrix создана и приглашение отправлено, но приветствие не доставлено: {exc}") from None
            return room_id, user_id, display_name

    async def start_conversation(self, sid):
        if not self.enabled:
            return
        with DB() as db:
            session = db.get(Session, sid)
            if not session or not session.matrix_user_id:
                raise ValueError("Matrix получатель для сессии не назначен")
            had_room = bool(session.matrix_room_id)
        await self._ensure_room(sid)
        if not had_room:
            with DB() as db:
                session = db.get(Session, sid)
                link = decrypt(session.link_secret) if session and session.state == "running" and session.link_secret else ""
            if link:
                await self.send_link(sid, link)

    async def _send_event(self, room_id, content, txn_id=None):
        txn_id = txn_id or uuid4().hex
        await self._request("PUT", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{txn_id}",
                            json_body=content)

    async def _send_with_retries(self, room_id, content):
        error = None
        txn_id = uuid4().hex
        for attempt in range(3):
            try:
                await self._send_event(room_id, content, txn_id)
                return
            except Exception as exc:
                error = exc
                if attempt < 2:
                    await asyncio.sleep(attempt + 1)
        raise ValueError(str(error) if isinstance(error, ValueError) else "Matrix не принял сообщение")

    async def send_link(self, sid, link, old_link=""):
        room_id, _user_id, display_name = await self._ensure_room(sid)
        with DB() as db:
            session = db.get(Session, sid)
            session_name = session.name if session else "звонок"
        intro = "Старая ссылка больше не действует. Создан новый звонок." if old_link else "Новая ссылка на звонок готова."
        text = f"{intro}\nСессия: {session_name}\n{link}\n\nQR-код прикреплён следующим сообщением."
        await self._send_with_retries(room_id, {"msgtype": "m.text", "body": text})
        image = qrcode.make(link)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        upload = await self._request("POST", "/_matrix/media/v3/upload", params={"filename": "call-qr.png"},
                                     content=buffer.getvalue(), content_type="image/png", timeout=30)
        content_uri = upload.json().get("content_uri")
        if not content_uri:
            raise ValueError("Matrix загрузил QR без content_uri")
        await self._send_with_retries(room_id, {"msgtype": "m.image", "body": f"QR · {session_name}",
                                                 "url": content_uri,
                                                 "info": {"mimetype": "image/png", "size": len(buffer.getvalue()),
                                                          "w": image.width, "h": image.height}})

    def _queue_notification(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.notification_tasks.add(task)
        task.add_done_callback(self.notification_tasks.discard)

    def queue_link_ready(self, sid, link, old_link=""):
        if self.enabled:
            self._queue_notification(self._notify_link_ready(sid, link, old_link))

    async def _notify_link_ready(self, sid, link, old_link):
        lock = self.notification_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            with DB() as db:
                session = db.get(Session, sid)
                if not session or not session.matrix_user_id or session.state != "running" or not session.link_secret:
                    return
                if decrypt(session.link_secret) != link:
                    return
            try:
                await self.send_link(sid, link, old_link)
                self.runner.log(sid, "Новая ссылка и QR отправлены Matrix пользователю", "info")
            except Exception as exc:
                self.runner.log(sid, f"Matrix не смог отправить новую ссылку и QR: {exc}", "error")

    def queue_manual_stop(self, sid):
        if not self.enabled:
            return
        with DB() as db:
            session = db.get(Session, sid)
            if not session or not session.matrix_user_id or session.state != "stopped":
                return
            name, user_id = session.name, session.matrix_user_id
        self._queue_notification(self._notify_manual_stop(sid, name, user_id))

    async def _notify_manual_stop(self, sid, name, user_id):
        lock = self.notification_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            with DB() as db:
                session = db.get(Session, sid)
                if not session or session.matrix_user_id != user_id:
                    return
            try:
                await self.send_notice(sid, f"Звонок «{name}» остановлен администратором. Старая ссылка больше не действует. Когда звонок запустят снова, я пришлю новую ссылку и QR-код.")
                self.runner.log(sid, "Matrix пользователь уведомлён об остановке звонка", "info")
            except Exception as exc:
                self.runner.log(sid, f"Matrix не смог сообщить об остановке звонка: {exc}", "error")

    async def _notify_interruption(self, sid, name, user_id):
        lock = self.notification_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            with DB() as db:
                session = db.get(Session, sid)
                if not session or session.matrix_user_id != user_id:
                    return
            try:
                await self.send_notice(sid, f"Звонок «{name}» прервался. Пытаюсь восстановить его; новую ссылку и QR-код пришлю после запуска.")
                self.runner.log(sid, "Matrix пользователь уведомлён о прерывании звонка", "info")
            except Exception as exc:
                self.runner.log(sid, f"Matrix не смог сообщить о прерывании звонка: {exc}", "error")

    async def start(self):
        if not self.enabled:
            return
        if self.sync_task and not self.sync_task.done():
            return
        self.stopping = False
        self.sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self):
        self.stopping = True
        task, self.sync_task = self.sync_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        recoveries = list(self.recovery_tasks.values())
        self.recovery_tasks.clear()
        for recovery in recoveries:
            if not recovery.done():
                recovery.cancel()
        if recoveries:
            await asyncio.gather(*recoveries, return_exceptions=True)
        notifications = list(self.notification_tasks)
        for notification in notifications:
            notification.cancel()
        if notifications:
            await asyncio.gather(*notifications, return_exceptions=True)
        self.notification_tasks.clear()

    async def _sync_loop(self):
        try:
            if not self.config.get("since"):
                initial = await self._request("GET", "/_matrix/client/v3/sync", params={"timeout": 0}, timeout=10)
                self.config["since"] = initial.json().get("next_batch", "")
                self._persist()
            while not self.stopping and self.config:
                response = await self._request("GET", "/_matrix/client/v3/sync",
                                               params={"timeout": 30000, "since": self.config.get("since", "")}, timeout=40)
                data = response.json()
                self.config["since"] = data.get("next_batch", self.config.get("since", ""))
                seen = set(self.config.get("seen_events", []))
                for room_id, joined in data.get("rooms", {}).get("join", {}).items():
                    for event in joined.get("timeline", {}).get("events", []):
                        event_id = event.get("event_id", "")
                        if not event_id or event_id in seen:
                            continue
                        seen.add(event_id)
                        await self._handle_event(room_id, event)
                self.config["seen_events"] = list(seen)[-300:]
                self._persist()
                self.sync_error = ""
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.stopping:
                self.sync_error = "Связь с Matrix потеряна. Проверяю повторно."
                await asyncio.sleep(5)
                if not self.stopping:
                    self.sync_task = None
                    await self.start()

    async def _handle_event(self, room_id, event):
        if event.get("type") != "m.room.message" or event.get("sender") == self.config.get("user_id"):
            return
        body = event.get("content", {}).get("body", "")
        if not isinstance(body, str) or not body.startswith("!"):
            return
        sender = event.get("sender", "")
        command, _, argument = body.strip().partition(" ")
        if command == "!help":
            await self._send_with_retries(room_id, {"msgtype": "m.text", "body":
                "Команды PRNL:\n!help — справка\n!refresh — обновить ссылку звонка (если к Matrix ID привязана одна сессия)\n!refresh ID — обновить ссылку выбранной сессии. ID виден в панели.\nКоманда меняет только ссылку звонка; VPN, имя и настройки сессии остаются без изменений."})
            return
        if command not in ("!refresh", "!link"):
            await self._send_with_retries(room_id, {"msgtype": "m.text", "body": "Неизвестная команда. Напишите !help."})
            return
        with DB() as db:
            linked = db.query(Session).filter(Session.matrix_user_id == sender).all()
            if argument:
                linked = [s for s in linked if s.id.lower().startswith(argument.lower())]
            session_ids = [s.id for s in linked]
            names = [f"{s.name} · {s.id[:8]}" for s in linked]
        if not linked:
            await self._send_with_retries(room_id, {"msgtype": "m.text", "body": "К этому Matrix аккаунту не привязана подходящая сессия. Попросите администратора добавить вас в настройках сессии."})
        elif len(linked) > 1:
            await self._send_with_retries(room_id, {"msgtype": "m.text", "body": "Найдено несколько сессий. Укажите ID: !refresh ID\n" + "\n".join(names)})
        else:
            await self._send_with_retries(room_id, {"msgtype": "m.text", "body": "Обновляю ссылку. Это может занять до минуты; пришлю новую ссылку и QR сюда."})
            await self.refresh_for_user(session_ids[0], sender, room_id)

    async def refresh_for_user(self, sid, user_id, reply_room=""):
        lock = self.command_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            with DB() as db:
                session = db.get(Session, sid)
                if not session or session.matrix_user_id != user_id:
                    raise ValueError("Matrix пользователь больше не привязан к сессии")
                old_link = decrypt(session.link_secret) if session.link_secret else ""
                name = session.name
            if sid in self.runner.jobs:
                await self.runner.stop(sid)
            try:
                await self.runner.start(sid, notify_on_link=False)
                new_link = await self._wait_for_link(sid, old_link, 90)
                if not new_link:
                    raise ValueError("Creator не создал новую ссылку за 90 секунд")
                await self.send_link(sid, new_link, old_link)
                self.runner.log(sid, "Новая ссылка и QR отправлены Matrix пользователю", "info")
            except Exception as exc:
                self.runner.log(sid, f"Matrix не смог обновить ссылку: {exc}", "error")
                if reply_room:
                    await self._send_with_retries(reply_room, {"msgtype": "m.text", "body":
                        f"Не удалось обновить звонок «{name}». Попробуйте позже или обратитесь к администратору."})
                raise

    async def _wait_for_link(self, sid, old_link, timeout):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if sid not in self.runner.jobs:
                return ""
            with DB() as db:
                session = db.get(Session, sid)
                if session and session.state == "running" and session.link_secret:
                    link = decrypt(session.link_secret)
                    if link and link != old_link:
                        return link
            await asyncio.sleep(.5)
        return ""

    async def handle_unexpected_exit(self, sid):
        if not self.enabled:
            return
        if sid in self.recovery_tasks:
            return
        task = asyncio.create_task(self._recover_session(sid))
        self.recovery_tasks[sid] = task
        try:
            await task
        finally:
            self.recovery_tasks.pop(sid, None)

    async def _recover_session(self, sid):
        with DB() as db:
            session = db.get(Session, sid)
            if not session or not session.matrix_user_id:
                return
            user_id = session.matrix_user_id
            old_link = decrypt(session.link_secret) if session.link_secret else ""
            name = session.name
        if self.enabled:
            self._queue_notification(self._notify_interruption(sid, name, user_id))
        for attempt in range(1, 4):
            await asyncio.sleep(attempt)
            with DB() as db:
                session = db.get(Session, sid)
                if not session or session.matrix_user_id != user_id:
                    return
            if sid in self.runner.jobs:
                return
            try:
                self.runner.log(sid, f"Matrix auto recovery: попытка {attempt}/3", "warning")
                await self.runner.start(sid, notify_on_link=False)
                link = await self._wait_for_link(sid, old_link, 90)
                if link:
                    with DB.begin() as db:
                        session = db.get(Session, sid)
                        if session:
                            session.matrix_retry_exhausted = False
                    try:
                        lock = self.notification_locks.setdefault(sid, asyncio.Lock())
                        async with lock:
                            await self.send_link(sid, link, old_link)
                        self.runner.log(sid, "Звонок восстановлен; новая ссылка и QR отправлены в Matrix", "info")
                    except Exception as exc:
                        self.runner.log(sid, f"Звонок восстановлен, но Matrix не смог отправить ссылку и QR: {exc}", "error")
                    return
            except Exception as exc:
                self.runner.log(sid, f"Matrix auto recovery attempt {attempt}/3 failed: {exc}", "error")
            if sid in self.runner.jobs:
                await self.runner.stop(sid)
        with DB.begin() as db:
            session = db.get(Session, sid)
            if session:
                session.matrix_retry_exhausted = True
                session.state = "error"
        self.runner.log(sid, "Автовосстановление Matrix исчерпало 3 попытки. Требуется вмешательство администратора.", "error")
        try:
            await self.send_notice(sid, f"Не удалось восстановить звонок «{name}» после 3 попыток. Обратитесь к администратору.")
        except Exception:
            pass

    async def send_notice(self, sid, text):
        room_id, _user_id, _display = await self._ensure_room(sid)
        await self._send_with_retries(room_id, {"msgtype": "m.text", "body": text})
