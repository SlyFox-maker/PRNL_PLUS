import json
import os
import re
from cryptography.fernet import Fernet

DESKTOP_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
)

PLATFORMS = {
    'vk': {'label': 'VK Звонки', 'url': 'https://vk.ru/', 'flag': '--vk-link'},
    # Match the upstream desktop Creator login flow: authenticate at Passport
    # and return directly to Telemost after sign-in.
    'telemost': {'label': 'Яндекс Телемост', 'url': 'https://passport.yandex.ru/auth?retpath=https%3A%2F%2Ftelemost.yandex.ru%2F', 'flag': '--tm-link'},
    'wbstream': {'label': 'WB Stream', 'url': 'https://stream.wb.ru/login', 'flag': '--room'},
    'dion': {'label': 'DION', 'url': 'https://dion.vc/login', 'flag': '--room'},
    'bitrix': {'label': 'Битрикс24', 'url': '', 'flag': '--room'},
}


def cipher():
    return Fernet(os.environ['ENCRYPTION_KEY'].encode())


def encrypt(value):
    return cipher().encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def decrypt(value):
    return json.loads(cipher().decrypt(value.encode()))


def validate_cookies(value, platform, portal=''):
    entries = value if isinstance(value, list) else value.get('cookies', []) if isinstance(value, dict) else None
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError('Ожидается массив cookies или объект с полем cookies')
    if not entries and not (isinstance(value, dict) and value.get('email') and value.get('password') and platform in ('dion', 'bitrix')):
        raise ValueError('Cookies пусты. Завершите вход или задайте учётные данные DION/Bitrix.')
    for cookie in entries:
        if not isinstance(cookie, dict) or not isinstance(cookie.get('name'), str) or not isinstance(cookie.get('value'), str):
            raise ValueError('Каждая cookie должна содержать строковые name и value')
    # The upstream clients accept name/value pairs, not a Playwright storage_state.
    clean = [{'name': c['name'], 'value': c['value']} for c in entries]
    if platform == 'bitrix':
        for original, cookie in zip(entries, clean):
            host = original.get('host', '')
            if host and isinstance(host, str) and re.fullmatch(r'https://[a-zA-Z0-9.-]+\.bitrix24\.(?:ru|com|net|eu|de)', host):
                cookie['host'] = host
    if platform in ('dion', 'bitrix'):
        result = {'cookies': clean}
        if isinstance(value, dict):
            result.update({k: value[k] for k in ('email', 'password') if isinstance(value.get(k), str)})
        if platform == 'bitrix':
            result['portal'] = portal
        return result
    return clean


def redact(line, secrets=()):
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= 3:
            line = line.replace(secret, '[REDACTED]')
    line = re.sub(r'(?:https?://|(?:wbstream|dion)://)\S+', '[URL REDACTED]', line)
    line = re.sub(r'(?i)((?:cookie|token|password|authorization|secret|room|slug|join_link|call_link)[\w-]*\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', line)
    if any(s in line.lower() for s in ('[auth]', 'set-cookie', 'bearer ', 'cookie-string')):
        return '[auth] Подробности авторизации скрыты'
    return line[:4000]
