from types import SimpleNamespace
import time
import core
import message_events as events


def test_wal_events_only():
    events.CHANGED.clear()
    h = events.Handler()
    h.on_any_event(SimpleNamespace(is_directory=False,event_type='modified',src_path='message_0.db-wal'))
    assert events.CHANGED.is_set()
    events.CHANGED.clear()
    for path in ['contact.db','message_0.db-shm','message_0.db.backup','unrelated.txt']:
        h.on_any_event(SimpleNamespace(is_directory=False,event_type='modified',src_path=path))
    assert not events.CHANGED.is_set()


def test_durable_queue_deduplicates(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path)
    core.init_db()
    mid=core.ingest('demo',[dict(id='x',text='question',sender='test',timestamp=core.now().isoformat())])[0]
    events.enqueue(mid);events.enqueue(mid)
    with core.db() as c:
        assert c.execute('SELECT count(*) FROM incoming_jobs').fetchone()[0]==1
        assert c.execute('SELECT state FROM incoming_jobs').fetchone()[0]=='queued'


def test_sync_queues_without_model(tmp_path,monkeypatch):
    import connectors
    import local_source
    monkeypatch.setattr(core,'DATA',tmp_path)
    core.init_db()
    with core.db() as c: c.execute('UPDATE groups SET live_after=1,reply_enabled=1')
    monkeypatch.setattr(local_source,'available',lambda:True)
    monkeypatch.setattr(local_source,'read_group',lambda g:dict(remote_id='g@chatroom',bot_id='self',total=1,skipped=0,
        rows=[dict(id='x',text='question',sender='test',timestamp=core.now().isoformat(),mentions=['self'])]))
    monkeypatch.setattr(core,'answer',lambda *a: (_ for _ in ()).throw(AssertionError('sync must not call model')))
    connectors.sync_group('demo',quiet=True)
    connectors.sync_group('demo',quiet=True)
    with core.db() as c:
        assert c.execute('SELECT count(*) FROM incoming_jobs').fetchone()[0]==1


def test_native_directory_notification(tmp_path):
    from watchdog.observers import Observer
    events.CHANGED.clear()
    observer=Observer()
    observer.schedule(events.Handler(),str(tmp_path),recursive=False)
    observer.start()
    try:
        (tmp_path/'message_0.db-wal').write_bytes(b'test notification only')
        assert events.CHANGED.wait(5), 'OS notification was not delivered'
    finally:
        observer.stop();observer.join(2)
        events.CHANGED.clear()


def test_bad_group_does_not_block_other_groups(tmp_path,monkeypatch):
    import connectors
    import pytest
    monkeypatch.setattr(core,'DATA',tmp_path)
    core.init_db();core.save_config({'paused':False,'sync_enabled':True})
    with core.db() as c:c.execute("INSERT INTO groups(id,name) VALUES ('second','second')")
    calls=[]
    def sync(gid,**kw):
        calls.append(gid)
        if gid=='demo':raise ValueError('first group unavailable')
    monkeypatch.setattr(connectors,'sync_group',sync)
    with pytest.raises(ValueError):events.reconcile()
    assert calls==['demo','second']
