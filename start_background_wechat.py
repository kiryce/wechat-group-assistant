"""Launch pinned client without changing the user's existing installation."""
import psutil
import core
import background_transport as bridge

def main():
    try:
        if bridge.status().get('compatible'): return
    except Exception: pass
    active=[]
    for p in psutil.process_iter(['name','exe']):
        if (p.info['name'] or '').lower()=='weixin.exe': active.append(p.info['exe'])
    if active:
        core.event('已有微信进程未接入后台模块；请退出该客户端后使用“启动后台微信.cmd”。','error')
        return
    path=core.ROOT/'private/client-4.1.10.27/runtime/Weixin.exe'
    bridge.launch(path)

if __name__=='__main__':
    core.init_db()
    main()
