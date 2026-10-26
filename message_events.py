"""Windows directory notifications -> durable per-message work queue.

Notifications indicate database writes, not a WeChat network callback. A short
debounce allows committed WAL frames to settle; periodic reconciliation recovers
missed notifications. Model calls never run on the observer/sync thread.
"""
import json
import re
import threading
import time
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
import core

CHANGED = threading.Event()
STATUS = {'running': False, 'mode': 'windows_directory_events', 'error': None,
          'last_event': None, 'last_sync': None}


def relevant(path):
    return bool(re.fullmatch(r'message_\d+\.db(?:-wal)?', Path(path).name))


class Handler(FileSystemEventHandler):
    def on_any_event(self, event):
        if not event.is_directory and event.event_type in ('modified', 'created', 'moved', 'deleted'):
            if relevant(event.src_path) or relevant(getattr(event, 'dest_path', '')):
                STATUS['last_event'] = core.now().isoformat()
                CHANGED.set()


def enqueue(mid):
    # Keep even messages ingested by manual synchronization eligible for dispatch.
    with core.db() as c:
        c.execute('INSERT OR IGNORE INTO incoming_jobs(message_id,created) VALUES (?,?)',
                  (mid, core.now().isoformat()))


def reconcile():
    import connectors
    cfg = core.config()
    if cfg['paused'] or not cfg['sync_enabled']:
        return
    with core.db() as c:
        groups = [r['id'] for r in c.execute('SELECT id FROM groups WHERE enabled=1')]
    errors=[]
    for gid in groups:
        try:
            connectors.sync_group(gid, quiet=True)
        except Exception as exc:
            errors.append(str(exc))
    STATUS['last_sync'] = core.now().isoformat()
    if errors:
        raise ValueError('; '.join(errors))


def worker():
    import connectors
    while True:
        try:
            if not core.config()['paused']:
                with core.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    row = c.execute("SELECT message_id FROM incoming_jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                    if row:
                        c.execute("UPDATE incoming_jobs SET state='processing' WHERE message_id=?", (row[0],))
                if row:
                    connectors.process_incoming(row[0])
                    with core.db() as c:
                        c.execute("UPDATE incoming_jobs SET state='done' WHERE message_id=?", (row[0],))
                    continue
        except Exception as exc:
            core.event('入站处理失败：' + type(exc).__name__, 'error')
        time.sleep(.25)


def start():
    with core.db() as c:
        # A crashed model call may be recomputed; outbox ref prevents duplicate sends.
        c.execute("DELETE FROM inbox WHERE state='processing' AND message_id IN (SELECT message_id FROM incoming_jobs WHERE state='processing')")
        c.execute("UPDATE incoming_jobs SET state='queued' WHERE state='processing'")
    for i in range(2):
        threading.Thread(target=worker, name=f'answer-{i}', daemon=True).start()

    def observe():
        observer = None
        try:
            settings = json.loads((core.DATA/'local-source.json').read_text('utf8'))
            folder = Path(settings['db_storage'])/'message'
            observer = Observer()
            observer.schedule(Handler(), str(folder), recursive=False)
            observer.start()
            STATUS.update(running=True, error=None)
            core.event('已启动数据库变更事件监听：真实 @消息进入独立答疑队列')
            CHANGED.set()
            while True:
                signalled = CHANGED.wait(30)  # recovery sweep, not normal delivery latency
                CHANGED.clear()
                if signalled:
                    time.sleep(.4)
                try:
                    reconcile()
                    STATUS['error'] = None
                except Exception as exc:
                    STATUS['error'] = str(exc)
                    CHANGED.set()
                    time.sleep(2)
        except Exception as exc:
            STATUS.update(running=False, error=str(exc))
            core.event('事件监听未启动：'+str(exc), 'error')
        finally:
            if observer:
                observer.stop()
                observer.join(2)
    threading.Thread(target=observe, name='message-events', daemon=True).start()
