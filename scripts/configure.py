"""Create or update deployment settings without replacing existing secrets."""
import argparse
import base64
import getpass
import ipaddress
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

path = Path(__file__).resolve().parents[1] / '.env'


def ask(label, default):
    value = input(f'{label} [{default}]: ').strip()
    return value or default


def quoted(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def read_env():
    result = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("\\'", "'").replace('\\\\', '\\')
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key.strip()] = value
    return result


def ask_host(default):
    while True:
        value = ask('Bind панели (localhost или 0.0.0.0)', default).lower()
        if value == 'localhost':
            return '127.0.0.1'
        if value == '0.0.0.0':
            return value
        print('Укажите localhost или 0.0.0.0.')


def ask_port(label, default):
    while True:
        value = ask(label, str(default))
        try:
            port = int(value)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
        print('Порт должен быть числом от 1 до 65535.')


def ask_public_origin(bind_ip, https_port, http_port, default):
    fallback = default or (f'https://localhost:{http_port}' if bind_ip == '127.0.0.1' else 'https://SERVER_IP')
    if bind_ip == '127.0.0.1' and (not default or urlsplit(default).hostname != 'localhost'):
        fallback = f'https://localhost:{http_port}'
    if bind_ip == '0.0.0.0' and (not default or urlsplit(default).hostname == 'localhost'):
        fallback = 'https://SERVER_IP'
    while True:
        value = ask('Внешний HTTPS URL панели (localhost или IP)', fallback)
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
                raise ValueError
            if host.lower() == 'localhost':
                cert_host, expected_port = 'localhost', http_port
            else:
                cert_host, expected_port = str(ipaddress.ip_address(host)), https_port
            if parsed.port is not None and (not 1 <= parsed.port <= 65535 or parsed.port != expected_port):
                raise ValueError
            if parsed.port is None and (cert_host == 'localhost' or https_port != 443):
                value = f'{value}:{expected_port}'
            return value.rstrip('/'), cert_host
        except (ValueError, AttributeError):
            print('Нужен HTTPS URL с localhost или IP, например https://203.0.113.10.')


def ask_password(label, existing=''):
    value = getpass.getpass(label + (' (Enter — оставить прежний): ' if existing else ': ')).strip()
    return value or existing


parser = argparse.ArgumentParser(description='Create or update the PRNL deployment .env')
parser.add_argument('--reconfigure', action='store_true', help='update deployment options while preserving database/encryption secrets')
args = parser.parse_args()

existing = read_env() if path.exists() else {}
if path.exists() and not args.reconfigure:
    raise SystemExit('.env уже существует и оставлен без изменений. Для настройки адреса/портов используй --reconfigure.')

print('Настройка PRNL. Файл .env будет сохранён с правами 0600.')
bind_ip = ask_host(existing.get('PANEL_BIND_IP', '127.0.0.1'))
http_port = ask_port('Локальный HTTPS-порт (контейнер 8080)', existing.get('PANEL_HTTP_PORT', 8080))
https_port = ask_port('Внешний HTTPS-порт (контейнер 443)', existing.get('PANEL_HTTPS_PORT', 443))
while http_port == https_port:
    print('Локальный и внешний HTTPS-порты должны различаться.')
    https_port = ask_port('Внешний HTTPS-порт (контейнер 443)', 443)
public_origin, cert_host = ask_public_origin(bind_ip, https_port, http_port, existing.get('PUBLIC_ORIGIN', ''))

admin_username = ask('Начальный логин администратора', existing.get('ADMIN_USERNAME', 'admin'))
while len(admin_username) < 2 or any(c.isspace() for c in admin_username):
    print('Логин должен содержать минимум 2 символа и не содержать пробелы.')
    admin_username = ask('Начальный логин администратора', 'admin')
admin_password = ask_password('Начальный пароль администратора (минимум 16 символов)', existing.get('ADMIN_PASSWORD', ''))
if not admin_password:
    admin_password = secrets.token_urlsafe(24)
    print(f'Сгенерирован пароль администратора: {admin_password}')
while len(admin_password) < 16:
    print('Пароль должен содержать минимум 16 символов.')
    admin_password = ask_password('Начальный пароль администратора', '')

old_vless = existing.get('XRAY_VLESS_URI', '')
vless_default = 'настроен (скрыт)' if old_vless else 'не задан'
vless_input = input(f'VLESS URI [{vless_default}; Enter — оставить, none — очистить]: ').strip()
vless_uri = old_vless if not vless_input else '' if vless_input.lower() == 'none' else vless_input
if vless_uri and not vless_uri.startswith('vless://'):
    raise SystemExit('Ожидалась ссылка vless://…')
xray_enabled = existing.get('XRAY_ENABLED', 'false').lower()
if vless_uri:
    xray_enabled = ask('Сразу включить VPN-маршрут для сессий (true/false)', xray_enabled).lower()
    if xray_enabled not in {'true', 'false'}:
        raise SystemExit('Значение VPN должно быть true или false')
else:
    xray_enabled = 'false'

old_homeserver = existing.get('MATRIX_HOMESERVER', '')
matrix_default = old_homeserver or 'не задан'
matrix_input = input(f'Matrix homeserver URL [{matrix_default}; Enter — оставить, none — отключить env-вход]: ').strip()
matrix_homeserver = old_homeserver if not matrix_input else '' if matrix_input.lower() == 'none' else matrix_input.rstrip('/')
matrix_username = existing.get('MATRIX_USERNAME', '') if matrix_homeserver else ''
matrix_password = existing.get('MATRIX_PASSWORD', '') if matrix_homeserver else ''
if matrix_homeserver:
    if not matrix_homeserver.startswith(('https://', 'http://')):
        raise SystemExit('Matrix homeserver должен начинаться с https:// или http://')
    matrix_username = ask('Matrix логин или user ID', matrix_username)
    matrix_password = ask_password('Matrix пароль', matrix_password)
    if not matrix_username or not matrix_password:
        raise SystemExit('Для первоначального Matrix-входа нужны и логин, и пароль')

values = {
    'POSTGRES_PASSWORD': existing.get('POSTGRES_PASSWORD') or secrets.token_hex(32),
    'ADMIN_USERNAME': admin_username,
    'ADMIN_PASSWORD': admin_password,
    'ENCRYPTION_KEY': existing.get('ENCRYPTION_KEY') or base64.urlsafe_b64encode(os.urandom(32)).decode(),
    'PANEL_BIND_IP': bind_ip,
    'PANEL_HTTP_PORT': str(http_port),
    'PANEL_HTTPS_PORT': str(https_port),
    'PANEL_CERT_HOST': cert_host,
    'PUBLIC_ORIGIN': public_origin,
    'COOKIE_SECURE': 'true',
    'XRAY_VLESS_URI': vless_uri,
    'XRAY_ENABLED': xray_enabled,
    'MATRIX_HOMESERVER': matrix_homeserver,
    'MATRIX_USERNAME': matrix_username,
    'MATRIX_PASSWORD': matrix_password,
}
body = ''.join(f'{key}={quoted(value)}\n' for key, value in values.items())
if existing:
    temporary = path.with_name(f'.env.{secrets.token_hex(6)}.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(body)
    os.replace(temporary, path)
else:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit('.env уже существует; файл оставлен без изменений')
    with os.fdopen(fd, 'w') as file:
        file.write(body)
print(f'.env сохранён (0600). Публичный адрес: {public_origin}')
print('Сохрани резервную копию .env. Не меняй ENCRYPTION_KEY после запуска: с ним зашифрованы данные приложения.')
