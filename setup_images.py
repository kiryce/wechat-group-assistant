"""Resolve and protect local image keys; never print key material."""
import json,os,sys
from pathlib import Path
import core
from vault import crypt
sys.path.insert(0,str(core.ROOT/'vendor/WeChatDataAnalysis/src'))
from wechat_decrypt_tool.image_key_resolver import resolve_local_image_key
if __name__=='__main__':
    settings=json.loads((core.DATA/'local-source.json').read_text('utf8'))
    result=resolve_local_image_key(kvcomm_dir=Path(os.environ['APPDATA'])/'Tencent/xwechat/net/kvcomm',account_dir=Path(settings['db_storage']).parent,account=settings['account'],target_wxid=core.config()['bot_id'] or None)
    if not result or not result.verified: raise SystemExit('No verified image key. Open a recent original image in WeChat and retry.')
    (core.DATA/'image-key.dpapi').write_bytes(crypt(json.dumps({'aes':result.aes_key,'xor':result.xor_key}).encode()))
    print('Verified image keys saved with Windows DPAPI.')
