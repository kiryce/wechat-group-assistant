import core
import transport
import pytest

def test_title_exact_and_member_count():
    assert transport.title_matches('示例群(9)','示例群')
    assert not transport.title_matches('示例社区 内部群(9)','示例群')
    assert not transport.title_matches('示例群2(9)','示例群')

@pytest.fixture
def queue(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path);core.init_db()
    core.save_config({'paused':False,'transport':'foreground_ocr'})
    with core.db() as c:
        c.execute("UPDATE groups SET reply_enabled=1")
        c.execute("INSERT INTO outbox(group_id,content,kind,ref,created) VALUES ('demo','test','answer','m',?)",(core.now().isoformat(),))

def test_wrong_window_stays_pending(queue,monkeypatch):
    def blocked(*args): raise transport.NotReady('wrong window')
    monkeypatch.setattr(transport,'send_once',blocked)
    transport.deliver_pending()
    with core.db() as c: assert c.execute('SELECT status FROM outbox').fetchone()[0]=='draft'

def test_uncertain_send_never_retries(queue,monkeypatch):
    calls=[]
    def ambiguous(*args): calls.append(1);raise transport.Uncertain('unknown')
    monkeypatch.setattr(transport,'send_once',ambiguous)
    transport.deliver_pending();transport.deliver_pending()
    assert len(calls)==1
    with core.db() as c: assert c.execute('SELECT status FROM outbox').fetchone()[0]=='uncertain'

def test_pause_blocks_send(queue,monkeypatch):
    core.save_config({'paused':True})
    monkeypatch.setattr(transport,'send_once',lambda *args:pytest.fail('paused'))
    transport.deliver_pending()


def test_verification_preserves_concurrent_incoming(queue,monkeypatch):
    import local_source
    monkeypatch.setattr(transport,'send_once',lambda *a:True)
    monkeypatch.setattr(transport.time,'sleep',lambda *a:None)
    ts=int(core.now().timestamp())
    rows=[dict(id='out',text='test',sender='self',is_self=True,timestamp=ts),
          dict(id='in',text='new question',sender='member',is_self=False,timestamp=ts)]
    monkeypatch.setattr(local_source,'read_group',lambda *a:dict(rows=rows))
    transport.deliver_pending()
    with core.db() as c:
        assert c.execute('SELECT status FROM outbox').fetchone()[0]=='sent'
        assert c.execute("SELECT count(*) FROM incoming_jobs j JOIN messages m ON m.id=j.message_id WHERE m.text='new question'").fetchone()[0]==1
