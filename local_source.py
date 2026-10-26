"""Read only selected group tables from validated temporary snapshots.
The upstream decryptor verifies page HMACs and committed WAL frames.
Source databases are never written; only selected group text is persisted.
"""
import hashlib
import json
import logging
import sqlite3
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
import xml.etree.ElementTree as ET
import zstandard
import core
from vault import crypt

sys.path.insert(0,str(core.ROOT/'vendor/WeChatDataAnalysis/src'))
from wechat_decrypt_tool.wechat_decrypt import WeChatDatabaseDecryptor

def available():
    return (core.DATA/'local-source.json').exists() and (core.DATA/'wechat-key.dpapi').exists()

def decode(value):
    if value is None: return ''
    if isinstance(value,str): return value
    raw=bytes(value)
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=8*1024*1024)
    return raw.decode('utf8',errors='replace')

@contextmanager
def snapshot(src):
    key=crypt((core.DATA/'wechat-key.dpapi').read_bytes(),True).decode()
    with tempfile.TemporaryDirectory(prefix='read-',dir=core.DATA) as td:
        out=Path(td)/src.name
        reader=WeChatDatabaseDecryptor(key)
        ok=reader.decrypt_database(str(src),str(out))
        if not ok: raise ValueError('本地数据库快照校验失败；未导入不完整记录')
        if reader.last_result.get('source_changed_during_read'):
            raise ValueError('读取时微信数据库发生变化，请稍后重新同步')
        conn=sqlite3.connect(f'file:{out.as_posix()}?mode=ro',uri=True)
        conn.row_factory=sqlite3.Row
        try: yield conn
        finally: conn.close()

def read_group(group):
    settings=json.loads((core.DATA/'local-source.json').read_text('utf8'))
    store=Path(settings['db_storage'])
    if not store.is_dir(): raise ValueError('微信数据目录已移动，请重新初始化数据入口')
    with snapshot(store/'contact/contact.db') as c:
        if group['remote_id']:
            matches=c.execute("SELECT username,nick_name,remark FROM contact WHERE username=? AND username LIKE '%@chatroom'",(group['remote_id'],)).fetchall()
        else:
            matches=c.execute("SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom' AND (nick_name=? OR remark=?)",(group['name'],group['name'])).fetchall()
            if not matches:
                # Normalize only punctuation/width; still require exactly one match.
                name=unicodedata.normalize('NFKC',group['name'])
                matches=[r for r in c.execute("SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom'") if name in (unicodedata.normalize('NFKC',r['nick_name'] or ''),unicodedata.normalize('NFKC',r['remark'] or ''))]
        if len(matches)!=1: raise ValueError('本机联系人中未唯一匹配该群，请核对完整群名或稳定群 ID')
        remote=matches[0]['username']
        table='Msg_'+hashlib.md5(remote.encode()).hexdigest()
        contacts={r['username']:(r['remark'] or r['nick_name'] or r['username']) for r in c.execute('SELECT username,nick_name,remark FROM contact')}
    own=settings['account'].rsplit('_',1)[0]
    records=[]; skipped=0; count=0
    for path in sorted((store/'message').glob('message_*.db')):
        if not path.stem.removeprefix('message_').isdigit(): continue
        with snapshot(path) as c:
            if not c.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone(): continue
            names={r['rowid']:r['user_name'] for r in c.execute('SELECT rowid,user_name FROM Name2Id')}
            for row in c.execute('SELECT * FROM "'+table+'" ORDER BY create_time,local_id'):
                r=dict(row);count+=1
                kind=int(r['local_type']) & 0xffffffff
                if kind not in (1,49): skipped+=1;continue
                body=decode(r.get('compress_content') or r.get('message_content'))
                sender_id=names.get(r['real_sender_id'],'')
                if ':\n' in body:
                    prefix,remaining=body.split(':\n',1)
                    if prefix==sender_id or prefix.startswith('wxid_'):
                        sender_id=prefix;body=remaining
                source=decode(r.get('source'))
                mentions=[]
                if source.strip().startswith('<'):
                    try:
                        root=ET.fromstring(source)
                        ats=root.find('.//atuserlist')
                        if ats is not None and ats.text: mentions=[x.strip() for x in ats.text.split(',') if x.strip()]
                    except ET.ParseError: pass
                quote_id='';quote_kind=0
                if kind==49:
                    try:
                        xml=ET.fromstring(body)
                        title=xml.findtext('.//appmsg/title','')
                        url=xml.findtext('.//appmsg/url','')
                        quote=xml.findtext('.//refermsg/content','')
                        quote_id=xml.findtext('.//refermsg/svrid','')
                        quote_kind=int(xml.findtext('.//refermsg/type','0') or 0)
                        if quote_kind==3: quote='[引用图片]'
                        elif quote.lstrip().startswith('<'): quote='[引用非文本消息]'
                        body='\n'.join(x for x in (title,url,('引用：'+quote) if quote else '') if x)
                    except ET.ParseError:
                        skipped+=1;continue
                if not body.strip(): skipped+=1;continue
                mid=str(r['server_id']) if r['server_id'] else path.name+':'+str(r['local_id'])
                records.append(dict(id=mid,timestamp=r['create_time'],sender=contacts.get(sender_id,sender_id or '未知'),
                    sender_id=sender_id,text=body,is_self=sender_id==own,mentions=mentions,
                    quote_id=quote_id,quote_kind=quote_kind))
    return dict(remote_id=remote,bot_id=own,rows=records,total=count,skipped=skipped)
