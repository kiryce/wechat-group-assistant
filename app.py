import json
import secrets
import threading
import uuid
from urllib.parse import urlparse
from flask import Flask, jsonify, request, render_template, abort
import core
import connectors

app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=20*1024*1024
TOKEN=secrets.token_urlsafe(32)
TASKS={}
TASK_LOCK=threading.Lock()
AI_LOCK=threading.Lock()
core.init_db()

@app.before_request
def local_only():
    if request.host not in ('127.0.0.1:8765','localhost:8765'): abort(403)
    if request.path.startswith('/api/') and request.headers.get('X-Local-Token')!=TOKEN: abort(403)
    origin=request.headers.get('Origin')
    if origin and origin not in ('http://127.0.0.1:8765','http://localhost:8765'): abort(403)

@app.after_request
def headers(resp):
    resp.headers['Cache-Control']='no-store'
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return resp

@app.errorhandler(ValueError)
def bad_input(e): return jsonify(error=str(e)),400

@app.errorhandler(Exception)
def unexpected(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e,HTTPException): return jsonify(error=e.description),e.code
    core.event('操作失败：'+type(e).__name__,'error')
    return jsonify(error='操作失败，请检查输入格式或连接状态（'+type(e).__name__+'）'),500

@app.get('/')
def index(): return render_template('index.html',token=TOKEN)

def task(fn,*args):
    with TASK_LOCK:
        if any(v['status']=='running' for v in TASKS.values()):
            raise ValueError('已有后台任务，请等待完成后再操作')
        tid=uuid.uuid4().hex
        TASKS[tid]=dict(status='running')
    def run():
        try:
            with AI_LOCK: result=fn(*args)
            with TASK_LOCK: TASKS[tid]=dict(status='done',result=result)
        except Exception as e:
            with TASK_LOCK: TASKS[tid]=dict(status='failed',error=str(e) if isinstance(e,ValueError) else type(e).__name__)
    threading.Thread(target=run,daemon=True).start()
    return jsonify(task_id=tid)

@app.get('/api/tasks/<tid>')
def task_status(tid):
    with TASK_LOCK: return jsonify(TASKS.get(tid,dict(status='failed',error='任务不存在或服务已重启')))

@app.get('/api/state')
def state():
    with core.db() as c:
        groups=[dict(x) for x in c.execute('''SELECT g.*,count(m.id) AS message_count,min(m.ts) AS first_ts,max(m.ts) AS last_ts
          FROM groups g LEFT JOIN messages m ON m.group_id=g.id GROUP BY g.id''')]
        reports=[dict(x) for x in c.execute('SELECT r.*,g.name FROM reports r JOIN groups g ON g.id=r.group_id ORDER BY created DESC LIMIT 50')]
        outbox=[dict(x) for x in c.execute('SELECT o.*,g.name FROM outbox o JOIN groups g ON g.id=o.group_id ORDER BY o.id DESC LIMIT 50')]
        events=[dict(x) for x in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 25')]
    import local_source
    import message_events
    cfg=core.config()
    ready=False
    detail='发送已关闭；入站监听与答疑草稿独立运行。'
    if cfg['transport']=='background_native':
        import background_transport
        try:
            bridge=background_transport.status()
            ready=bridge.get('compatible',False)
            detail='后台发送模块已连接（'+bridge.get('version','未知版本')+'）。请保持微信已登录；发送不使用鼠标、键盘或聊天窗口，按稳定群 ID 投递并查原群记录核验。' if ready else '后台模块与当前客户端版本不匹配，发送已阻止。'
        except Exception:
            detail='后台发送模块未连接。请通过“启动后台微信.cmd”打开兼容客户端并登录；不会自动退回前台操作。'
    elif cfg['transport']=='foreground_ocr':
        ready=True
        detail='旧前台模式：必须打开目标群、输入框为空、键鼠空闲。'
    return jsonify(config=core.config(),key_saved=(core.DATA/'api-key.dpapi').exists(),local_source_ready=local_source.available(),groups=groups,reports=reports,
        listener=dict(message_events.STATUS),outbox=outbox,events=events,transport=dict(ready=ready,detail=detail))

@app.post('/api/config')
def save_config():
    d=request.get_json()
    url=d.get('base_url',core.config()['base_url'])
    p=urlparse(url)
    if p.scheme!='https' and p.hostname not in ('127.0.0.1','localhost'): raise ValueError('模型地址必须使用 HTTPS')
    if p.username or p.password: raise ValueError('地址中不能嵌入凭据')
    if d.get('transport','disabled') not in ('disabled','foreground_ocr','background_native'): raise ValueError('不支持的发送通道')
    if 'weekly_time' in d:
        from datetime import datetime
        try: datetime.strptime(d['weekly_time'],'%H:%M')
        except ValueError: raise ValueError('时间应为 HH:MM')
    if 'weekly_day' in d and int(d['weekly_day']) not in range(7): raise ValueError('星期无效')
    for k in ('paused','sync_enabled','web_enabled'):
        if k in d and not isinstance(d[k],bool): raise ValueError('开关必须为布尔值')
    # Changing provider requires entering that provider's key, preventing accidental credential forwarding.
    if url.rstrip('/')!=core.config()['base_url'].rstrip('/') and not d.get('api_key'): raise ValueError('更换服务地址时请同时填写对应密钥')
    if d.get('api_key'): core.set_key(d['api_key'])
    core.save_config(d)
    return jsonify(ok=True)

@app.get('/api/models')
def model_list(): return jsonify(models=core.models())

@app.post('/api/test-model')
def test_model(): return task(core.llm,'你是连接测试助手。','请只回复：连接成功。',64)

@app.post('/api/groups')
def save_group():
    d=request.get_json(); name=str(d.get('name','')).strip()
    if not name or len(name)>120: raise ValueError('请填写有效群名')
    gid=d.get('id') or uuid.uuid4().hex
    if d.get('weekly_send'): raise ValueError('周报必须先预览；发送通道接通前不能启用定时发群')
    with core.db() as c:
        old=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
        live_after=old['live_after'] if old and old['reply_enabled'] else int(core.now().timestamp())
        c.execute('''INSERT INTO groups(id,name,remote_id,enabled,reply_enabled,live_after) VALUES (?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET name=excluded.name,remote_id=excluded.remote_id,
          enabled=excluded.enabled,reply_enabled=excluded.reply_enabled,live_after=excluded.live_after''',
          (gid,name,str(d.get('remote_id','')),int(bool(d.get('enabled',True))),int(bool(d.get('reply_enabled',False))),live_after))
    return jsonify(id=gid)

@app.post('/api/discover')
def discover(): return task(connectors.discover)

@app.post('/api/sync')
def sync():
    d=request.get_json(); return task(connectors.sync_group,d['group_id'],bool(d.get('full',False)))

@app.post('/api/import')
def import_messages():
    gid=request.form['group_id']
    upload=request.files['file']
    raw=json.loads(upload.read().decode('utf-8-sig'))
    rows=raw if isinstance(raw,list) else raw.get('messages',[])
    if not isinstance(rows,list): raise ValueError('JSON 必须是消息数组或含 messages 数组的对象')
    if rows and 'createTime' in rows[0]: rows=[x for m in rows if (x:=connectors.normalize(m))]
    count=len(core.ingest(gid,rows))
    core.event(f'导入 {count} 条消息；历史导入不会触发自动回复')
    return jsonify(added=count)

@app.post('/api/ask')
def ask():
    d=request.get_json(); q=str(d.get('question','')).strip()
    if not q or len(q)>2000: raise ValueError('问题长度应为 1–2000 字')
    return task(core.answer,d['group_id'],q)

@app.post('/api/report')
def report():
    d=request.get_json()
    a=core.timestamp(d['start']); b=core.timestamp(d['end'])+86400
    if b<=a: raise ValueError('结束日期不能早于开始日期')
    return task(core.report,d['group_id'],a,b)

if __name__=='__main__':
    connectors.start_scheduler()
    # Loopback only. No debug reloader, no duplicate schedulers.
    from werkzeug.serving import run_simple
    run_simple('127.0.0.1',8765,app,use_reloader=False,threaded=True)
