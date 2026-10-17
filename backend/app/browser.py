import asyncio
import ipaddress
import os
import shutil
import socket
import time
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from .security import DESKTOP_USER_AGENT, PLATFORMS


async def public_url(url):
    u = urlparse(url)
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password:
        return False
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, u.hostname, u.port or 443)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (OSError, ValueError):
        return False


class Browsers:
    def __init__(self):
        self.items = {}
        self.lock = asyncio.Lock()
        self.pw = None
        self.browser = None
        self.display = None

    async def start(self, account, initial_cookies=()):
        async with self.lock:
            if account.id in self.items:
                return
            if len(self.items) >= 3:
                raise ValueError('Одновременно можно открыть не больше трёх окон входа')
            if not self.pw:
                await self.ensure_display()
                self.pw = await async_playwright().start()
                self.browser = await self.pw.chromium.launch(headless=False, args=[
                    '--disable-dev-shm-usage', '--use-fake-device-for-media-stream',
                    '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required',
                ])
            context = await self.browser.new_context(
                viewport={'width': 1100, 'height': 760}, accept_downloads=False,
                service_workers='block',
                user_agent=DESKTOP_USER_AGENT,
            )
            if initial_cookies:
                accepted = [c for c in initial_cookies if isinstance(c, dict) and c.get('name') and c.get('value') and (c.get('domain') or c.get('url'))]
                if accepted:
                    await context.add_cookies(accepted)
            capture = {}
            # Block browser access to server-local resources, including redirects.
            async def route(request_route):
                url = request_route.request.url
                if account.platform == 'wbstream' and urlparse(url).hostname == 'stream.wb.ru':
                    authorization = await request_route.request.header_value('authorization')
                    if authorization and authorization.startswith('Bearer '):
                        capture['wb_access_token'] = authorization[7:]
                if url.startswith(('data:', 'blob:')) or await public_url(url):
                    await request_route.continue_()
                else:
                    await request_route.abort()
            await context.route('**/*', route)
            page = await context.new_page()
            self.items[account.id] = {'context': context, 'page': page, 'at': time.monotonic(), 'lock': asyncio.Lock(), 'capture': capture}
            url = account.portal if account.platform == 'bitrix' else PLATFORMS[account.platform]['url']
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=45000)
            except Exception:
                await self.close(account.id)
                raise ValueError('Не удалось открыть страницу входа. Проверьте доступность платформы.')

    async def ensure_display(self):
        if os.environ.get('DISPLAY'):
            return
        if not shutil.which('Xvfb'):
            raise ValueError('Для desktop-режима Chromium в контейнере нужен Xvfb')
        display = ':99'
        self.display = await asyncio.create_subprocess_exec(
            'Xvfb', display, '-screen', '0', '1280x900x24', '-nolisten', 'tcp',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.sleep(0.25)
        if self.display.returncode is not None:
            self.display = None
            raise ValueError('Не удалось запустить виртуальный экран для браузера')
        os.environ['DISPLAY'] = display

    async def get(self, account_id):
        item = self.items.get(account_id)
        if not item:
            raise ValueError('Окно входа закрыто. Откройте его заново.')
        item['at'] = time.monotonic()
        # OAuth may open a new tab; always display/control the most recent one.
        pages = item['context'].pages
        if pages:
            item['page'] = pages[-1]
        return item

    async def close(self, account_id):
        item = self.items.pop(account_id, None)
        if item:
            await item['context'].close()

    async def reap(self):
        for key, item in list(self.items.items()):
            if time.monotonic() - item['at'] > 900:
                await self.close(key)

    async def shutdown(self):
        for key in list(self.items):
            await self.close(key)
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
        if self.display:
            self.display.terminate()
            await self.display.wait()
            self.display = None
