import asyncio
import base64
import importlib
import io
import json
import zipfile
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-password-with-32-characters')
    monkeypatch.setenv('RUNTIME_DIR', str(tmp_path/'runtime'))
    monkeypatch.setenv('CREATOR_BIN_DIR', str(tmp_path/'bin'))
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    from app import main, runner, db
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(main, 'DB', factory)
    monkeypatch.setattr(runner, 'DB', factory)
    main.attempts.clear()
    with TestClient(main.app) as c:
        yield c, tmp_path, factory
    engine.dispose()


def login(c):
    r=c.post('/api/login', json={'password':'test-password-with-32-characters'})
    assert r.status_code == 200
    assert 'HttpOnly' in r.headers['set-cookie']


def account(c):
    a=c.post('/api/accounts',json={'name':'Test','platform':'telemost'}).json()
    assert c.put(f'/api/accounts/{a["id"]}/cookies',json=[
        {'name':'Session_id','value':'test-session-cookie'},
        {'name':'token','value':'super-private-cookie'},
    ]).status_code==200
    return a


def test_auth_csrf_and_no_secret_echo(client):
    c,_,factory=client
    assert c.get('/api/state').status_code==401
    assert c.post('/api/login', json={'password':'bad'}).status_code==401
    login(c)
    assert c.post('/api/accounts',json={'name':'Bad','platform':'vk'},headers={'Origin':'https://evil.example'}).status_code==403
    a=account(c)
    assert 'super-private-cookie' not in c.get('/api/state').text
    from app.db import Account
    with factory() as db:
        assert 'super-private-cookie' not in db.get(Account,a['id']).secret
    r=c.post('/api/login',json={'password':{'private':'do-not-echo'}})
    assert r.status_code==422 and 'do-not-echo' not in r.text
    c.post('/api/logout',json={})
    assert c.get('/api/state').status_code==401


def test_cookie_validation_and_portal(client):
    c,_,_=client;login(c)
    assert c.post('/api/accounts',json={'name':'Bad','platform':'bitrix','portal':'http://127.0.0.1'}).status_code==400
    a=account(c)
    assert c.put(f'/api/accounts/{a["id"]}/cookies',json={'cookies':[{'name':'x'}]}).status_code==400
    assert c.put(f'/api/accounts/{a["id"]}/cookies',json=[]).status_code==400
    assert c.put(f'/api/accounts/{a["id"]}/cookies',json=[{'name':'token','value':'x'}]).status_code==400


def test_official_creator_zip_import(client):
    c,_,factory=client;login(c)
    a=c.post('/api/accounts',json={'name':'Yandex','platform':'telemost'}).json()
    exported=[{'name':'Session_id','value':'official-session'}, {'name':'yandex_login','value':'account'}]
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('cookies-yandex.json',json.dumps(exported))
    response=c.put(f'/api/accounts/{a["id"]}/cookies',json={
        'export_zip_base64':base64.b64encode(archive.getvalue()).decode(),
    })
    assert response.status_code==200
    from app.db import Account
    from app.security import decrypt
    with factory() as db:
        assert decrypt(db.get(Account,a['id']).secret)==exported


def test_real_subprocess_metrics_stop_rotation_and_logs(client):
    c,tmp,factory=client;login(c);a=account(c)
    binary=tmp/'bin'/'headless-telemost-creator';binary.parent.mkdir()
    binary.write_text('''#!/usr/bin/env python3
import argparse,json,time
p=argparse.ArgumentParser();p.add_argument('--cookies');p.add_argument('--write-file');p.add_argument('--resources');a=p.parse_args()
open(a.write_file,'w').write('https://telemost.yandex.ru/j/private-room')
open(a.cookies,'w').write(json.dumps([{'name':'token','value':'rotated-secret'}]))
print('ERROR token=super-private-cookie https://private.example/secret',flush=True)
print('WLP_METRIC:'+json.dumps({'rx_bytes':42,'tx_bytes':84,'peer_age_ms':5,'active_connections':1}),flush=True)
time.sleep(30)
''')
    binary.chmod(0o755)
    s=c.post('/api/sessions',json={'name':'Session','account_id':a['id']}).json()['id']
    assert c.post(f'/api/sessions/{s}/start',json={}).status_code==200
    assert c.post(f'/api/sessions/{s}/start',json={}).status_code==400
    assert c.put(f'/api/accounts/{a["id"]}/cookies',json=[]).status_code==409
    async def check():
        from app import main
        for _ in range(100):
            await main.runner.tick()
            if main.runner.snapshot(s)['rx_bytes']==42:
                return
            await asyncio.sleep(.02)
        raise AssertionError('No metrics from subprocess')
    c.portal.call(check)
    state=c.get('/api/state').json()['sessions'][0]
    assert state['peer']=='recent' and state['active_connections']==1
    assert state['state']=='running'
    assert c.get(f'/api/sessions/{s}/link').json()['link'].endswith('private-room')
    errors=c.get('/api/logs?errors=true').text
    assert 'ERROR' in errors and 'super-private-cookie' not in errors and 'private.example' not in errors
    assert c.post(f'/api/sessions/{s}/stop',json={}).status_code==200
    assert c.get('/api/state').json()['sessions'][0]['state']=='stopped'
    assert not (tmp/'runtime'/s).exists()
    from app.db import Account
    from app.security import decrypt
    with factory() as db:
        assert decrypt(db.get(Account,a['id']).secret)[0]['value']=='rotated-secret'
    assert c.request('DELETE',f'/api/sessions/{s}',json={}).status_code==200


def test_redact():
    from app.security import redact
    assert 'secret' not in redact('password=secret')
    assert 'cookie-value' not in redact('hello cookie-value', ['cookie-value'])
    assert 'abcdef' not in redact('[auth] returned abcdef')


def test_browser_save_requires_auth_and_preserves_bitrix_hosts(client):
    c,_,factory=client; login(c)
    a=c.post('/api/accounts',json={'name':'Bitrix','platform':'bitrix','portal':'https://example.bitrix24.ru'}).json()
    class Context:
        def __init__(self):
            self.auth=False
            self.pages=[]
            self.closed=False
        async def cookies(self):
            return [{'name':'BITRIX_SM_UIDH' if self.auth else 'analytics', 'value':'secure-value', 'domain':'.example.bitrix24.ru'},
                    {'name':'auth','value':'net-secret','domain':'.auth2.bitrix24.net'}]
        async def close(self):
            self.closed=True
    ctx=Context()
    async def inject():
        from app.main import browsers
        browsers.items[a['id']]={'context':ctx,'page':None,'at':0,'capture':{},'lock':asyncio.Lock()}
    c.portal.call(inject)
    assert c.post(f'/api/accounts/{a["id"]}/browser/save',json={}).status_code==400
    ctx.auth=True
    assert c.post(f'/api/accounts/{a["id"]}/browser/save',json={}).status_code==200
    assert ctx.closed
    from app.db import Account
    from app.security import decrypt
    with factory() as db:
        payload=decrypt(db.get(Account,a['id']).secret)
        assert payload['portal']=='https://example.bitrix24.ru'
        assert payload['cookies'][1]['host']=='https://auth2.bitrix24.net'


def test_browser_blocks_private_targets():
    from app.browser import public_url
    assert asyncio.run(public_url('http://127.0.0.1/')) is False
    assert asyncio.run(public_url('file:///etc/passwd')) is False
    assert asyncio.run(public_url('http://169.254.169.254/')) is False
