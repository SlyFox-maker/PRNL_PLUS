"""Manage the local Xray SOCKS egress used only by Creator relay traffic."""
import asyncio
import json
import os
import re
import socket
import ssl
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .security import decrypt, encrypt


SOCKS_HOST = "127.0.0.1"
SOCKS_PORT = 10808
VPN_SOCKS_PORT = 10810
XRAY_BIN = os.environ.get("XRAY_BIN", "/usr/local/bin/xray")
TEST_SOCKS_PORT = 10809
TEST_VPN_SOCKS_PORT = 10811


def parse_vless_uri(uri: str) -> dict:
    if not isinstance(uri, str) or len(uri) > 32768:
        raise ValueError("Ссылка VLESS пустая или слишком длинная")
    try:
        parsed = urlsplit(uri.strip())
        if parsed.scheme.lower() != "vless" or not parsed.hostname or not parsed.port:
            raise ValueError
        user_id = unquote(parsed.username or "")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except (ValueError, UnicodeError):
        raise ValueError("Проверьте формат ссылки vless://…@host:port") from None
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", user_id):
        raise ValueError("В ссылке указан некорректный UUID VLESS")
    query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=50)
    get = lambda key, default="": query.get(key, [default])[-1]
    if get("encryption", "none") != "none":
        raise ValueError("Поддерживается VLESS encryption=none")
    if get("type") != "xhttp" or get("security") != "reality":
        raise ValueError("Пока поддерживается формат VLESS XHTTP + REALITY")
    sni, public_key = get("sni"), get("pbk") or get("password")
    fingerprint = get("fp", "chrome")
    if not sni or not public_key or not fingerprint:
        raise ValueError("В ссылке не хватает SNI, pbk или fp для REALITY")
    mode = get("mode", "auto")
    if mode not in {"auto", "packet-up", "stream-up", "stream-one", "stream-down", "stream-up-old"}:
        raise ValueError("Неизвестный режим XHTTP")
    short_id = get("sid")
    if len(short_id) > 16 or (short_id and not re.fullmatch(r"[0-9a-fA-F]+", short_id)):
        raise ValueError("REALITY short ID должен быть hex-строкой длиной до 16 символов")
    return {
        "uri": uri.strip(), "host": host, "port": port, "id": user_id,
        "sni": sni, "public_key": public_key, "fingerprint": fingerprint,
        "short_id": short_id, "spider_x": get("spx", "/") or "/",
        "path": get("path", "/") or "/", "transport_host": get("host"),
        "mode": mode, "mldsa65_verify": get("pqv"),
        "name": unquote(parsed.fragment).strip()[:100],
    }


def _recv_exact(sock, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise OSError("Proxy connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _google_probe() -> int:
    """Make a minimal HTTPS request over SOCKS5 and return the HTTP status."""
    raw = socket.create_connection((SOCKS_HOST, TEST_VPN_SOCKS_PORT), timeout=5)
    raw.settimeout(6)
    try:
        raw.sendall(b"\x05\x01\x00")
        if _recv_exact(raw, 2) != b"\x05\x00":
            raise OSError("SOCKS authentication negotiation failed")
        host = b"www.google.com"
        raw.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + (443).to_bytes(2, "big"))
        reply = _recv_exact(raw, 4)
        if reply[0] != 5 or reply[1] != 0:
            raise OSError("SOCKS destination connection failed")
        address_length = 4 if reply[3] == 1 else 16 if reply[3] == 4 else _recv_exact(raw, 1)[0]
        _recv_exact(raw, address_length + 2)
        with ssl.create_default_context().wrap_socket(raw, server_hostname="www.google.com") as secure:
            secure.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: www.google.com\r\nConnection: close\r\nUser-Agent: prnl-vpn-check/1\r\n\r\n")
            response = bytearray()
            while b"\r\n" not in response and len(response) < 4096:
                part = secure.recv(512)
                if not part:
                    break
                response.extend(part)
        match = re.match(rb"HTTP/\d(?:\.\d)?\s+(\d{3})", response)
        if not match:
            raise OSError("Invalid HTTP response")
        return int(match.group(1))
    finally:
        raw.close()


class XrayManager:
    socks_host = SOCKS_HOST
    socks_port = SOCKS_PORT
    vpn_socks_port = VPN_SOCKS_PORT

    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.state_file = runtime / "xray-profile.enc"
        self.enabled = False
        self.profile = None
        self.process = None
        self.config_file = None
        self.lock = asyncio.Lock()
        self.last_error = ""

    def load(self):
        if not self.state_file.exists():
            return
        state = decrypt(self.state_file.read_text())
        self.profile = state.get("profile")
        self.enabled = bool(state.get("enabled", False))
        if self.profile:
            self.profile = parse_vless_uri(self.profile["uri"])

    def _persist(self):
        payload = {"enabled": self.enabled, "profile": {"uri": self.profile["uri"]} if self.profile else None}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(encrypt(payload))
        tmp.chmod(0o600)
        os.replace(tmp, self.state_file)

    def _config(self, enabled: bool, socks_port: int = SOCKS_PORT,
                vpn_socks_port: int = VPN_SOCKS_PORT) -> dict:
        p = self.profile
        reality = {
            "serverName": p["sni"], "fingerprint": p["fingerprint"],
            "password": p["public_key"], "shortId": p["short_id"],
            "spiderX": p["spider_x"],
        }
        if p["mldsa65_verify"]:
            reality["mldsa65Verify"] = p["mldsa65_verify"]
        xhttp = {"path": p["path"], "mode": p["mode"]}
        if p["transport_host"]:
            xhttp["host"] = p["transport_host"]
        outbounds = [{"tag": "vless-egress", "protocol": "vless",
                      "settings": {"vnext": [{"address": p["host"], "port": p["port"],
                                                "users": [{"id": p["id"], "encryption": "none"}]}]},
                      "streamSettings": {"network": "xhttp", "security": "reality",
                                         "realitySettings": reality, "xhttpSettings": xhttp}},
                     {"tag": "direct-egress", "protocol": "freedom", "settings": {}}]
        return {
            "log": {"loglevel": "warning"},
            "inbounds": [{"tag": "creator-relay", "listen": SOCKS_HOST, "port": socks_port,
                           "protocol": "socks", "settings": {"auth": "noauth", "udp": True, "userLevel": 0}},
                          {"tag": "creator-relay-vpn", "listen": SOCKS_HOST, "port": vpn_socks_port,
                           "protocol": "socks", "settings": {"auth": "noauth", "udp": True, "userLevel": 0}}],
            "outbounds": outbounds,
            "routing": {"domainStrategy": "AsIs", "rules": [
                {"type": "field", "inboundTag": ["creator-relay-vpn"], "outboundTag": "vless-egress"},
                {"type": "field", "inboundTag": ["creator-relay"],
                 "outboundTag": "vless-egress" if enabled else "direct-egress"}]},
        }

    async def _stop_process(self):
        proc, self.process = self.process, None
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if self.config_file:
            self.config_file.unlink(missing_ok=True)
            self.config_file = None

    async def _launch(self, enabled: bool, validate_vless: bool = True):
        if not self.profile:
            await self._stop_process()
            return
        # Validate the saved VLESS outbound even while direct routing is selected.
        config = self._config(True) if validate_vless or enabled else self._config(False)
        fd, filename = tempfile.mkstemp(prefix="wlp-xray-", suffix=".json")
        self.config_file = Path(filename)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(config, stream, separators=(",", ":"))
            check = await asyncio.create_subprocess_exec(XRAY_BIN, "run", "-test", "-config", filename,
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            if await asyncio.wait_for(check.wait(), 12) != 0:
                raise ValueError("Xray отклонил конфигурацию. Проверьте ссылку VLESS")
            with open(filename, "w") as stream:
                os.chmod(filename, 0o600)
                json.dump(self._config(enabled), stream, separators=(",", ":"))
            self.process = await asyncio.create_subprocess_exec(XRAY_BIN, "run", "-config", filename,
                                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            for _ in range(40):
                if self.process.returncode is not None:
                    raise ValueError("Xray не смог запустить локальный SOCKS-маршрут")
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(SOCKS_HOST, SOCKS_PORT), .2)
                    writer.close()
                    await writer.wait_closed()
                    return
                except (OSError, asyncio.TimeoutError):
                    await asyncio.sleep(.1)
            raise ValueError("Xray запущен, но локальный SOCKS-маршрут не отвечает")
        except Exception:
            await self._stop_process()
            raise

    async def save_profile(self, uri: str):
        profile = parse_vless_uri(uri)
        async with self.lock:
            previous = self.profile
            self.profile = profile
            try:
                await self._stop_process()
                await self._launch(self.enabled, validate_vless=True)
                self._persist()
                self.last_error = ""
            except Exception:
                self.profile = previous
                if previous:
                    await self._launch(self.enabled, validate_vless=bool(self.enabled))
                raise
        return self.status()

    async def set_enabled(self, enabled: bool):
        if enabled and not self.profile:
            raise ValueError("Сначала добавьте ссылку VLESS")
        async with self.lock:
            previous = self.enabled
            self.enabled = bool(enabled)
            try:
                if self.profile:
                    await self._stop_process()
                    await self._launch(self.enabled, validate_vless=bool(self.enabled))
                self._persist()
                self.last_error = ""
            except Exception:
                self.enabled = previous
                if self.profile:
                    await self._launch(previous, validate_vless=bool(previous))
                raise
        return self.status()

    async def remove_profile(self):
        async with self.lock:
            previous = self.profile
            self.profile = None
            self.enabled = False
            try:
                await self._stop_process()
                self.state_file.unlink(missing_ok=True)
            except Exception:
                self.profile = previous
                raise
        return self.status()

    async def start(self):
        async with self.lock:
            if self.profile:
                await self._launch(self.enabled, validate_vless=bool(self.enabled))
                self.last_error = ""

    async def shutdown(self):
        async with self.lock:
            await self._stop_process()

    async def test_connection(self):
        """Probe Google through an isolated temporary VLESS Xray instance."""
        async with self.lock:
            if not self.profile:
                raise ValueError("Сначала добавьте ссылку VLESS")
            fd, filename = tempfile.mkstemp(prefix="wlp-xray-check-", suffix=".json")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(self._config(True, TEST_SOCKS_PORT, TEST_VPN_SOCKS_PORT), stream, separators=(",", ":"))
            proc = None
            try:
                check = await asyncio.create_subprocess_exec(
                    XRAY_BIN, "run", "-test", "-config", filename,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                if await asyncio.wait_for(check.wait(), 12) != 0:
                    raise ValueError("Xray отклонил конфигурацию проверки")
                proc = await asyncio.create_subprocess_exec(
                    XRAY_BIN, "run", "-config", filename,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                for _ in range(60):
                    if proc.returncode is not None:
                        raise ValueError("Тестовый Xray не запустился")
                    try:
                        reader, writer = await asyncio.wait_for(asyncio.open_connection(SOCKS_HOST, TEST_VPN_SOCKS_PORT), .2)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except (OSError, asyncio.TimeoutError):
                        await asyncio.sleep(.1)
                else:
                    raise ValueError("Тестовый SOCKS-маршрут не отвечает")
                started = time.monotonic()
                try:
                    status = await asyncio.wait_for(asyncio.to_thread(_google_probe), 12)
                except Exception:
                    raise ValueError("Google не ответил через VPN. Проверьте узел и параметры VLESS/REALITY.") from None
                elapsed = round((time.monotonic() - started) * 1000)
                return {"ok": status == 204, "target": "www.google.com/generate_204",
                        "http_status": status, "latency_ms": elapsed}
            finally:
                if proc and proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                Path(filename).unlink(missing_ok=True)

    def has_route(self) -> bool:
        return bool(self.profile and self.process and self.process.returncode is None)

    def status(self, active_sessions=0):
        p = self.profile or {}
        return {"configured": bool(self.profile), "enabled": self.enabled,
                "running": bool(self.process and self.process.returncode is None),
                "host": p.get("host", ""), "port": p.get("port", 0),
                "transport": "xhttp + reality" if self.profile else "",
                "name": p.get("name", ""), "active_sessions": active_sessions,
                "error": self.last_error}
