"""Authenticated loopback native bridge. No mouse, keyboard or UI calls."""
import json
import os
import secrets
import subprocess
from pathlib import Path
import requests
import core
from vault import crypt

URL='http://127.0.0.1:30001'

def token():
    path=core.DATA/'bridge-token.dpapi'
    if not path.exists():
        path.write_bytes(crypt(secrets.token_urlsafe(48).encode()))
    return crypt(path.read_bytes(),True).decode()

def session():
    s=requests.Session();s.trust_env=False
    s.headers['Authorization']='Bearer '+token()
    return s

def status():
    with session() as s:
        r=s.get(URL+'/status',timeout=(2,3),allow_redirects=False)
        r.raise_for_status()
        return r.json()

def update_allowlist():
    with core.db() as c:
        groups=[r[0] for r in c.execute("SELECT remote_id FROM groups WHERE enabled=1 AND reply_enabled=1 AND remote_id LIKE '%@chatroom'")]
    p=core.DATA/'bridge-allowlist.json'
    tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'groups':groups}),encoding='utf8');tmp.replace(p)
    return p

def send(group,text):
    from transport import NotReady,Uncertain
    try:
        info=status()
    except (requests.RequestException,ValueError):
        raise NotReady('后台发送模块未连接；不会改用前台输入') from None
    if not info.get('compatible') or info.get('version')!='4.1.10.27':
        raise NotReady('后台模块与微信版本不匹配，已停止发送')
    if not group.get('remote_id','').endswith('@chatroom'):
        raise NotReady('未绑定稳定群 ID')
    if not text.strip() or len(text)>1800: raise NotReady('发送正文长度无效')
    update_allowlist()
    # Any error after POST starts is ambiguous: never retry automatically.
    try:
        with session() as s:
            r=s.post(URL+'/send',json={'to':group['remote_id'],'text':text},timeout=(2,15),allow_redirects=False)
            if r.status_code in (403,409): raise NotReady('后台模块拒绝发送：群白名单或版本不匹配')
            r.raise_for_status()
            if r.json().get('accepted') is not True: raise ValueError('not accepted')
    except NotReady: raise
    except (requests.RequestException,ValueError):
        raise Uncertain('后台调用结果不确定，请核对原群；不会自动重发') from None

def launch(executable):
    exe=Path(executable).resolve()
    if not exe.is_file(): raise ValueError('兼容版客户端不存在')
    env=os.environ.copy()
    env['WX_ASSIST_TOKEN']=token()
    env['WX_ASSIST_ALLOWLIST']=str(update_allowlist())
    return subprocess.Popen([str(exe)],cwd=exe.parent,env=env)

if __name__=='__main__':
    import sys
    core.init_db()
    if len(sys.argv)==2: launch(sys.argv[1])
    else: print(status())
