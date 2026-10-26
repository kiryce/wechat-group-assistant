"""Conservative foreground-only Windows transport for this local application.
Never switches chats or steals focus. OCR title + empty draft + foreground checks
precede input. A send with uncertain outcome is never retried automatically.
"""
import ctypes
import re
import threading
import time
import unicodedata
from pathlib import Path
import core

LOCK=threading.Lock()
ENGINE=None

class NotReady(ValueError): pass
class Uncertain(ValueError): pass

def norm(text):
    return re.sub(r'[^\w\u4e00-\u9fff]','',unicodedata.normalize('NFKC',text).lower())

def title_matches(observed,expected):
    observed=re.sub(r'[（(]\s*\d+\s*[）)]\s*$','',observed.strip())
    return norm(observed)==norm(expected)

def ocr(img):
    global ENGINE
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    if ENGINE is None: ENGINE=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    result,_=ENGINE(np.array(img))
    return result or []

def idle_seconds():
    from ctypes import wintypes
    class LastInput(ctypes.Structure):
        _fields_=[('cbSize',wintypes.UINT),('dwTime',wintypes.DWORD)]
    value=LastInput();value.cbSize=ctypes.sizeof(value)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(value))
    return ((ctypes.windll.kernel32.GetTickCount()-value.dwTime)&0xffffffff)/1000

def foreground():
    import win32gui,win32process,psutil
    hwnd=win32gui.GetForegroundWindow()
    _,pid=win32process.GetWindowThreadProcessId(hwnd)
    try: exe=Path(psutil.Process(pid).exe())
    except (psutil.Error,OSError): raise NotReady('无法确认前台进程')
    if exe.name.lower()!='weixin.exe' or win32gui.GetWindowText(hwnd)!='微信':
        raise NotReady('等待微信主窗口处于前台；程序不会抢占其他窗口')
    if win32gui.IsIconic(hwnd): raise NotReady('微信已最小化')
    rect=win32gui.GetWindowRect(hwnd)
    if rect[2]-rect[0]<700 or rect[3]-rect[1]<500: raise NotReady('微信窗口过小，无法可靠核对')
    return hwnd,rect

def check(group,expected_hwnd=None):
    from PIL import ImageGrab
    hwnd,rect=foreground()
    if expected_hwnd is not None and hwnd!=expected_hwnd: raise Uncertain('前台窗口发生变化，已停止发送')
    image=ImageGrab.grab(bbox=rect)
    w,h=image.size
    # The window layout is calibrated to the observed standard Weixin desktop UI.
    titles=ocr(image.crop((int(w*.35),int(h*.045),int(w*.78),int(h*.12))))
    title_parts=sorted(titles,key=lambda r:min(p[0] for p in r[0]))
    joined=' '.join(r[1] for r in title_parts)
    if not (title_parts and all(float(r[2])>=.85 for r in title_parts) and title_matches(joined,group['name'])):
        raise NotReady('当前聊天标题未通过核对：'+repr([(r[1],round(float(r[2]),2)) for r in titles]))
    bottom=ocr(image.crop((int(w*.87),int(h*.90),w,h)))
    if not any('发送' in r[1] and float(r[2])>=.80 for r in bottom):
        raise NotReady('未识别到发送按钮，窗口布局未通过检查')
    return hwnd,rect,image

def send_once(group,text):
    import pyautogui as ui
    import pyperclip
    import win32gui
    if len(text)>1800: raise NotReady('回复超过1800字，请在管理页人工处理')
    if not text.strip(): raise NotReady('禁止发送空消息')
    with LOCK:
        if idle_seconds()<2: raise NotReady('等待键鼠空闲')
        hwnd,rect,img=check(group)
        w,h=img.size
        draft=ocr(img.crop((int(w*.36),int(h*.79),int(w*.96),int(h*.90))))
        if any(str(r[1]).strip() for r in draft): raise NotReady('输入框中已有内容，未覆盖草稿')
        old_clip=pyperclip.paste()
        touched=False
        try:
            ui.click(rect[0]+int(w*.52),rect[1]+int(h*.82))
            if win32gui.GetForegroundWindow()!=hwnd: raise NotReady('输入前焦点变化')
            pyperclip.copy('')
            ui.hotkey('ctrl','a');ui.hotkey('ctrl','c');time.sleep(.15)
            if pyperclip.paste().strip(): raise NotReady('输入框内有草稿，未覆盖')
            check(group,hwnd)
            pyperclip.copy(text);touched=True
            ui.hotkey('ctrl','v');time.sleep(.25)
            if win32gui.GetForegroundWindow()!=hwnd: raise Uncertain('粘贴后焦点变化；请人工检查草稿')
            pyperclip.copy('');ui.hotkey('ctrl','a');ui.hotkey('ctrl','c');time.sleep(.15)
            if pyperclip.paste().replace('\r\n','\n')!=text.replace('\r\n','\n'):
                raise Uncertain('输入框文本校验失败；没有点击发送')
            check(group,hwnd)
            ui.press('end')
            if win32gui.GetForegroundWindow()!=hwnd: raise Uncertain('发送前焦点变化')
            ui.hotkey('alt','s')
            return True
        except NotReady as e:
            if touched: raise Uncertain(str(e)+'；已保留草稿，禁止自动重试') from None
            raise
        except Uncertain: raise
        except Exception:
            if touched: raise Uncertain('发送结果不确定，禁止自动重试') from None
            raise NotReady('发送前检查失败，请检查窗口状态') from None
        finally:
            pyperclip.copy(old_clip)

def deliver_pending():
    cfg=core.config()
    if cfg['paused'] or cfg['transport'] not in ('foreground_ocr','background_native'): return
    with core.db() as c:
        row=c.execute("SELECT o.* FROM outbox o JOIN groups g ON g.id=o.group_id WHERE o.status='draft' AND o.kind='answer' AND g.enabled=1 AND g.reply_enabled=1 ORDER BY o.id LIMIT 1").fetchone()
        if not row: return
        item=dict(row)
        group=dict(c.execute('SELECT * FROM groups WHERE id=?',(item['group_id'],)).fetchone())
        c.execute("UPDATE outbox SET status='sending',error=NULL WHERE id=?",(item['id'],))
    try:
        if cfg['transport']=='background_native':
            import background_transport
            background_transport.send(group,item['content'])
        else:
            send_once(group,item['content'])
    except NotReady as exc:
        with core.db() as c: c.execute("UPDATE outbox SET status='draft',error=? WHERE id=?",(str(exc),item['id']))
        return
    except Exception as exc:
        with core.db() as c: c.execute("UPDATE outbox SET status='uncertain',error=? WHERE id=?",(str(exc),item['id']))
        core.event('发送暂停：'+str(exc),'error');return
    # Independently verify the exact outgoing text in the same group's source DB.
    import local_source
    for _ in range(6):
        time.sleep(2)
        try:
            result=local_source.read_group(group)
            matches=[r for r in result['rows'] if r['is_self'] and r['text'].replace('\r\n','\n')==item['content'].replace('\r\n','\n') and r['timestamp']>=core.timestamp(item['created'])-2]
            if matches:
                # A new inbound message may arrive while outgoing verification reads
                # the source. Persist its dispatch job atomically with ingestion.
                core.ingest(group['id'],result['rows'],enqueue=True)
                with core.db() as c:
                    c.execute("UPDATE outbox SET status='sent',error=NULL WHERE id=?",(item['id'],))
                    c.execute("UPDATE inbox SET state='sent' WHERE message_id=?",(item['ref'],))
                core.event(group['name']+'：答复已发送并在原群消息记录核验')
                return
        except Exception: pass
    with core.db() as c: c.execute("UPDATE outbox SET status='uncertain',error='未在源记录确认；不自动重试' WHERE id=?",(item['id'],))
    core.event('发送结果尚未确认，请在微信群核对；不会自动重发','error')
