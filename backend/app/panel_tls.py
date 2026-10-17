"""Status and configuration for the local self-signed HTTPS gateway."""

import hashlib
import ipaddress
import os
import secrets
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


def _directory():
    return Path(os.environ.get('PANEL_TLS_DIR', '/panel-tls'))


def configured_target():
    try:
        value = (_directory() / 'ip').read_text().strip()
        return 'localhost' if value.lower() == 'localhost' else str(ipaddress.ip_address(value))
    except (OSError, ValueError):
        return 'localhost'


def origin():
    target = configured_target()
    if target == 'localhost':
        return f'https://localhost:{os.environ.get("PANEL_HTTP_PORT", "8080")}'
    host = f'[{target}]' if ':' in target else target
    port = os.environ.get('PANEL_HTTPS_PORT', '443')
    return f'https://{host}' if port == '443' else f'https://{host}:{port}'


def access_path():
    directory = _directory()
    try:
        value = (directory / 'access-path').read_text().strip()
        if len(value) >= 32 and all(char.isalnum() or char in '-_' for char in value):
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = directory / 'access-path.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(value + '\n')
    temporary.replace(directory / 'access-path')
    return value


def rotate_access_path():
    directory = _directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    temporary = directory / 'access-path.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(value + '\n')
    temporary.replace(directory / 'access-path')
    return value


def download_ca_certificate():
    return (_directory() / 'ca.crt').read_bytes()


def request_certificate_renewal():
    directory = _directory()
    temporary = directory / 'cert-format.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write('2\n')
    temporary.replace(directory / 'cert-format')


def local_origin():
    return f'https://localhost:{os.environ.get("PANEL_HTTP_PORT", "8080")}'


def legacy_local_origin():
    return f'http://localhost:{os.environ.get("PANEL_HTTP_PORT", "8080")}'


def status():
    target = configured_target()
    access = access_path()
    result = {'configured': True, 'ip': target, 'url': f'{origin()}/{access}/', 'local_url': f'{local_origin()}/{access}/', 'access_path': access,
              'bind_ip': os.environ.get('PANEL_BIND_IP', '127.0.0.1'),
              'http_port': int(os.environ.get('PANEL_HTTP_PORT', '8080')),
              'https_port': int(os.environ.get('PANEL_HTTPS_PORT', '443')),
              'ready': False, 'auto_renew': True, 'expires_at': '', 'fingerprint': '', 'ca_fingerprint': '', 'error': ''}
    try:
        certificate = x509.load_pem_x509_certificate((_directory() / 'cert.pem').read_bytes())
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        result['error'] = 'Сертификат ещё выпускается.'
        return result
    local_matches = 'localhost' in san.get_values_for_type(x509.DNSName)
    matches = local_matches and (target == 'localhost' or ipaddress.ip_address(target) in san.get_values_for_type(x509.IPAddress))
    expires = certificate.not_valid_after_utc
    result['expires_at'] = expires.isoformat()
    result['fingerprint'] = hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    try:
        ca_certificate = x509.load_pem_x509_certificate((_directory() / 'ca.crt').read_bytes())
        result['ca_fingerprint'] = hashlib.sha256(ca_certificate.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
        certificate.verify_directly_issued_by(ca_certificate)
    except (OSError, ValueError, InvalidSignature):
        result['error'] = 'Серверный сертификат не подписан локальным центром PRNL.'
        return result
    if not matches:
        result['error'] = 'Сертификат для нового IP ещё выпускается.'
        return result
    if expires <= datetime.now(timezone.utc):
        result['error'] = 'Срок сертификата истёк; ожидаем перевыпуска.'
        return result
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        for port in (443, 8080):
            with socket.create_connection(('panel-tls', port), timeout=0.5) as connection:
                with context.wrap_socket(connection, server_hostname='localhost') as tls:
                    served = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest().upper()
            if served != result['fingerprint']:
                result['error'] = 'HTTPS-прокси ещё применяет новый сертификат.'
                return result
        result['ready'] = True
    except (OSError, ssl.SSLError):
        result['error'] = 'Ожидаем запуска HTTPS-прокси.'
    return result


def set_ip(value):
    target = value.strip()
    if target.lower() == 'localhost':
        target = 'localhost'
    else:
        try:
            ip = ipaddress.ip_address(target)
        except ValueError:
            raise ValueError('Укажите localhost или корректный IPv4/IPv6 адрес сервера') from None
        if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError('Этот IP нельзя использовать для панели')
        target = str(ip)
    directory = _directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = directory / 'ip.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(target + '\n')
    temporary.replace(directory / 'ip')
    return status()
