"""Use upstream read-only database-key recovery for the logged-in user's local history.
No hook/restart fallback. Keys are DPAPI-protected and never printed.
"""
import multiprocessing
import sys
from pathlib import Path
import psutil
import core
from vault import crypt

SRC=core.ROOT/'vendor/WeChatDataAnalysis/src'
sys.path.insert(0,str(SRC))

def main():
    from wechat_decrypt_tool.dll_key_scan import extract_xor_keys_from_dll
    from wechat_decrypt_tool.key_v4 import recover_key
    candidates=[]
    for p in psutil.process_iter(['name','exe','cmdline']):
        if (p.info['name'] or '').lower()=='weixin.exe':
            candidates.append(p)
    if not candidates: raise RuntimeError('WeChat is not running')
    p=min(candidates,key=lambda x:len(x.info.get('cmdline') or []))
    exe=Path(p.info['exe'])
    dlls=list(exe.parent.rglob('Weixin.dll'))
    if len(dlls)!=1: raise RuntimeError('Cannot uniquely select Weixin.dll')
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--db-storage',type=Path)
    args=parser.parse_args()
    core.init_db()
    stores=list((Path.home()/'Documents/xwechat_files').glob('*/db_storage'))
    if args.db_storage: stores=[args.db_storage.resolve()]
    if len(stores)!=1: raise RuntimeError('Cannot uniquely select local account data')
    store=stores[0]
    probe=store/'contact/contact.db'
    if not probe.exists(): raise RuntimeError('Contact database is missing')
    keys=extract_xor_keys_from_dll(dlls[0],max_workers=2)
    print('Auxiliary candidates:',len(keys),flush=True)
    if not keys: raise RuntimeError('No compatible key pattern; no hook or restart was attempted')
    for item in keys[:3]:
        mask=bytes.fromhex(item['key_hex'].replace(' ',''))
        raw=recover_key(p.pid,str(probe),mask)
        if raw:
            raw=bytes.fromhex(raw) if isinstance(raw,str) else bytes(raw)
            key=bytes(a^b for a,b in zip(raw,mask)).hex()
            (core.DATA/'wechat-key.dpapi').write_bytes(crypt(key.encode()))
            (core.DATA/'local-source.json').write_text(__import__('json').dumps({'db_storage':str(store),'account':store.parent.name}),encoding='utf8')
            print('Local data key verified and encrypted. No chat contents printed.',flush=True)
            return
    raise RuntimeError('Read-only key recovery failed; no hook or restart was attempted')

if __name__=='__main__':
    multiprocessing.freeze_support()
    main()
