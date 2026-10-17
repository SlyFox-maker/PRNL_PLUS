import asyncio
import contextlib
import hashlib
import hmac
import base64
import binascii
import io
import json
import os
from pathlib import Path
import re
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from playwright.async_api import Error as PlaywrightError
from itsdangerous import URLSafeTimedSerializer, BadSignature
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select, update, inspect, delete, func, text
from .db import Base, engine, DB, Account, Session, Log, Destination, AppSetting, now
from .security import DESKTOP_USER_AGENT, PLATFORMS, encrypt, decrypt, validate_cookies, cipher
from .browser import Browsers
from .runner import Runner
from .desktop_creator import DesktopCreators
from .vpn import XrayManager
from .matrix import MatrixIntegration
from . import panel_tls

browsers = Browsers()
desktop_creators = None
runner = None
vpn_manager = None
matrix_manager = None
attempts = {}
admin_username = None
admin_password_hash = None
MATRIX_DEFAULT_GREETING = ('Привет! Я бот PRNL. Буду присылать сюда актуальную ссылку и QR-код звонка для сессии «{session}».\n\n'
                          'Для стабильного подключения выставь в настройках приложения DNS: 8.8.8.8 и 1.1.1.1.\n\n'
                          'Напишите !help для списка команд или !refresh, чтобы обновить ссылку.')
MATRIX_OLD_DEFAULT_GREETING = ('Привет! Я бот PRNL. Буду присылать сюда актуальную ссылку и QR-код звонка для сессии «{session}».\n\n'
                               'Напишите !help для списка команд или !refresh, чтобы обновить ссылку.')


@asynccontextmanager
async def lifespan(app):
    global runner, desktop_creators, vpn_manager, matrix_manager, admin_username, admin_password_hash
    if len(os.environ.get('ADMIN_PASSWORD', '')) < 16:
        raise RuntimeError('ADMIN_PASSWORD must contain at least 16 characters')
    cipher()  # Fail before serving if the encryption key is missing/invalid.
    Base.metadata.create_all(engine)
    # create_all does not add columns to an existing sessions table.
    with engine.begin() as conn:
        columns = {column['name'] for column in inspect(conn).get_columns('sessions')}
        migrations = {
            'rx_total': 'BIGINT NOT NULL DEFAULT 0', 'tx_total': 'BIGINT NOT NULL DEFAULT 0',
            'recipient_type': "VARCHAR(20) NOT NULL DEFAULT ''",
            'recipient_label': "VARCHAR(100) NOT NULL DEFAULT ''",
            'recipient_value': "VARCHAR(500) NOT NULL DEFAULT ''",
            'vpn_enabled': "BOOLEAN NOT NULL DEFAULT FALSE",
            'matrix_user_id': "VARCHAR(255) NOT NULL DEFAULT ''",
            'matrix_display_name': "VARCHAR(255) NOT NULL DEFAULT ''",
            'matrix_has_avatar': "BOOLEAN NOT NULL DEFAULT FALSE",
            'matrix_room_id': "VARCHAR(255) NOT NULL DEFAULT ''",
            'matrix_greeting_sent': "BOOLEAN NOT NULL DEFAULT FALSE",
            'matrix_retry_exhausted': "BOOLEAN NOT NULL DEFAULT FALSE",
        }
        for column, definition in migrations.items():
            if column not in columns:
                conn.exec_driver_sql(f'ALTER TABLE sessions ADD COLUMN {column} {definition}')
        conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_logs_at ON logs (at)')
        destination_columns = {column['name'] for column in inspect(conn).get_columns('destinations')}
        if 'connection_id' not in destination_columns:
            conn.exec_driver_sql('ALTER TABLE destinations ADD COLUMN connection_id INTEGER')
        conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_destinations_session_connection_id ON destinations (session_id, connection_id)')
    with DB.begin() as db:
        defaults = {'log_retention_days': '30', 'session_logs_enabled': 'true', 'domain_history_enabled': 'true',
                    'admin_username': os.environ.get('ADMIN_USERNAME', 'admin'),
                    'matrix_greeting': MATRIX_DEFAULT_GREETING}
        for key, value in defaults.items():
            setting = db.get(AppSetting, key)
            if setting is None:
                db.add(AppSetting(key=key, value=value))
            elif key == 'matrix_greeting' and setting.value == MATRIX_OLD_DEFAULT_GREETING:
                setting.value = value
        admin_username = db.get(AppSetting, 'admin_username').value
        stored_hash = db.get(AppSetting, 'admin_password_hash')
        admin_password_hash = stored_hash.value if stored_hash else None
    await asyncio.to_thread(purge_expired_records, 25)
    with DB() as db:
        recover_after_boot = [sid for sid, in db.query(Session.id).filter(
            Session.matrix_user_id != '', Session.state.in_(['starting', 'running', 'interrupted'])
        ).all()]
    with DB.begin() as db:
        db.execute(update(Session).where(Session.state.in_(['starting', 'running'])).values(state='interrupted'))
    runtime = Path(os.environ.get('RUNTIME_DIR', '/tmp/wlp-runtime'))
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    vpn_manager = XrayManager(runtime)
    vpn_manager.load()
    xray_bootstrap = runtime / 'xray-env-bootstrap.done'
    if not vpn_manager.profile and os.environ.get('XRAY_VLESS_URI') and not xray_bootstrap.exists():
        vpn_manager.enabled = os.environ.get('XRAY_ENABLED', 'false').lower() == 'true'
        try:
            await vpn_manager.save_profile(os.environ['XRAY_VLESS_URI'])
            xray_bootstrap.touch(mode=0o600)
        except Exception:
            vpn_manager.last_error = 'Не удалось применить XRAY_VLESS_URI из .env. Проверьте VLESS-конфигурацию в настройках.'
    try:
        await vpn_manager.start()
    except Exception:
        vpn_manager.last_error = 'Xray не смог запустить сохранённый маршрут. Проверьте конфигурацию в настройках.'
    runner = Runner(vpn_manager)
    matrix_manager = MatrixIntegration(runtime, runner)
    matrix_manager.load()
    runner.on_unexpected_exit = matrix_manager.handle_unexpected_exit
    matrix_bootstrap = runtime / 'matrix-env-bootstrap.done'
    matrix_env = (os.environ.get('MATRIX_HOMESERVER', '').strip(),
                  os.environ.get('MATRIX_USERNAME', '').strip(), os.environ.get('MATRIX_PASSWORD', ''))
    if not matrix_manager.config and all(matrix_env) and not matrix_bootstrap.exists():
        try:
            await matrix_manager.login(*matrix_env)
            matrix_bootstrap.touch(mode=0o600)
        except Exception:
            matrix_manager.sync_error = 'Не удалось подключить Matrix из .env. Проверьте homeserver и данные входа в настройках.'
    await matrix_manager.start()
    for sid in recover_after_boot:
        asyncio.create_task(matrix_manager.handle_unexpected_exit(sid))
    with DB() as db:
        runner.session_logs_enabled = setting_enabled(db, 'session_logs_enabled', True)
        runner.domain_history_enabled = setting_enabled(db, 'domain_history_enabled', True)
    desktop_creators = DesktopCreators(runner.root, browsers)
    # Runtime files are plaintext only while a process is alive. Recover rotated credentials after an unclean restart.
    for directory in runner.root.iterdir():
        if directory.is_dir() and directory.name != 'desktop-creator':
            with DB() as db:
                session = db.get(Session, directory.name)
                if session:
                    job = {'directory': directory, 'account_id': session.account_id, 'cookie_text': '', 'secrets': []}
                    runner.sync_cookies(job)
            import shutil
            shutil.rmtree(directory)
    async def maintain():
        last_cleanup = time.monotonic()
        while True:
            try:
                await runner.tick()
                await browsers.reap()
                if time.monotonic() - last_cleanup >= 3600:
                    await asyncio.to_thread(purge_expired_records, 25)
                    last_cleanup = time.monotonic()
            except Exception:
                # Do not dump credential-bearing exceptions to container logs.
                import logging
                logging.getLogger('wlp').error('Maintenance tick failed; retrying')
            await asyncio.sleep(1)
    task = asyncio.create_task(maintain())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await desktop_creators.shutdown()
    await matrix_manager.stop()
    await runner.shutdown()
    await vpn_manager.shutdown()
    await browsers.shutdown()


app = FastAPI(title='WLP Control', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def signer():
    return URLSafeTimedSerializer(os.environ['ENCRYPTION_KEY'], salt='wlp-admin')


def authenticated(request):
    try:
        data = signer().loads(request.cookies.get('wlp_session', ''), max_age=43200)
        return hmac.compare_digest(data, admin_session_token())
    except (BadSignature, TypeError):
        return False


@app.middleware('http')
async def access(request, call_next):
    if request.url.path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = os.environ.get('PUBLIC_ORIGIN', f'{request.url.scheme}://{request.headers.get("host", "")}')
            panel_url = panel_tls.origin()
            if origin and origin not in {expected, panel_url, panel_tls.local_origin(), panel_tls.legacy_local_origin()}:
                return JSONResponse({'detail': 'Недопустимый источник запроса'}, 403)
            if 'application/json' not in request.headers.get('content-type', ''):
                return JSONResponse({'detail': 'Требуется application/json'}, 415)
            try:
                size = int(request.headers.get('content-length', 0))
            except ValueError:
                return JSONResponse({'detail': 'Некорректная длина запроса'}, 400)
            if size > 1_000_000:
                return JSONResponse({'detail': 'Запрос слишком большой'}, 413)
        if request.url.path not in ('/api/login', '/api/health') and not authenticated(request):
            return JSONResponse({'detail': 'Войдите в панель'}, 401)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, 400)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default error response can echo passwords/cookies in input.
    return JSONResponse({'detail': 'Проверьте формат и обязательные поля запроса'}, 422)


class Login(BaseModel):
    username: str = Field(default='admin', min_length=1, max_length=100)
    password: str = Field(max_length=1000)


class AdminCredentials(BaseModel):
    current_password: str = Field(min_length=1, max_length=1000)
    username: str = Field(min_length=1, max_length=100)
    new_password: str = Field(default='', max_length=1000)


def admin_session_token():
    if admin_password_hash:
        payload = f'wlp-admin:{admin_username}:{admin_password_hash}'.encode()
        return hmac.digest(os.environ['ENCRYPTION_KEY'].encode(), payload, 'sha256').hex()
    return hmac.digest(os.environ['ADMIN_PASSWORD'].encode(), b'admin', 'sha256').hex()


def verify_admin_password(password):
    if admin_password_hash:
        try:
            scheme, salt, expected = admin_password_hash.split('$', 2)
            if scheme != 'scrypt':
                return False
            derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
            return hmac.compare_digest(derived.hex(), expected)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(password.encode(), os.environ['ADMIN_PASSWORD'].encode())


def hash_admin_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f'scrypt${salt.hex()}${digest.hex()}'


RETENTION_OPTIONS = {7, 14, 30, 60, 90, 180, 365}


def retention_days(db=None):
    if db is not None:
        setting = db.get(AppSetting, 'log_retention_days')
        return int(setting.value) if setting else 30
    with DB() as session:
        setting = session.get(AppSetting, 'log_retention_days')
        return int(setting.value) if setting else 30


def purge_expired_records(max_batches=25):
    cutoff = now() - timedelta(days=retention_days())
    deleted = {'logs': 0, 'destinations': 0, 'has_more': False}
    for model, key in ((Log, 'logs'), (Destination, 'destinations')):
        for _ in range(max_batches):
            with DB.begin() as db:
                ids = select(model.id).where(model.at < cutoff).order_by(model.at).limit(2000)
                result = db.execute(delete(model).where(model.id.in_(ids)))
                count = result.rowcount or 0
            deleted[key] += count
            if count < 2000:
                break
        else:
            deleted['has_more'] = True
    return deleted


@app.post('/api/login')
async def login(data: Login, request: Request, response: Response):
    forwarded_for = request.headers.get('x-forwarded-for', '')
    key = forwarded_for.split(',', 1)[0].strip() or (request.client.host if request.client else 'unknown')
    now_monotonic = time.monotonic()
    if len(attempts) > 10_000:
        for ip, times in list(attempts.items()):
            recent = [attempt for attempt in times if now_monotonic - attempt < 60]
            if recent:
                attempts[ip] = recent
            else:
                attempts.pop(ip, None)
    previous = attempts.get(key, [])
    previous = [t for t in previous if now_monotonic - t < 60]
    attempts[key] = previous
    if len(previous) >= 8:
        raise HTTPException(429, 'Слишком много попыток. Повторите через минуту.')
    if (not hmac.compare_digest(data.username.strip(), admin_username or os.environ.get('ADMIN_USERNAME', 'admin'))
            or not verify_admin_password(data.password)):
        previous.append(time.monotonic())
        raise HTTPException(401, 'Неверный логин или пароль')
    attempts.pop(key, None)
    token = admin_session_token()
    response.set_cookie('wlp_session', signer().dumps(token), httponly=True, samesite='strict', max_age=43200,
                        secure=os.environ.get('COOKIE_SECURE') == 'true' or request.headers.get('x-forwarded-proto') == 'https')
    return {'ok': True}


@app.get('/api/settings/admin')
def get_admin_settings():
    return {'username': admin_username or os.environ.get('ADMIN_USERNAME', 'admin'), 'password_configured': True,
            'initial_setup_required': not bool(admin_password_hash)}


@app.put('/api/settings/admin')
def update_admin_settings(data: AdminCredentials, request: Request, response: Response):
    global admin_username, admin_password_hash
    username = data.username.strip()
    if len(username) < 2 or any(char.isspace() for char in username):
        raise HTTPException(400, 'Логин должен содержать не менее 2 символов и не содержать пробелы')
    if not verify_admin_password(data.current_password):
        raise HTTPException(401, 'Текущий пароль указан неверно')
    if not admin_password_hash and not data.new_password:
        raise HTTPException(400, 'При первой настройке задайте новый пароль панели')
    if data.new_password and len(data.new_password) < 16:
        raise HTTPException(400, 'Новый пароль должен содержать не менее 16 символов')
    new_hash = hash_admin_password(data.new_password or data.current_password)
    with DB.begin() as db:
        for key, value in (('admin_username', username), ('admin_password_hash', new_hash)):
            setting = db.get(AppSetting, key)
            if setting:
                setting.value = value
            else:
                db.add(AppSetting(key=key, value=value))
    admin_username, admin_password_hash = username, new_hash
    response.set_cookie('wlp_session', signer().dumps(admin_session_token()), httponly=True, samesite='strict', max_age=43200,
                        secure=os.environ.get('COOKIE_SECURE') == 'true' or request.headers.get('x-forwarded-proto') == 'https')
    return {'username': username, 'password_configured': True, 'initial_setup_required': False}


@app.post('/api/logout')
async def logout(response: Response):
    response.delete_cookie('wlp_session')
    return {'ok': True}


@app.get('/api/health')
def health():
    return {'ok': True}


class StorageSettings(BaseModel):
    retention_days: int | None = None
    session_logs_enabled: bool | None = None
    domain_history_enabled: bool | None = None


class VlessConfigInput(BaseModel):
    uri: str = Field(min_length=1, max_length=32768)


class VpnEnabledInput(BaseModel):
    enabled: bool


class MatrixLoginInput(BaseModel):
    homeserver: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1000)


class MatrixGreetingInput(BaseModel):
    greeting: str = Field(max_length=500)


def vpn_status():
    return vpn_manager.status(len(runner.jobs) if runner else 0)


@app.get('/api/settings/vpn')
def get_vpn_settings():
    return vpn_status()


@app.post('/api/settings/vpn/test')
async def test_vpn_connection():
    return await vpn_manager.test_connection()


@app.put('/api/settings/vpn/config')
async def put_vpn_config(data: VlessConfigInput):
    try:
        await vpn_manager.save_profile(data.uri)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Не удалось запустить Xray. Проверьте ссылку и доступность сервера.') from None
    return vpn_status()


@app.put('/api/settings/vpn/enabled')
async def put_vpn_enabled(data: VpnEnabledInput):
    try:
        await vpn_manager.set_enabled(data.enabled)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Не удалось переключить маршрут Xray') from None
    return vpn_status()


@app.delete('/api/settings/vpn/config')
async def delete_vpn_config():
    if runner.jobs:
        raise HTTPException(409, 'Сначала остановите активные сессии Creator')
    await vpn_manager.remove_profile()
    return vpn_status()


@app.get('/api/settings/matrix')
def get_matrix_settings():
    return {**matrix_manager.status(), 'greeting': matrix_manager.get_greeting()}


@app.post('/api/settings/matrix/login')
async def matrix_login(data: MatrixLoginInput):
    try:
        return await matrix_manager.login(data.homeserver, data.username, data.password)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Не удалось подключить Matrix аккаунт') from None


@app.put('/api/settings/matrix/greeting')
def update_matrix_greeting(data: MatrixGreetingInput):
    return {'greeting': matrix_manager.set_greeting(data.greeting)}


@app.delete('/api/settings/matrix')
async def matrix_logout():
    return await matrix_manager.logout()


@app.get('/api/settings/matrix/users')
async def matrix_search_users(q: str = ''):
    return {'results': await matrix_manager.search_users(q)}


@app.get('/api/settings/matrix/avatar')
async def matrix_user_avatar(user_id: str):
    content, mime = await matrix_manager.avatar(user_id)
    return Response(content=content, media_type=mime, headers={'Cache-Control': 'private, max-age=300'})


@app.get('/api/sessions/{sid}/matrix-avatar')
async def matrix_session_avatar(sid: str):
    content, mime = await matrix_manager.session_avatar(sid)
    return Response(content=content, media_type=mime, headers={'Cache-Control': 'private, max-age=3600'})


class PanelTlsInput(BaseModel):
    ip: str = Field(min_length=1, max_length=64)


@app.get('/api/settings/panel')
def get_panel_settings():
    return panel_tls.status()


@app.put('/api/settings/panel')
def update_panel_settings(data: PanelTlsInput):
    return panel_tls.set_ip(data.ip)


@app.get('/api/settings/panel/ca')
def get_panel_ca_certificate():
    try:
        certificate = panel_tls.download_ca_certificate()
    except OSError:
        raise HTTPException(503, 'Локальный центр подписи ещё не создан') from None
    return Response(certificate, media_type='application/x-pem-file', headers={
        'Content-Disposition': 'attachment; filename="prnl-panel-ca.crt"',
        'Cache-Control': 'no-store',
    })


@app.post('/api/settings/panel/certificate')
def renew_panel_certificate():
    try:
        panel_tls.request_certificate_renewal()
    except OSError:
        raise HTTPException(503, 'Не удалось отправить запрос на перевыпуск сертификата') from None
    return {'requested': True}


@app.post('/api/settings/panel/path')
def rotate_panel_access_path():
    panel_tls.rotate_access_path()
    return panel_tls.status()


def setting_enabled(db, key, default):
    setting = db.get(AppSetting, key)
    return setting.value.lower() == 'true' if setting else default


def storage_report(db):
    days = retention_days(db)
    session_logs_enabled = setting_enabled(db, 'session_logs_enabled', True)
    domain_history_enabled = setting_enabled(db, 'domain_history_enabled', True)
    if engine.dialect.name == 'postgresql':
        logs = db.execute(text("SELECT count(*), coalesce(sum(pg_column_size(l)), 0) FROM logs l")).one()
        destinations = db.execute(text("SELECT count(*), coalesce(sum(pg_column_size(d)), 0) FROM destinations d")).one()
        sizes = db.execute(text("SELECT pg_total_relation_size('logs'), pg_total_relation_size('destinations'), pg_database_size(current_database())")).one()
        return {'retention_days': days, 'session_logs_enabled': session_logs_enabled,
                'domain_history_enabled': domain_history_enabled,
                'logs': {'records': logs[0], 'live_bytes': logs[1], 'bytes': sizes[0]},
                'destinations': {'records': destinations[0], 'live_bytes': destinations[1], 'bytes': sizes[1]},
                'database_bytes': sizes[2]}
    counts = db.execute(select(func.count(Log.id), func.coalesce(func.sum(func.length(Log.message)), 0))).one()
    destinations = db.execute(select(func.count(Destination.id), func.coalesce(func.sum(func.length(Destination.domain)), 0))).one()
    path = engine.url.database
    db_bytes = os.path.getsize(path) if path and os.path.exists(path) else 0
    return {'retention_days': days, 'session_logs_enabled': session_logs_enabled,
            'domain_history_enabled': domain_history_enabled,
            'logs': {'records': counts[0], 'live_bytes': counts[1], 'bytes': counts[1]},
            'destinations': {'records': destinations[0], 'live_bytes': destinations[1], 'bytes': destinations[1]},
            'database_bytes': db_bytes}


@app.get('/api/settings/storage')
def get_storage_settings():
    with DB() as db:
        return storage_report(db)


@app.put('/api/settings/storage')
def put_storage_settings(data: StorageSettings):
    if data.retention_days is not None and data.retention_days not in RETENTION_OPTIONS:
        raise ValueError('Выберите срок хранения от 7 до 365 дней')
    values = {'log_retention_days': str(data.retention_days) if data.retention_days is not None else None,
              'session_logs_enabled': str(data.session_logs_enabled).lower() if data.session_logs_enabled is not None else None,
              'domain_history_enabled': str(data.domain_history_enabled).lower() if data.domain_history_enabled is not None else None}
    with DB.begin() as db:
        for key, value in values.items():
            if value is None:
                continue
            setting = db.get(AppSetting, key)
            if setting is None:
                db.add(AppSetting(key=key, value=value))
            else:
                setting.value = value
    if runner:
        if data.session_logs_enabled is not None:
            runner.session_logs_enabled = data.session_logs_enabled
        if data.domain_history_enabled is not None:
            runner.domain_history_enabled = data.domain_history_enabled
    with DB() as db:
        return storage_report(db)


@app.post('/api/settings/storage/purge')
def purge_storage():
    result = purge_expired_records(100)
    with DB() as db:
        return {**result, 'storage': storage_report(db)}


def account_json(a):
    payload = decrypt(a.secret) if a.secret else None
    cookies = payload if isinstance(payload, list) else payload.get('cookies', []) if isinstance(payload, dict) else []
    has_credentials = isinstance(payload, dict) and bool(payload.get('email')) and bool(payload.get('password'))
    return {'id': a.id, 'name': a.name, 'platform': a.platform, 'portal': a.portal,
            'has_cookies': bool(cookies), 'has_credentials': has_credentials,
            'updated_at': a.updated_at.isoformat(), 'browser_open': a.id in browsers.items,
            'desktop_creator_open': bool(desktop_creators and a.id in desktop_creators.items
                                         and desktop_creators.items[a.id]['proc'].returncode is None)}


def preserve_credentials(value, secret, platform, portal, preserve_browser=True):
    previous = decrypt(secret) if secret else None
    incoming = value if isinstance(value, dict) else {'cookies': value}
    payload = {'cookies': incoming.get('cookies', [])}
    for key in ('email', 'password'):
        candidate = incoming.get(key)
        if not candidate and isinstance(previous, dict):
            candidate = previous.get(key)
        if isinstance(candidate, str):
            payload[key] = candidate
    clean = validate_cookies(payload, platform, portal)
    browser_cookies = incoming.get('browser_cookies')
    if preserve_browser and browser_cookies is None and isinstance(previous, dict):
        browser_cookies = previous.get('browser_cookies')
    if isinstance(browser_cookies, list):
        wrapped = {'cookies': clean if isinstance(clean, list) else clean.get('cookies', []),
                   'browser_cookies': browser_cookies}
        if isinstance(clean, dict):
            for key in ('email', 'password'):
                if key in clean:
                    wrapped[key] = clean[key]
        if isinstance(previous, dict):
            for key in ('email', 'password'):
                if key in previous and key not in wrapped:
                    wrapped[key] = previous[key]
        if platform == 'bitrix':
            wrapped['portal'] = portal
        return wrapped
    return clean


def browser_cookie_jar(cookies):
    allowed = {'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite', 'partitionKey'}
    result = []
    for cookie in cookies:
        item = {key: cookie[key] for key in allowed if key in cookie}
        # Playwright expects a positive Unix timestamp for persistent cookies;
        # session cookies are represented by omitting expires.
        if not isinstance(item.get('expires'), (int, float)) or item.get('expires', -1) <= 0:
            item.pop('expires', None)
        if item.get('sameSite') not in ('Strict', 'Lax', 'None'):
            item.pop('sameSite', None)
        if item.get('domain') and item.get('name') and item.get('value'):
            item.setdefault('path', '/')
            result.append(item)
    return result


EXPORT_COOKIE_FILES = {
    'vk': 'cookies-vk.json', 'telemost': 'cookies-yandex.json',
    'wbstream': 'cookies-wbstream.json', 'dion': 'cookies-dion.json',
    'bitrix': 'cookies-bitrix.json',
}


def cookies_from_export(archive: bytes, platform: str):
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            info = package.getinfo(EXPORT_COOKIE_FILES[platform])
            if info.file_size > 1_000_000:
                raise ValueError('Файл cookies в ZIP слишком большой')
            value = json.loads(package.read(info).decode('utf-8'))
    except (KeyError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('ZIP не содержит корректный файл cookies для выбранной платформы') from exc
    return value


def snapshot():
    with DB() as db:
        accounts = db.scalars(select(Account).order_by(Account.updated_at.desc())).all()
        sessions = db.scalars(select(Session).order_by(Session.created_at.desc())).all()
        return {'at': now().isoformat(), 'accounts': [account_json(a) for a in accounts],
                'platforms': [{'id': k, 'label': v['label']} for k, v in PLATFORMS.items()],
                'sessions': [{'id': s.id, 'name': s.name, 'account_id': s.account_id, 'state': s.state,
                              'recipient_type': s.recipient_type, 'recipient_label': s.recipient_label,
                              'recipient_value': s.recipient_value,
                              'vpn_enabled': s.vpn_enabled,
                              'vpn_default_enabled': bool(vpn_manager and vpn_manager.enabled),
                              'vpn_configured': bool(vpn_manager and vpn_manager.profile),
                              'vpn_effective': bool(vpn_manager and (vpn_manager.enabled or s.vpn_enabled)),
                              'matrix_user_id': s.matrix_user_id,
                              'matrix_display_name': s.matrix_display_name,
                              'matrix_has_avatar': s.matrix_has_avatar,
                              'matrix_retry_exhausted': s.matrix_retry_exhausted,
                              'resources': s.resources, 'has_link': bool(s.link_secret), 'created_at': s.created_at.isoformat(),
                              **runner.snapshot(s.id, s.rx_total, s.tx_total)} for s in sessions]}


@app.get('/api/state')
def state():
    return snapshot()


@app.get('/api/events')
async def events(request: Request):
    async def stream():
        while not await request.is_disconnected():
            if not authenticated(request):
                break
            yield 'data: ' + json.dumps(snapshot()) + '\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


class AccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform: str
    portal: str = Field(default='', max_length=255)


class AccountCredentials(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1000)


@app.post('/api/accounts', status_code=201)
def create_account(data: AccountInput):
    if not data.name.strip():
        raise ValueError('Введите название аккаунта')
    if data.platform not in PLATFORMS:
        raise ValueError('Неизвестная платформа')
    if data.platform == 'bitrix' and not re.fullmatch(r'https://[a-zA-Z0-9-]+\.bitrix24\.(?:ru|com|net|eu|de)/?', data.portal):
        raise ValueError('Введите HTTPS-адрес портала вида https://name.bitrix24.ru')
    with DB.begin() as db:
        account = Account(name=data.name.strip(), platform=data.platform, portal=data.portal.rstrip('/'))
        db.add(account)
        db.flush()
        return account_json(account)


def get_account(db, aid):
    a = db.get(Account, aid)
    if not a:
        raise HTTPException(404, 'Аккаунт не найден')
    return a


def assert_account_idle(aid):
    if any(j['account_id'] == aid for j in runner.jobs.values()):
        raise HTTPException(409, 'Остановите сессию перед изменением авторизации аккаунта')


@app.delete('/api/accounts/{aid}')
async def delete_account(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        with DB.begin() as db:
            a = get_account(db, aid)
            if db.scalar(select(Session.id).where(Session.account_id == aid).limit(1)):
                raise HTTPException(409, 'Сначала удалите сессии этого аккаунта')
            db.delete(a)
        await desktop_creators.remove(aid)
        await browsers.close(aid)
    return {'ok': True}


@app.put('/api/accounts/{aid}/cookies')
async def import_cookies(aid: str, request: Request):
    value = await request.json()
    async with runner.lock:
        assert_account_idle(aid)
        with DB.begin() as db:
            a = get_account(db, aid)
            if isinstance(value, dict) and 'export_zip_base64' in value:
                try:
                    archive = base64.b64decode(value['export_zip_base64'], validate=True)
                except (TypeError, ValueError, binascii.Error) as exc:
                    raise ValueError('Некорректный ZIP с cookies') from exc
                value = cookies_from_export(archive, a.platform)
            entries = value if isinstance(value, list) else value.get('cookies', []) if isinstance(value, dict) else []
            if a.platform == 'telemost':
                if not isinstance(entries, list) or not any(
                    isinstance(cookie, dict) and cookie.get('name') == 'Session_id' and cookie.get('value')
                    for cookie in entries
                ):
                    raise ValueError('В экспорте нет Session_id. Войдите в Яндекс через Desktop Creator и экспортируйте cookies ещё раз.')
                browser_jar = browser_cookie_jar(entries)
                if browser_jar:
                    value = {'cookies': entries, 'browser_cookies': browser_jar}
            a.secret = encrypt(preserve_credentials(value, a.secret, a.platform, a.portal, preserve_browser=False))
            a.updated_at = now()
    return {'ok': True, 'validated': 'format_only'}


@app.put('/api/accounts/{aid}/credentials')
async def save_account_credentials(aid: str, data: AccountCredentials):
    async with runner.lock:
        assert_account_idle(aid)
        with DB.begin() as db:
            a = get_account(db, aid)
            if a.platform not in ('dion', 'bitrix'):
                raise ValueError('Автоматический повторный вход поддерживается Creator только для DION и Битрикс24')
            email = data.email.strip()
            if not email:
                raise ValueError('Введите email или логин')
            payload = decrypt(a.secret) if a.secret else {'cookies': []}
            cookies = payload if isinstance(payload, list) else payload.get('cookies', []) if isinstance(payload, dict) else []
            a.secret = encrypt(validate_cookies({'cookies': cookies, 'email': email, 'password': data.password}, a.platform, a.portal))
            a.updated_at = now()
    return {'ok': True, 'has_credentials': True}


@app.delete('/api/accounts/{aid}/credentials')
async def delete_account_credentials(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        with DB.begin() as db:
            a = get_account(db, aid)
            if a.platform not in ('dion', 'bitrix'):
                raise ValueError('Автоматический повторный вход поддерживается Creator только для DION и Битрикс24')
            payload = decrypt(a.secret) if a.secret else None
            cookies = payload if isinstance(payload, list) else payload.get('cookies', []) if isinstance(payload, dict) else []
            if cookies:
                a.secret = encrypt(validate_cookies({'cookies': cookies}, a.platform, a.portal))
            else:
                a.secret = None
            a.updated_at = now()
    return {'ok': True, 'has_credentials': False}


@app.post('/api/accounts/{aid}/browser')
async def open_browser(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        if aid in desktop_creators.items and desktop_creators.items[aid]['proc'].returncode is None:
            raise HTTPException(409, 'Сначала закройте Desktop Creator для этого аккаунта')
        with DB() as db:
            a = get_account(db, aid)
            payload = decrypt(a.secret) if a.secret else None
        browser_cookies = payload.get('browser_cookies', []) if isinstance(payload, dict) else []
        await browsers.start(a, browser_cookies)
    return {'ok': True}


@app.post('/api/accounts/{aid}/browser/fresh')
async def open_fresh_browser(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        if aid in desktop_creators.items and desktop_creators.items[aid]['proc'].returncode is None:
            raise HTTPException(409, 'Сначала закройте Desktop Creator для этого аккаунта')
        with DB() as db:
            account = get_account(db, aid)
            if account.platform != 'telemost':
                raise ValueError('Повторный вход с очисткой сессии доступен только для Телемоста')
        await browsers.close(aid)
        with DB.begin() as db:
            account = get_account(db, aid)
            account.secret = None
            account.updated_at = now()
        await browsers.start(account)
    return {'ok': True}


@app.get('/api/accounts/{aid}/browser/frame')
async def browser_frame(aid: str):
    item = await browsers.get(aid)
    async with item['lock']:
        # Telemost occasionally makes Chromium fail during a font/layout
        # update. Retry once with animations disabled before failing the frame.
        try:
            image = await item['page'].screenshot(
                type='jpeg', quality=70, timeout=20000, animations='disabled', caret='hide'
            )
        except PlaywrightError:
            try:
                await asyncio.sleep(0.15)
                image = await item['page'].screenshot(
                    type='jpeg', quality=60, timeout=20000, animations='disabled', caret='hide'
                )
            except PlaywrightError:
                raise HTTPException(503, 'Экран браузера временно недоступен. Повторите попытку.')
    return Response(image, media_type='image/jpeg')


class BrowserAction(BaseModel):
    action: str
    x: float = Field(default=0, ge=0, le=1100)
    y: float = Field(default=0, ge=0, le=760)
    text: str = Field(default='', max_length=4000)
    key: str = Field(default='Tab', max_length=30)
    delta: int = Field(default=0, ge=-2000, le=2000)


@app.post('/api/accounts/{aid}/browser/action')
async def browser_action(aid: str, data: BrowserAction):
    item = await browsers.get(aid)
    async with item['lock']:
        p = item['page']
        if data.action == 'click':
            await p.mouse.click(data.x, data.y)
        elif data.action == 'type':
            await p.keyboard.insert_text(data.text)
        elif data.action == 'key' and data.key in ('Tab', 'Shift+Tab', 'Enter', 'Backspace', 'Escape', 'Control+A', 'ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Space'):
            await p.keyboard.press(data.key)
        elif data.action == 'scroll':
            await p.mouse.wheel(0, data.delta)
        else:
            raise ValueError('Неизвестная команда браузера')
    return {'ok': True}


@app.post('/api/accounts/{aid}/browser/save')
async def browser_save(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        item = await browsers.get(aid)
        async with item['lock']:
            browser_cookies = await item['context'].cookies()
        with DB() as db:
            a = get_account(db, aid)
            cookies = platform_cookies(browser_cookies, a, item)
        with DB.begin() as db:
            a = get_account(db, aid)
            required = {'vk': 'remixsid', 'telemost': 'Session_id', 'dion': 'vc-refresh-token', 'wbstream': 'wb_access_token', 'bitrix': 'BITRIX_SM_UIDH'}
            name = required.get(a.platform)
            if name and not any(c['name'] == name and c['value'] for c in cookies):
                raise ValueError('Авторизационные cookies ещё не найдены. Завершите вход на сайте платформы.')
            payload = preserve_credentials({'cookies': cookies, 'browser_cookies': browser_cookie_jar(browser_cookies)}, a.secret, a.platform, a.portal)
            a.secret = encrypt(payload)
            a.updated_at = now()
        await browsers.close(aid)
    return {'ok': True, 'validated': 'format_only'}


def platform_cookies(cookies, account, item):
    domains = {'vk': ('vk.com', 'vk.ru', 'vkid.ru'), 'telemost': ('yandex.ru', 'yandex.net', 'ya.ru'),
               'wbstream': ('wb.ru', 'wildberries.ru'), 'dion': ('dion.vc',), 'bitrix': ('bitrix24.ru', 'bitrix24.net', 'bitrix24.com', 'bitrix24.eu', 'bitrix24.de')}[account.platform]
    result = [c for c in cookies if any(c['domain'].lstrip('.') == d or c['domain'].lstrip('.').endswith('.'+d) for d in domains)]
    if account.platform == 'telemost':
        # The Creator consumes a flat name=value header, so cookies with the
        # same name on yandex.ru and ya.ru become ambiguous. Prefer the cookie
        # scoped to the Telemost/Yandex host, then keep ya.ru as a fallback.
        priority = {'yandex.ru': 0, 'yandex.net': 1, 'ya.ru': 2}
        selected = {}
        order = []
        for cookie in result:
            domain = cookie['domain'].lstrip('.').lower()
            domain_rank = min((rank for suffix, rank in priority.items()
                               if domain == suffix or domain.endswith('.' + suffix)), default=99)
            score = (domain_rank, 0 if cookie.get('path', '/') == '/' else 1)
            name = cookie['name']
            if name not in selected:
                selected[name] = (score, cookie)
                order.append(name)
            elif score < selected[name][0]:
                selected[name] = (score, cookie)
        result = [selected[name][1] for name in order]
    if account.platform == 'wbstream' and item['capture'].get('wb_access_token'):
        result = [c for c in result if c['name'] != 'wb_access_token']
        result.append({'name': 'wb_access_token', 'value': item['capture']['wb_access_token']})
    if account.platform == 'bitrix':
        for cookie in result:
            cookie['host'] = 'https://auth2.bitrix24.net' if 'bitrix24.net' in cookie['domain'] else account.portal
    return result


async def check_telemost_cookies(cookies):
    # Electron may hold same-name cookies on several Yandex subdomains and
    # paths. A flat Cookie header with every duplicate can shadow the account
    # session that Telemost actually uses. Keep one most-specific candidate.
    priority = {'yandex.ru': 0, 'yandex.net': 1, 'ya.ru': 2}
    selected = {}
    for cookie in cookies:
        if not cookie.get('name') or not cookie.get('value'):
            continue
        domain = str(cookie.get('domain') or '').lstrip('.').lower()
        rank = min((value for suffix, value in priority.items()
                    if domain == suffix or domain.endswith('.' + suffix)), default=99)
        score = (rank, 0 if cookie.get('path', '/') == '/' else 1)
        current = selected.get(cookie['name'])
        if current is None or score < current[0]:
            selected[cookie['name']] = (score, cookie)
    cookie_header = '; '.join(f"{c['name']}={c['value']}" for _, c in selected.values())
    headers = {'Cookie': cookie_header, 'User-Agent': DESKTOP_USER_AGENT}
    try:
        # Passport redirects signed-in accounts to id.yandex.ru and unsigned
        # sessions to /auth. This is a read-only session check; it does not
        # create a throwaway Telemost conference.
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get('https://passport.yandex.ru/', headers=headers)
        if response.status_code == 429 or response.status_code >= 500:
            return 'inconclusive'
        if response.status_code >= 400:
            return 'inconclusive'
        final = str(response.url)
        if 'passport.yandex.ru/auth' in final:
            return 'invalid'
        if 'id.yandex.ru' in final:
            return 'accepted'
        return 'inconclusive'
    except httpx.HTTPError:
        return 'inconclusive'


@app.get('/api/accounts/{aid}/browser/cookies.json')
async def download_browser_cookies(aid: str):
    item = await browsers.get(aid)
    async with item['lock']:
        cookies = await item['context'].cookies()
    with DB() as db:
        account = get_account(db, aid)
    domains = {'vk': ('vk.com', 'vk.ru', 'vkid.ru'), 'telemost': ('yandex.ru', 'yandex.net', 'ya.ru'),
               'wbstream': ('wb.ru', 'wildberries.ru'), 'dion': ('dion.vc',), 'bitrix': ('bitrix24.ru', 'bitrix24.net', 'bitrix24.com', 'bitrix24.eu', 'bitrix24.de')}[account.platform]
    cookies = [c for c in cookies if c.get('domain') and any(
        c['domain'].lstrip('.').lower() == d or c['domain'].lstrip('.').lower().endswith('.' + d)
        for d in domains
    )]
    data = json.dumps(browser_cookie_jar(cookies), ensure_ascii=False, indent=2)
    return Response(data, media_type='application/json', headers={'Content-Disposition': 'attachment; filename="telemost-cookies.json"'})


@app.post('/api/accounts/{aid}/desktop-creator')
async def start_desktop_creator(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        with DB() as db:
            get_account(db, aid)
        if aid in browsers.items:
            raise HTTPException(409, 'Закройте окно входа панели перед запуском Desktop Creator')
        await desktop_creators.start(aid)
    return {'ok': True}


@app.delete('/api/accounts/{aid}/desktop-creator')
async def stop_desktop_creator(aid: str):
    await desktop_creators.stop(aid)
    return {'ok': True}


@app.get('/api/accounts/{aid}/desktop-creator/frame')
async def desktop_creator_frame(aid: str):
    response = await desktop_creators.request(aid, 'GET', '/frame')
    return Response(response.content, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


class DesktopCreatorAction(BaseModel):
    action: str
    x: float = Field(default=0, ge=0, le=1200)
    y: float = Field(default=0, ge=0, le=800)
    text: str = Field(default='', max_length=4000)
    key: str = Field(default='Tab', max_length=30)
    delta: int = Field(default=0, ge=-2000, le=2000)


@app.post('/api/accounts/{aid}/desktop-creator/action')
async def desktop_creator_action(aid: str, data: DesktopCreatorAction):
    await desktop_creators.request(aid, 'POST', '/action', data.model_dump())
    return {'ok': True}


@app.post('/api/accounts/{aid}/desktop-creator/save')
async def save_desktop_creator_cookies(aid: str):
    async with runner.lock:
        assert_account_idle(aid)
        with DB() as db:
            account = get_account(db, aid)
            platform_id = account.platform
        if platform_id not in PLATFORMS:
            raise ValueError('Неизвестная платформа аккаунта')
        archive = await desktop_creators.request(aid, 'GET', '/cookies.zip')
        exported = cookies_from_export(archive.content, platform_id)
        entries = exported if isinstance(exported, list) else exported.get('cookies', []) if isinstance(exported, dict) else []
        required = {'vk': 'remixsid', 'telemost': 'Session_id', 'dion': 'vc-refresh-token',
                    'wbstream': 'wb_access_token', 'bitrix': 'BITRIX_SM_UIDH'}[platform_id]
        if not any(isinstance(cookie, dict) and cookie.get('name') == required and cookie.get('value') for cookie in entries):
            raise ValueError(f'Desktop Creator пока не видит авторизованную сессию: не найдена cookie {required}')
        response = await desktop_creators.request(aid, 'GET', f'/cookies?platform={platform_id}')
        jar = browser_cookie_jar(response.json())
        payload = exported.copy() if isinstance(exported, dict) else {'cookies': exported}
        if jar:
            payload['browser_cookies'] = jar
        with DB.begin() as db:
            account = get_account(db, aid)
            account.secret = encrypt(preserve_credentials(payload, account.secret, account.platform, account.portal))
            account.updated_at = now()
        return {'ok': True, 'cookie_count': len(entries), 'auth_cookie': required}


@app.get('/api/accounts/{aid}/desktop-creator/cookies.json')
async def download_desktop_creator_cookies(aid: str):
    with DB() as db:
        account = get_account(db, aid)
        platform_id = account.platform
    response = await desktop_creators.request(aid, 'GET', '/cookies.zip')
    cookies = cookies_from_export(response.content, platform_id)
    if not cookies:
        raise HTTPException(404, 'Desktop Creator пока не экспортировал cookies для этой платформы')
    data = json.dumps(cookies, ensure_ascii=False, indent=2)
    filename = EXPORT_COOKIE_FILES[platform_id]
    return Response(data, media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store, private',
        'Pragma': 'no-cache',
    })


@app.get('/api/accounts/{aid}/desktop-creator/cookies.zip')
async def download_desktop_creator_cookie_export(aid: str):
    with DB() as db:
        get_account(db, aid)
    response = await desktop_creators.request(aid, 'GET', '/cookies.zip')
    if not response.content:
        raise HTTPException(404, 'Desktop Creator пока не экспортировал cookies')
    return Response(response.content, media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="cookies.zip"',
        'Cache-Control': 'no-store, private',
        'Pragma': 'no-cache',
    })


@app.post('/api/accounts/{aid}/desktop-creator/check')
async def check_desktop_creator_cookies(aid: str):
    with DB() as db:
        account = get_account(db, aid)
        platform_id = account.platform
    response = await desktop_creators.request(aid, 'GET', f'/cookies?platform={platform_id}')
    cookies = browser_cookie_jar(response.json())
    if not cookies:
        return {'status': 'missing', 'cookie_count': 0}
    required = {'vk': 'remixsid', 'telemost': 'Session_id', 'dion': 'vc-refresh-token',
                'wbstream': 'wb_access_token', 'bitrix': 'BITRIX_SM_UIDH'}[platform_id]
    if not any(cookie.get('name') == required and cookie.get('value') for cookie in cookies):
        return {'status': 'missing_auth_cookie', 'cookie_count': len(cookies)}
    if platform_id != 'telemost':
        return {'status': 'presence_only', 'cookie_count': len(cookies)}
    status = await check_telemost_cookies(cookies)
    return {'status': status, 'cookie_count': len(cookies)}


@app.delete('/api/accounts/{aid}/browser')
async def close_browser(aid: str):
    await browsers.close(aid)
    return {'ok': True}


class SessionInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    account_id: str
    resources: str = 'default'
    room: str = Field(default='', max_length=2000)
    recipient_type: str = Field(default='', max_length=20)
    recipient_label: str = Field(default='', max_length=100)
    recipient_value: str = Field(default='', max_length=500)


class SessionRecipient(BaseModel):
    recipient_type: str = Field(default='', max_length=20)
    recipient_label: str = Field(default='', max_length=100)
    recipient_value: str = Field(default='', max_length=500)
    matrix_user_id: str = Field(default='', max_length=255)
    matrix_display_name: str = Field(default='', max_length=255)
    matrix_has_avatar: bool = False


class SessionRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SessionVpnInput(BaseModel):
    enabled: bool


@app.post('/api/sessions', status_code=201)
def create_session(data: SessionInput):
    if not data.name.strip():
        raise ValueError('Введите название сессии')
    if data.resources not in ('moderate', 'default', 'unlimited'):
        raise ValueError('Неизвестный профиль ресурсов')
    if data.room.startswith('-') or any(c in data.room for c in ('\n', '\r', '\x00')):
        raise ValueError('Некорректная ссылка комнаты')
    validate_recipient(data.recipient_type, data.recipient_label, data.recipient_value)
    with DB.begin() as db:
        get_account(db, data.account_id)
        s = Session(name=data.name.strip(), account_id=data.account_id, resources=data.resources,
                    room=encrypt(data.room.strip()) if data.room.strip() else '', recipient_type=data.recipient_type,
                    recipient_label=data.recipient_label.strip(), recipient_value=data.recipient_value.strip())
        db.add(s)
        db.flush()
        return {'id': s.id}


def get_session(db, sid):
    s = db.get(Session, sid)
    if not s:
        raise HTTPException(404, 'Сессия не найдена')
    return s


@app.put('/api/sessions/{sid}')
def rename_session(sid: str, data: SessionRename):
    name = data.name.strip()
    if not name:
        raise ValueError('Введите название сессии')
    with DB.begin() as db:
        session = get_session(db, sid)
        session.name = name
        return {'id': session.id, 'name': session.name}


@app.put('/api/sessions/{sid}/vpn')
def set_session_vpn(sid: str, data: SessionVpnInput):
    if data.enabled and not vpn_manager.profile:
        raise ValueError('VPN не настроен. Сначала добавьте и проверьте VLESS-конфигурацию в настройках.')
    if sid in runner.jobs:
        raise HTTPException(409, 'Остановите сессию перед сменой VPN-маршрута; затем запустите её снова.')
    with DB.begin() as db:
        session = get_session(db, sid)
        session.vpn_enabled = data.enabled
        return {'id': session.id, 'vpn_enabled': session.vpn_enabled,
                'vpn_effective': bool(vpn_manager.enabled or session.vpn_enabled)}


def validate_recipient(kind: str, label: str, value: str):
    if kind not in ('', 'telegram', 'whatsapp', 'other'):
        raise ValueError('Выберите Telegram, WhatsApp или другой способ связи')
    if any(ord(ch) < 32 for ch in label + value):
        raise ValueError('Контакт содержит недопустимые символы')
    if kind == 'other' and not label.strip():
        raise ValueError('Укажите название способа связи')
    if kind == 'telegram' and value.strip():
        identifier = value.strip().removeprefix('@')
        if not (re.fullmatch(r'[A-Za-z0-9_]{5,32}', identifier) or re.fullmatch(r'\d{5,20}', identifier)):
            raise ValueError('Для Telegram укажите @username или числовой ID')
    if kind == 'whatsapp' and value.strip():
        identifier = value.strip()
        if not re.fullmatch(r'[+\d() .-]{8,30}|[\w.-]{1,64}@s\.whatsapp\.net', identifier, re.I):
            raise ValueError('Для WhatsApp укажите номер телефона или ID WhatsApp')


@app.put('/api/sessions/{sid}/recipient')
async def update_session_recipient(sid: str, data: SessionRecipient):
    validate_recipient(data.recipient_type, data.recipient_label, data.recipient_value)
    if data.matrix_user_id and not re.fullmatch(r'@[^:\s]{1,255}:[^\s]{1,255}', data.matrix_user_id):
        raise ValueError('Некорректный Matrix user ID')
    with DB.begin() as db:
        session = get_session(db, sid)
        if data.matrix_user_id and data.matrix_user_id != session.matrix_user_id and not matrix_manager.config:
            raise ValueError('Сначала подключите Matrix интеграцию в настройках')
        old_matrix_user = session.matrix_user_id
        session.recipient_type = data.recipient_type
        session.recipient_label = data.recipient_label.strip()
        session.recipient_value = data.recipient_value.strip()
        session.matrix_user_id = data.matrix_user_id.strip()
        session.matrix_display_name = data.matrix_display_name.strip()[:255] if data.matrix_user_id else ''
        session.matrix_has_avatar = bool(data.matrix_has_avatar) if data.matrix_user_id else False
        if not data.matrix_user_id or data.matrix_user_id != old_matrix_user:
            session.matrix_room_id = ''
            session.matrix_greeting_sent = False
        result = {'id': session.id, 'matrix_user_id': session.matrix_user_id}
    if data.matrix_user_id != old_matrix_user:
        matrix_manager.clear_session_avatar(sid)
    if data.matrix_user_id:
        # Persisting the binding comes first; retries can then resume a room or greeting
        # if the homeserver temporarily rejects the first request.
        await matrix_manager.start_conversation(sid)
    return result


@app.post('/api/sessions/{sid}/start')
async def start_session(sid: str):
    with DB() as db:
        s = get_session(db, sid)
        if s.account_id in browsers.items:
            raise HTTPException(409, 'Сначала сохраните или закройте окно входа')
    await runner.start(sid)
    return {'ok': True}


@app.post('/api/sessions/{sid}/stop')
async def stop_session(sid: str):
    with DB() as db:
        get_session(db, sid)
    await runner.stop(sid)
    return {'ok': True}


@app.delete('/api/sessions/{sid}')
async def delete_session(sid: str):
    async with runner.lock:
        if sid in runner.jobs:
            raise HTTPException(409, 'Сначала остановите сессию')
        with DB.begin() as db:
            s = get_session(db, sid)
            db.query(Log).filter(Log.session_id == sid).delete()
            db.query(Destination).filter(Destination.session_id == sid).delete()
            db.delete(s)
        matrix_manager.clear_session_avatar(sid)
    return {'ok': True}


@app.get('/api/sessions/{sid}/link')
def get_link(sid: str):
    with DB() as db:
        s = get_session(db, sid)
        return {'link': decrypt(s.link_secret) if s.link_secret else None}


@app.get('/api/logs')
def logs(sid: str | None = None, errors: bool = False, after: int = 0):
    with DB() as db:
        q = select(Log).where(Log.id > after)
        if sid:
            q = q.where(Log.session_id == sid)
        if errors:
            q = q.where(Log.level == 'error')
        # Initial load shows the newest 300; incremental load reads oldest first to avoid gaps.
        q = q.order_by(Log.id.asc() if after else Log.id.desc()).limit(300)
        rows = list(db.scalars(q))
        if not after:
            rows.reverse()
        return [{'id': r.id, 'session_id': r.session_id, 'level': r.level, 'message': r.message, 'at': r.at.isoformat()} for r in rows]


@app.get('/api/sessions/{sid}/logs')
def session_logs(sid: str, level: str = 'all', since: datetime | None = None,
                 until: datetime | None = None, before: int = 0, after: int = 0):
    if level not in ('all', 'info', 'warning', 'error'):
        raise ValueError('Неизвестный уровень журнала')
    if since and until and since > until:
        raise ValueError('Начало периода позже его конца')
    if before < 0 or after < 0 or before and after:
        raise ValueError('Некорректная граница журнала')
    with DB() as db:
        get_session(db, sid)
        q = select(Log).where(Log.session_id == sid)
        if level != 'all':
            q = q.where(Log.level == level)
        if since:
            q = q.where(Log.at >= since)
        if until:
            q = q.where(Log.at <= until)
        if before:
            q = q.where(Log.id < before)
        if after:
            q = q.where(Log.id > after)
        # Incremental reads go oldest-first so large bursts can be consumed
        # without skipping rows; reverse those results for the UI's newest-first view.
        rows = list(db.scalars(q.order_by(Log.id.asc() if after else Log.id.desc()).limit(301)))
        has_more = len(rows) > 300
        rows = rows[:300]
        if after:
            rows.reverse()
        return {'items': [{'id': r.id, 'level': r.level, 'message': r.message,
                           'at': r.at.isoformat()} for r in rows], 'has_more': has_more}


@app.get('/api/sessions/{sid}/destinations')
def session_destinations(sid: str, domain: str = '', since: datetime | None = None,
                         until: datetime | None = None, before: int = 0, after: int = 0):
    if since and until and since > until:
        raise ValueError('Начало периода позже его конца')
    if before < 0 or after < 0 or before and after:
        raise ValueError('Некорректная граница журнала')
    with DB() as db:
        get_session(db, sid)
        q = select(Destination).where(Destination.session_id == sid)
        if domain:
            q = q.where(Destination.domain.ilike(f'%{domain.strip()}%'))
        if since:
            q = q.where(Destination.at >= since)
        if until:
            q = q.where(Destination.at <= until)
        if before:
            q = q.where(Destination.id < before)
        if after:
            q = q.where(Destination.id > after)
        # Newest-first pages; incremental reads briefly use ascending order so
        # bursts larger than one page are consumed without leaving gaps.
        rows = list(db.scalars(q.order_by(Destination.id.asc() if after else Destination.id.desc()).limit(301)))
        has_more = len(rows) > 300
        rows = rows[:300]
        if after:
            rows.reverse()
        items = []
        for row in rows:
            items.append({'id': row.id, 'domain': row.domain, 'port': row.port, 'at': row.at.isoformat()})
        return {'items': items, 'has_more': has_more}
