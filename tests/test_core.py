import json
from datetime import datetime
import pytest
import core
import connectors

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path)
    core.init_db()
    with core.db() as c:
        c.execute("INSERT INTO groups(id,name) VALUES ('other','隔离群')")

def msg(text,mid='1',sender='甲',ts='2026-10-20T10:00:00+08:00',**kw):
    return dict(id=mid,text=text,sender=sender,timestamp=ts,**kw)

def test_import_dedup_and_group_boundary():
    assert len(core.ingest('demo',[msg('项目预算确定为一万元')]))==1
    assert not core.ingest('demo',[msg('项目预算确定为一万元')])
    core.ingest('other',[msg('项目预算确定为十万元')])
    rows=core.retrieve('demo','项目预算是多少',end=2000000000,start=0)
    assert rows and all(x['group_id']=='demo' for x in rows)
    assert '十万元' not in str(rows)

def test_import_transaction_invalid_timestamp():
    with pytest.raises(ValueError): core.ingest('demo',[msg('正常'),msg('错误','2',ts='bad')])
    with core.db() as c: assert c.execute('SELECT count(*) FROM messages').fetchone()[0]==0

def test_week_boundary():
    ref=datetime(2026,10,26,9,tzinfo=core.TZ)
    a,b=core.time_range('上周',ref)
    assert a==core.timestamp('2026-10-19T00:00:00+08:00')
    assert b==core.timestamp('2026-10-26T00:00:00+08:00')
    assert core.time_range('最近三天',ref)[0]==int(ref.timestamp())-3*86400

def test_no_evidence_can_answer_general_question(monkeypatch):
    calls=[]
    def model(system,prompt,**kw):
        calls.append((system,prompt));return '当然可以 👨‍💻\n```python\na = [1]\nprint(a[0])\n```'
    monkeypatch.setattr(core,'llm',model)
    result=core.answer('demo','写一个快速排序算法')
    assert calls and '只能根据提供的记录' in calls[0][0]
    assert 'a = [1]' in result['answer'] and result['sources']==[]

def test_citations_are_from_current_group(monkeypatch):
    core.ingest('demo',[msg('项目预算一万元')])
    monkeypatch.setattr(core,'llm',lambda *a,**kw:'预算一万元 [1]，错误编号 [999]')
    result=core.answer('demo','项目预算')
    assert [s['number'] for s in result['sources']]==[1]
    assert '[1]' not in result['answer'] and '[999]' not in result['answer']

def test_clean_reply_preserves_code():
    raw='可以 👨‍💻 [26]\n```python\nx = [1]\ny = a[26]\n```\n使用 `a[26]`。\n\n依据：\n[26] 原消息'
    cleaned=core.clean_reply(raw)
    assert '依据' not in cleaned and '👨‍💻 [26]' not in cleaned
    assert 'x = [1]' in cleaned and 'y = a[26]' in cleaned and '`a[26]`' in cleaned

def test_report_covers_all_chunks_and_end_exclusive(monkeypatch):
    core.ingest('demo',[msg('项目预算'+str(i)+'。'*3000,str(i)) for i in range(14)]+[msg('不应收录','outside',ts='2026-10-26T00:00:00+08:00')])
    calls=[]
    def fake(system,prompt,*args): calls.append(prompt); return '片段总结'
    monkeypatch.setattr(core,'llm',fake)
    result=core.report('demo',core.timestamp('2026-10-19'),core.timestamp('2026-10-26'))
    combined='\n'.join(calls)
    assert all('项目预算'+str(i) in combined for i in range(14))
    assert '不应收录' not in combined
    assert result['status']=='preview'

def test_at_filter_skips_self_and_old(monkeypatch):
    core.save_config({'paused':False,'bot_id':'self'})
    with core.db() as c: c.execute("UPDATE groups SET reply_enabled=1,live_after=1 WHERE id='demo'")
    monkeypatch.setattr(core,'answer',lambda *a:pytest.fail('must not reply'))
    for m in [msg('预算',mentions=['self']),msg('预算','2',ts=core.now().isoformat(),is_self=True,mentions=['self']),msg('预算','3',ts=core.now().isoformat(),mentions=['someone-else'])]:
        for mid in core.ingest('demo',[m]): connectors.process_incoming(mid)
    with core.db() as c: assert c.execute('SELECT count(*) FROM outbox').fetchone()[0]==0

def test_real_at_only_once(monkeypatch):
    core.save_config({'paused':False,'bot_id':'self'})
    with core.db() as c: c.execute("UPDATE groups SET reply_enabled=1,live_after=1 WHERE id='demo'")
    monkeypatch.setattr(core,'answer',lambda *a:dict(answer='回答',sources=[]))
    mid=core.ingest('demo',[msg('预算',ts=core.now().isoformat(),mentions=['self'])])[0]
    connectors.process_incoming(mid);connectors.process_incoming(mid)
    with core.db() as c: assert c.execute('SELECT count(*) FROM outbox').fetchone()[0]==1

def test_scheduler_once_and_preview(monkeypatch):
    core.save_config({'paused':False})
    core.ingest('demo',[msg('周五上线')])
    monkeypatch.setattr(core,'llm',lambda *a:'周报')
    ref=datetime(2026,10,26,9,1,tzinfo=core.TZ)
    connectors.scheduler_tick(ref);connectors.scheduler_tick(ref)
    with core.db() as c:
        assert c.execute('SELECT count(*) FROM reports').fetchone()[0]==1
        assert c.execute('SELECT status FROM reports').fetchone()[0]=='preview'

def test_http_guards():
    import app
    client=app.app.test_client(); base='http://127.0.0.1:8765'
    assert client.get('/api/state',base_url=base).status_code==403
    headers={'X-Local-Token':app.TOKEN}
    assert client.get('/api/state',base_url=base,headers=headers).status_code==200
    assert client.post('/api/config',base_url=base,headers=headers|{'Origin':'https://evil.test'},json={'paused':False}).status_code==403
    assert client.get('/',base_url='http://evil.test:8765').status_code==403
    assert client.post('/api/groups',base_url=base,headers=headers,json={'name':'群','weekly_send':True}).status_code==400
