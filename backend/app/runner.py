import asyncio
import json
import ipaddress
import os
import re
import shutil
import signal
import time
import re
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select, delete
from .db import DB, Account, Session, Log, Destination, now
from .security import PLATFORMS, decrypt, encrypt, redact


class Runner:
    def __init__(self, vpn=None):
        self.jobs = {}
        self.lock = asyncio.Lock()
        self.vpn = vpn
        self.on_unexpected_exit = None
        self.on_link_ready = None
        self.session_logs_enabled = True
        self.domain_history_enabled = True
        self.root = Path(os.environ.get('RUNTIME_DIR', '/tmp/wlp-runtime'))
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def log(self, sid, message, level='info', secrets=()):
        if not self.session_logs_enabled:
            return
        with DB.begin() as db:
            db.add(Log(session_id=sid, level=level, message=redact(message, secrets)))
            # Bound retained logs per session, including repetitive upstream errors.
            cutoff = db.scalar(select(Log.id).where(Log.session_id == sid).order_by(Log.id.desc()).offset(1999).limit(1))
            if cutoff:
                db.execute(delete(Log).where(Log.session_id == sid, Log.id < cutoff))

    def snapshot(self, sid, rx_total=0, tx_total=0):
        job = self.jobs.get(sid)
        if not job:
            return {'telemetry': 'offline', 'peer': 'unknown', 'rx_bytes': rx_total, 'tx_bytes': tx_total, 'rx_rate': None, 'tx_rate': None, 'last_seen': None, 'active_connections': None}
        m = job.get('metrics')
        fresh = m and time.monotonic() - job.get('metric_at', 0) < 6
        age = m.get('peer_age_ms', -1) if fresh else -1
        if age >= 0:
            age += int((time.monotonic() - job['metric_at']) * 1000)
        return {'telemetry': 'live' if fresh else 'waiting', 'peer': 'recent' if 0 <= age < 15000 else 'unknown',
                'last_seen': m.get('last_seen') if fresh else None,
                'rx_bytes': max(rx_total, job.get('traffic_base_rx', 0) + (m.get('rx_bytes', 0) if m else 0)),
                'tx_bytes': max(tx_total, job.get('traffic_base_tx', 0) + (m.get('tx_bytes', 0) if m else 0)),
                'rx_rate': job.get('rx_rate', 0) if fresh else None, 'tx_rate': job.get('tx_rate', 0) if fresh else None,
                'active_connections': m.get('active_connections') if fresh else None}

    async def start(self, sid, *, notify_on_link=True):
        async with self.lock:
            if sid in self.jobs:
                raise ValueError('Сессия уже запущена')
            with DB() as db:
                s = db.get(Session, sid)
                account = db.get(Account, s.account_id)
                if not account.secret:
                    raise ValueError('Сначала войдите в аккаунт и сохраните cookies')
                if any(j['account_id'] == account.id for j in self.jobs.values()):
                    raise ValueError('Этот аккаунт уже используется. Остановите его сессию: обновляемые токены нельзя разделять.')
                binary = Path(os.environ.get('CREATOR_BIN_DIR', '/opt/creators')) / f'headless-{account.platform}-creator'
                if not binary.is_file():
                    raise ValueError('Creator-бинарник отсутствует. Соберите backend Docker-образ.')
                payload = decrypt(account.secret)
                if isinstance(payload, dict) and account.platform not in ('dion', 'bitrix'):
                    creator_payload = payload.get('cookies', [])
                elif isinstance(payload, dict):
                    creator_payload = {k: v for k, v in payload.items() if k != 'browser_cookies'}
                else:
                    creator_payload = payload
                if account.platform == 'telemost' and not any(
                    isinstance(c, dict) and c.get('name') == 'Session_id' and c.get('value')
                    for c in creator_payload
                ):
                    raise ValueError('В сохранённом экспорте нет Session_id. Перенесите cookies из Desktop Creator или импортируйте cookies-yandex.json.')
                directory = self.root / sid
                directory.mkdir(mode=0o700, exist_ok=True)
                cookie_file = directory / 'cookies.json'
                cookie_file.write_text(json.dumps(creator_payload))
                cookie_file.chmod(0o600)
                link_file = directory / 'link.txt'
                link_file.unlink(missing_ok=True)
                args = [str(binary), '--cookies', str(cookie_file), '--write-file', str(link_file), '--resources', s.resources]
                use_vpn = bool(self.vpn and (self.vpn.enabled or s.vpn_enabled))
                if use_vpn and not self.vpn.profile:
                    shutil.rmtree(directory, ignore_errors=True)
                    raise ValueError('Для этой сессии включён VPN, но VLESS не настроен. Добавьте конфигурацию в настройках VPN.')
                if self.vpn and self.vpn.profile:
                    if not self.vpn.has_route():
                        shutil.rmtree(directory, ignore_errors=True)
                        raise ValueError('Локальный Xray-маршрут не запущен. Откройте настройки Xray и проверьте конфигурацию.')
                    socks_port = self.vpn.vpn_socks_port if use_vpn and not self.vpn.enabled else self.vpn.socks_port
                    args += ['--upstream-socks', f'{self.vpn.socks_host}:{socks_port}']
                if s.room:
                    args += [PLATFORMS[account.platform]['flag'], decrypt(s.room)]
                secrets = [c['value'] for c in (creator_payload if isinstance(creator_payload, list) else creator_payload.get('cookies', []))]
                if isinstance(creator_payload, dict):
                    secrets += [creator_payload.get('password', ''), creator_payload.get('email', '')]
                try:
                    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                                start_new_session=True, limit=1024*1024)
                except OSError:
                    shutil.rmtree(directory)
                    raise ValueError('Не удалось запустить Creator')
                job = {'proc': proc, 'account_id': account.id, 'directory': directory, 'secrets': secrets, 'stopping': False,
                       'traffic_base_rx': s.rx_total, 'traffic_base_tx': s.tx_total,
                       'cookie_text': cookie_file.read_text(),
                       'previous_link': decrypt(s.link_secret) if s.link_secret else '',
                       'notify_on_link': notify_on_link}
                self.jobs[sid] = job
                s.state = 'starting'
                s.link_secret = None
                s.matrix_retry_exhausted = False
                db.commit()
            self.log(sid, 'Creator запущен; ожидаем создания звонка')
            job['task'] = asyncio.create_task(self.read(sid, job))

    def sync_cookies(self, job):
        path = job['directory'] / 'cookies.json'
        try:
            text = path.read_text()
            if text != job['cookie_text']:
                value = json.loads(text)
                with DB.begin() as db:
                    account = db.get(Account, job['account_id'])
                    previous = decrypt(account.secret) if account.secret else None
                    if isinstance(previous, dict) and 'browser_cookies' in previous:
                        if isinstance(value, dict):
                            value['browser_cookies'] = previous['browser_cookies']
                        else:
                            value = {'cookies': value, 'browser_cookies': previous['browser_cookies']}
                    account.secret = encrypt(value)
                    account.updated_at = now()
                job['cookie_text'] = text
                job['secrets'] += [c['value'] for c in (value if isinstance(value, list) else value.get('cookies', []))]
        except (OSError, ValueError):
            pass  # A platform may be midway through replacing its cookie file; retry next tick.

    async def read(self, sid, job):
        proc = job['proc']
        try:
            while raw := await proc.stdout.readline():
                line = raw.decode(errors='replace').strip()
                if 'WLP_METRIC:' in line:
                    try:
                        metric = json.loads(line.split('WLP_METRIC:', 1)[1])
                        if not all(isinstance(metric.get(k), int) and metric[k] >= 0 for k in ('rx_bytes', 'tx_bytes')):
                            continue
                        before = job.get('metrics')
                        moment = time.monotonic()
                        dt = moment - job.get('metric_at', moment)
                        if before and dt > 0:
                            for key in ('rx', 'tx'):
                                job[key+'_rate'] = max(0, metric[key+'_bytes'] - before[key+'_bytes']) / dt
                        metric['last_seen'] = (now() - timedelta(milliseconds=metric['peer_age_ms'])).isoformat() if 0 <= metric.get('peer_age_ms', -1) < 15000 else None
                        job['metrics'] = metric
                        job['metric_at'] = moment
                        with DB.begin() as db:
                            session = db.get(Session, sid)
                            if session:
                                session.rx_total = max(session.rx_total, job['traffic_base_rx'] + metric['rx_bytes'])
                                session.tx_total = max(session.tx_total, job['traffic_base_tx'] + metric['tx_bytes'])
                    except (ValueError, TypeError):
                        self.log(sid, 'Некорректный пакет телеметрии', 'warning')
                    continue
                destination = re.search(r'WLP_DESTINATION\s+(?:(\d+)\s+)?([^\s]+)', line)
                if destination:
                    if not self.domain_history_enabled:
                        continue
                    try:
                        connection_id = int(destination.group(1)) if destination.group(1) else None
                        host, port = destination.group(2).rsplit(':', 1)
                        host = host.rstrip('.').encode('idna').decode('ascii').lower()
                        if (len(host) <= 253 and re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', host)
                                and not host.endswith('.in-addr.arpa') and not host.endswith('.ip6.arpa')):
                            try:
                                ipaddress.ip_address(host)
                            except ValueError:
                                if port.isdigit() and 1 <= int(port) <= 65535:
                                    with DB.begin() as db:
                                        existing = db.scalar(select(Destination).where(
                                            Destination.session_id == sid,
                                            Destination.connection_id == connection_id
                                        ).order_by(Destination.id.desc()).limit(1)) if connection_id is not None else None
                                        if existing:
                                            existing.domain = host
                                            existing.port = int(port)
                                        else:
                                            db.add(Destination(session_id=sid, domain=host, port=int(port), connection_id=connection_id))
                    except (UnicodeError, ValueError):
                        pass
                    continue
                lower = line.lower()
                level = 'error' if any(x in lower for x in ('fatal', 'error', 'failed', 'panic', 'unauthorized')) else 'warning' if 'warning' in lower else 'info'
                self.log(sid, line, level, job['secrets'])
        except (ValueError, OSError):
            self.log(sid, 'Ошибка чтения вывода Creator', 'error')
            if proc.returncode is None:
                proc.kill()
        finally:
            code = await proc.wait()
            self.sync_cookies(job)
            with DB.begin() as db:
                s = db.get(Session, sid)
                s.state = 'interrupted' if job.get('shutdown') else 'stopped' if job['stopping'] else 'exited' if code == 0 else 'error'
            if job.get('shutdown'):
                self.log(sid, 'Сессия прервана при остановке backend или контейнера. Проверьте Docker/сервер и перезапустите звонок.', 'warning', job['secrets'])
            else:
                self.log(sid, f'Creator завершён, код {code}', 'info' if job['stopping'] or code == 0 else 'error', job['secrets'])
            shutil.rmtree(job['directory'], ignore_errors=True)
            self.jobs.pop(sid, None)
            if not job['stopping'] and not job.get('shutdown') and self.on_unexpected_exit:
                asyncio.create_task(self.on_unexpected_exit(sid))

    async def tick(self):
        for sid, job in list(self.jobs.items()):
            self.sync_cookies(job)
            path = job['directory'] / 'link.txt'
            if path.exists():
                lines = path.read_text().strip().splitlines()
                if lines:
                    link = lines[-1].strip()
                    link_ready = False
                    with DB.begin() as db:
                        s = db.get(Session, sid)
                        if s and s.state == 'starting':
                            s.state = 'running'
                            s.link_secret = encrypt(link)
                            link_ready = True
                    if link_ready and job['notify_on_link'] and self.on_link_ready:
                        self.on_link_ready(sid, link, job['previous_link'])
        
    async def stop(self, sid, interrupted=False):
        async with self.lock:
            job = self.jobs.get(sid)
            if not job:
                return
            job['stopping'] = True
            job['shutdown'] = interrupted
            proc = job['proc']
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(asyncio.shield(job['task']), timeout=12)
            except asyncio.TimeoutError:
                if proc.returncode is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                await job['task']

    async def shutdown(self):
        await asyncio.gather(*(self.stop(sid, interrupted=True) for sid in list(self.jobs)))
