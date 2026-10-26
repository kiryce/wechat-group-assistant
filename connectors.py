"""Read-only adapter for pinned WeChatDataAnalysis API; outgoing transport is fail-closed."""
import json
import threading
import time
from urllib.parse import urlparse
import requests
import core

SYNC_LOCK = threading.Lock()

def wda(path, **params):
    cfg=core.config()
    url=cfg['wda_url'].rstrip('/')
    if urlparse(url).hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('历史数据服务必须运行在本机')
    if cfg['account']: params['account']=cfg['account']
    try:
        r=requests.get(url+path, params=params,timeout=(3,60))
        if not r.ok: raise ValueError(f'历史数据接口 HTTP {r.status_code}')
        data=r.json()
        if data.get('status')=='error': raise ValueError('历史数据服务未就绪：请先在数据工具中加载微信账号')
        return data
    except requests.RequestException:
        raise ValueError('未连接本地历史服务（默认 10392 端口）。请先配置 WeChatDataAnalysis，或导入 JSON 记录。') from None

def discover():
    return [dict(remote_id=x['username'],name=x['name']) for x in wda('/api/chat/sessions',limit=2000,preview='none').get('sessions',[]) if x.get('isGroup')]

def normalize(m):
    content=m.get('content','')
    if m.get('url'): content+='\n'+str(m['url'])
    # First version indexes text, link cards and quotes; no pretend OCR/ASR.
    if m.get('renderType') in ('image','video','voice','emoji','file'): return None
    return dict(id=m.get('serverIdStr') or m.get('id'), timestamp=m['createTime'],
                sender=m.get('senderDisplayName') or m.get('senderUsername') or '未知',
                sender_id=m.get('senderUsername',''),text=content,is_self=m.get('isSent',False),
                mentions=m.get('atUsernames',[]))

def sync_group(gid, full=False, quiet=False):
    if not SYNC_LOCK.acquire(False): raise ValueError('已有同步任务进行中')
    try:
        with core.db() as c:
            row=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
            if not row: raise ValueError('群不存在')
            g=dict(row)
            if not g['enabled']: raise ValueError('群已停用')
            latest=c.execute('SELECT max(ts) FROM messages WHERE group_id=?',(gid,)).fetchone()[0]
        import local_source
        if local_source.available():
            result=local_source.read_group(g)
            new_ids=core.ingest(gid,result['rows'],enqueue=bool(not full and g['live_after']))
            with core.db() as c:
                c.execute('UPDATE groups SET remote_id=?,synced_at=?,sync_error=NULL WHERE id=?',
                          (result['remote_id'],core.now().isoformat(),gid))
            if not core.config()['bot_id']: core.save_config({'bot_id':result['bot_id']})
            if new_ids or not quiet:
                core.event(f'{g["name"]}：本地同步新增 {len(new_ids)} 条；当前快照 {result["total"]} 条，跳过 {result["skipped"]} 条非文本/不支持消息')
            return dict(added=len(new_ids),total=result['total'],skipped=result['skipped'],source='local')
        remote=g['remote_id']
        if not remote:
            candidates=[x for x in discover() if x['name']==g['name']]
            if len(candidates)!=1: raise ValueError('未唯一匹配到群名，请在设置中填入该群稳定 ID')
            remote=candidates[0]['remote_id']
            with core.db() as c: c.execute('UPDATE groups SET remote_id=? WHERE id=?',(remote,gid))
        offset=0; count=0; new_ids=[]; completed=False
        for _ in range(400):
            batch=wda('/api/chat/messages',username=remote,limit=500,offset=offset,order='desc').get('messages',[])
            rows=[normalize(m) for m in batch]
            added=core.ingest(gid,[m for m in rows if m],enqueue=bool(not full and g['live_after']))
            new_ids+=added; count+=len(added); offset+=len(batch)
            if not batch or len(batch)<500 or (not full and latest and min(m['createTime'] for m in batch)<latest-600):
                completed=True; break
        if not completed: raise ValueError('达到单次 20 万条同步上限；本次已保存的记录仍保留，历史完整性尚未验证')
        with core.db() as c:
            c.execute('UPDATE groups SET synced_at=?,sync_error=NULL WHERE id=?',(core.now().isoformat(),gid))
        core.event(f'{g["name"]}：同步新增 {count} 条文本/链接记录')
        return dict(added=count)
    except Exception as exc:
        with core.db() as c: c.execute('UPDATE groups SET sync_error=? WHERE id=?',(str(exc),gid))
        raise
    finally: SYNC_LOCK.release()

def process_incoming(mid):
    cfg=core.config()
    if cfg['paused'] or not cfg['bot_id']: return
    with core.db() as c:
        row=c.execute('SELECT m.*,g.enabled,g.reply_enabled,g.live_after FROM messages m JOIN groups g ON g.id=m.group_id WHERE m.id=?',(mid,)).fetchone()
        if not row: return
        r=dict(row)
        if not r['enabled'] or not r['reply_enabled'] or r['is_self'] or r['sender_id']==cfg['bot_id']: return
        if r['ts']<=r['live_after'] or r['ts']<int(core.now().timestamp())-600: return
        if cfg['bot_id'] not in json.loads(r['mentions']): return
        if not c.execute('INSERT OR IGNORE INTO inbox VALUES (?, ?, NULL)',(mid,'processing')).rowcount: return
        recent=c.execute("SELECT count(*) FROM outbox WHERE group_id=? AND created>?",(r['group_id'],(core.now()-core.timedelta(minutes=1)).isoformat())).fetchone()[0]
        if recent>=3:
            c.execute("UPDATE inbox SET state='rate_limited' WHERE message_id=?",(mid,)); return
    try:
        with core.db() as c:
            quote=c.execute('SELECT * FROM message_quotes WHERE message_id=?',(mid,)).fetchone()
        if quote and quote['quote_kind']==3:
            import quoted_images
            try:
                picture=quoted_images.load(r['group_id'],quote['quote_id'])
                result=core.answer(r['group_id'],r['text'],image=picture)['answer']
            except ValueError as exc:
                result='暂时没能读取这张引用图片：'+str(exc)+' 🖼️'
        elif '总结' in r['text'] and any(word in r['text'] for word in ('群聊','群消息','聊天记录','上周','本周','周报')):
            a,b=core.time_range(r['text'])
            if a==0: a,b=core.time_range('上周')
            result=core.report(r['group_id'],a,b)['content']
        else:
            response=core.answer(r['group_id'],r['text'])
            result=response['answer']
        result='【群知答疑】🤖\n'+core.wechat_plain_text(core.clean_reply(result))
        if len(result)>1750:
            result=result[:1660]+'\n（内容较长，完整内容请在本地管理页查看。）'
        with core.db() as c:
            c.execute('INSERT OR IGNORE INTO outbox(group_id,content,kind,ref,created) VALUES (?,?,?,?,?)',
                      (r['group_id'],result,'answer',mid,core.now().isoformat()))
            c.execute("UPDATE inbox SET state='draft' WHERE message_id=?",(mid,))
        core.event('已生成 @答疑并进入发送队列')
    except Exception as e:
        with core.db() as c: c.execute("UPDATE inbox SET state='failed',error=? WHERE message_id=?",(str(e),mid))
        core.event('答疑生成失败：'+str(e),'error')

def scheduler_tick(reference=None):
    cfg=core.config(); n=reference or core.now()
    if cfg['paused']: return
    with core.db() as c:
        groups=[dict(x) for x in c.execute('SELECT * FROM groups WHERE enabled=1')]
    for g in groups:
        import message_events
        if cfg['sync_enabled'] and not message_events.STATUS['running']:
            try: sync_group(g['id'])
            except Exception as e: core.event(str(e),'error')
        if n.weekday()==cfg['weekly_day'] and n.strftime('%H:%M')>=cfg['weekly_time']:
            a,b=core.time_range('上周',n)
            with core.db() as c:
                existing=c.execute('SELECT 1 FROM reports WHERE group_id=? AND start=? AND end=?',(g['id'],a,b)).fetchone()
                has_messages=c.execute('SELECT 1 FROM messages WHERE group_id=? AND ts>=? AND ts<? LIMIT 1',(g['id'],a,b)).fetchone()
            if not existing and has_messages:
                try:
                    core.report(g['id'],a,b)
                    core.event(g['name']+'：周报已生成，可在本地预览；未发送')
                except Exception as e: core.event('周报生成失败：'+str(e),'error')

def start_scheduler():
    with core.db() as c:
        c.execute("UPDATE outbox SET status='uncertain',error='程序曾在发送途中退出，须人工核对，禁止自动重试' WHERE status='sending'")
    import message_events
    message_events.start()
    def loop():
        while True:
            try: scheduler_tick()
            except Exception: core.event('后台任务异常，详见连接状态','error')
            time.sleep(60)
    threading.Thread(target=loop,daemon=True).start()
    def send_loop():
        import transport
        while True:
            try: transport.deliver_pending()
            except Exception as exc: core.event('发送工作线程：'+type(exc).__name__,'error')
            time.sleep(5)
    threading.Thread(target=send_loop,daemon=True).start()
