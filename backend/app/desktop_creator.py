import asyncio
import os
import secrets
import signal
import shutil
import socket
from pathlib import Path

import httpx


class DesktopCreators:
    """Manage the pinned upstream Electron Creator and its private local bridge."""

    def __init__(self, runtime: Path, browsers):
        self.runtime = runtime
        self.browsers = browsers
        self.items = {}
        self.lock = asyncio.Lock()

    async def start(self, account_id: str):
        async with self.lock:
            current = self.items.get(account_id)
            if current and current['proc'].returncode is None:
                return
            if any(item['proc'].returncode is None for item in self.items.values()):
                raise ValueError('Остановите уже открытый Desktop Creator перед запуском другого аккаунта')
            app_dir = Path(os.environ.get('DESKTOP_CREATOR_APP_DIR', '/opt/desktop-creator/creator-app'))
            electron = Path(os.environ.get('DESKTOP_CREATOR_BIN', '/opt/desktop-creator/creator-app/node_modules/electron/dist/electron'))
            if not app_dir.is_dir() or not electron.exists():
                raise ValueError('Desktop Creator не собран в Docker-образе')
            await self.browsers.ensure_display()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            token = secrets.token_urlsafe(32)
            profile_root = self.runtime / 'desktop-creator'
            profile_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            user_data = profile_root / account_id
            user_data.mkdir(mode=0o700, parents=True, exist_ok=True)
            env = os.environ.copy()
            env.update({
                'WLP_CREATOR_USER_DATA_DIR': str(user_data),
                'WLP_CREATOR_CONTROL_PORT': str(port),
                'WLP_CREATOR_CONTROL_TOKEN': token,
                'WLP_DESKTOP_CREATOR_TELEMOST': '1',
            })
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(electron), '--no-sandbox', str(app_dir), cwd=str(app_dir), env=env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                )
            except OSError as exc:
                raise ValueError('Не удалось запустить Desktop Creator') from exc
            item = {'proc': proc, 'port': port, 'token': token, 'user_data': user_data}
            self.items[account_id] = item
            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    for _ in range(60):
                        if proc.returncode is not None:
                            raise ValueError('Desktop Creator завершился во время запуска')
                        try:
                            response = await client.get(f'http://127.0.0.1:{port}/health', headers={'Authorization': f'Bearer {token}'})
                            if response.status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.25)
            except Exception:
                await self._stop(item)
                self.items.pop(account_id, None)
                raise
            await self._stop(item)
            self.items.pop(account_id, None)
            raise ValueError('Desktop Creator не ответил. Проверьте запуск Chromium в Docker.')

    def get(self, account_id: str):
        item = self.items.get(account_id)
        if not item or item['proc'].returncode is not None:
            raise ValueError('Desktop Creator закрыт. Запустите его заново.')
        return item

    async def request(self, account_id: str, method: str, path: str, body=None):
        item = self.get(account_id)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(
                    method, f"http://127.0.0.1:{item['port']}{path}",
                    headers={'Authorization': f"Bearer {item['token']}"}, json=body,
                )
            if response.status_code >= 400:
                raise ValueError('Desktop Creator не смог выполнить запрос')
            return response
        except httpx.HTTPError as exc:
            raise ValueError('Desktop Creator временно недоступен') from exc

    async def stop(self, account_id: str):
        async with self.lock:
            item = self.items.pop(account_id, None)
            if item:
                await self._stop(item)

    async def remove(self, account_id: str):
        await self.stop(account_id)
        shutil.rmtree(self.runtime / 'desktop-creator' / account_id, ignore_errors=True)

    async def _stop(self, item):
        proc = item['proc']
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=8)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()

    async def shutdown(self):
        async with self.lock:
            items, self.items = list(self.items.values()), {}
            await asyncio.gather(*(self._stop(item) for item in items))
