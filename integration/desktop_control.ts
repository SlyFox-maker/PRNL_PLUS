import { createServer, IncomingMessage, ServerResponse } from 'http';
import { timingSafeEqual } from 'crypto';
import { BrowserWindow, session, webContents } from 'electron';
import type { TabManager } from './tab-manager';

const COOKIE_DOMAINS: Record<string, string[]> = {
  vk: ['vk.ru', 'vk.com', 'vkid.ru'], telemost: ['yandex.ru', 'yandex.net', 'ya.ru'],
  wbstream: ['wb.ru', 'wildberries.ru'], dion: ['dion.vc'],
  bitrix: ['bitrix24.ru', 'bitrix24.net', 'bitrix24.com', 'bitrix24.eu', 'bitrix24.de'],
};
const KEYS = new Set(['Tab', 'Enter', 'Backspace', 'Escape', 'Control+A', 'Shift+Tab', 'ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Space']);

async function activeWebview(win: BrowserWindow) {
  const view = await win.webContents.executeJavaScript(`(() => {
    const view = Array.from(document.querySelectorAll('webview')).find((item) => {
      const rect = item.getBoundingClientRect();
      const style = getComputedStyle(item);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    });
    if (!view) return null;
    const rect = view.getBoundingClientRect();
    return { id: view.getWebContentsId(), left: rect.left, top: rect.top, width: rect.width, height: rect.height };
  })()`);
  const target = Number.isInteger(view?.id) ? webContents.fromId(view.id) : undefined;
  return { contents: target || win.webContents, rect: target ? view : undefined };
}

function sendJson(res: ServerResponse, status: number, value: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
}

async function readBody(req: IncomingMessage): Promise<any> {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk.toString();
    if (raw.length > 5000) throw new Error('request too large');
  }
  return JSON.parse(raw || '{}');
}

export function startDesktopControl(win: BrowserWindow, tabManager: TabManager, port: number, token: string): void {
  let windowLoaded = !win.webContents.isLoadingMainFrame();
  win.webContents.once('did-finish-load', () => { windowLoaded = true; });
  const server = createServer(async (req, res) => {
    const auth = Buffer.from(req.headers.authorization || '');
    const expected = Buffer.from(`Bearer ${token}`);
    if (auth.length !== expected.length || !timingSafeEqual(auth, expected)) {
      sendJson(res, 401, { detail: 'unauthorized' }); return;
    }
    if (req.method === 'GET' && req.url === '/health') {
      if (win.isDestroyed() || !windowLoaded) { sendJson(res, 503, { ok: false }); return; }
      try {
        const hasTab = await win.webContents.executeJavaScript('!!document.querySelector("webview")');
        sendJson(res, hasTab ? 200 : 503, { ok: hasTab });
      } catch { sendJson(res, 503, { ok: false }); }
      return;
    }
    if (req.method === 'GET' && req.url === '/frame') {
      if (win.isDestroyed()) { sendJson(res, 503, { detail: 'window closed' }); return; }
      try {
        const image = await win.capturePage();
        res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store' });
        res.end(image.toJPEG(65));
      } catch { sendJson(res, 503, { detail: 'screen unavailable' }); }
      return;
    }
    if (req.method === 'GET' && req.url === '/cookies.zip') {
      try {
        const archive = await tabManager.buildCookiesZip();
        res.writeHead(200, {
          'Content-Type': 'application/zip',
          'Content-Disposition': 'attachment; filename="cookies.zip"',
          'Cache-Control': 'no-store, private',
        });
        res.end(archive);
      } catch { sendJson(res, 503, { detail: 'cookie export unavailable' }); }
      return;
    }
    if (req.method === 'GET' && req.url?.startsWith('/cookies?platform=')) {
      const platform = decodeURIComponent(req.url.split('=', 2)[1] || '');
      const domains = COOKIE_DOMAINS[platform];
      if (!domains) { sendJson(res, 400, { detail: 'unknown platform' }); return; }
      try {
        const cookies = await session.fromPartition('persist:creator').cookies.get({});
        const selected = cookies.filter(cookie => cookie.domain && domains.some(domain => {
          const host = cookie.domain!.replace(/^\./, '').toLowerCase();
          return host === domain || host.endsWith(`.${domain}`);
        })).map(cookie => ({
          name: cookie.name, value: cookie.value, domain: cookie.domain, path: cookie.path || '/',
          expires: cookie.expirationDate, httpOnly: cookie.httpOnly, secure: cookie.secure,
          sameSite: cookie.sameSite === 'no_restriction' ? 'None' : cookie.sameSite === 'lax' ? 'Lax' : cookie.sameSite === 'strict' ? 'Strict' : undefined,
        }));
        sendJson(res, 200, selected);
      } catch { sendJson(res, 503, { detail: 'cookies unavailable' }); }
      return;
    }
    if (req.method === 'POST' && req.url === '/action') {
      if (win.isDestroyed()) { sendJson(res, 503, { detail: 'window closed' }); return; }
      try {
        const action = await readBody(req);
        const contents = win.webContents;
        if (action.action === 'click' && Number.isFinite(action.x) && Number.isFinite(action.y) && action.x >= 0 && action.x <= 1200 && action.y >= 0 && action.y <= 800) {
          const view = await activeWebview(win);
          const inView = view.rect && action.x >= view.rect.left && action.x <= view.rect.left + view.rect.width
            && action.y >= view.rect.top && action.y <= view.rect.top + view.rect.height;
          const target = inView ? view.contents : contents;
          const x = inView ? action.x - view.rect.left : action.x;
          const y = inView ? action.y - view.rect.top : action.y;
          target.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
          target.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
        } else if (action.action === 'type' && typeof action.text === 'string' && action.text.length <= 4000) {
          const view = await activeWebview(win);
          view.contents.insertText(action.text);
        } else if (action.action === 'key' && typeof action.key === 'string' && KEYS.has(action.key)) {
          const view = await activeWebview(win);
          const control = action.key === 'Control+A';
          const keyCode = control ? 'A' : action.key === 'Space' ? ' ' : action.key.replace(/^Shift\+/, '');
          const modifiers: ('control' | 'shift')[] = control ? ['control'] : action.key.startsWith('Shift+') ? ['shift'] : [];
          view.contents.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
          view.contents.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
        } else if (action.action === 'scroll' && Number.isFinite(action.delta) && Math.abs(action.delta) <= 2000) {
          const view = await activeWebview(win);
          const x = view.rect ? Math.min(600, view.rect.width - 1) : 600;
          const y = view.rect ? Math.min(400, view.rect.height - 1) : 400;
          view.contents.sendInputEvent({ type: 'mouseWheel', x, y, deltaX: 0, deltaY: action.delta });
        } else { sendJson(res, 400, { detail: 'invalid action' }); return; }
        sendJson(res, 200, { ok: true });
      } catch { sendJson(res, 400, { detail: 'invalid request' }); }
      return;
    }
    sendJson(res, 404, { detail: 'not found' });
  });
  server.listen(port, '127.0.0.1');
}
