"""Resolve an exact image server ID in the same group; never fetch XML URLs."""
import base64
import hashlib
import io
import json
import re
import struct
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image,ImageOps
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import core
import local_source
from vault import crypt


def decode_dat(data,keys):
    if data[:6] in (b'\x07\x08V2\x08\x07',b'\x07\x08V1\x08\x07'):
        if len(data)<15:raise ValueError('图片文件不完整')
        _,a,x=struct.unpack('<6sLLx',data[:15]);a+=16-a%16
        rest=data[15:]
        if a>len(rest) or x>len(rest)-a:raise ValueError('图片文件不完整')
        key=keys['aes'].encode() if data[2:4]==b'V2' else b'cfcd208495d565ef'
        head=unpad(AES.new(key[:16],AES.MODE_ECB).decrypt(rest[:a]),16)
        return head+(rest[a:-x] if x else rest[a:])+ (bytes(v^keys['xor'] for v in rest[-x:]) if x else b'')
    if data.startswith((b'\xff\xd8\xff',b'\x89PNG',b'GIF8',b'RIFF')):return data
    for signature in (b'\xff\xd8\xff',b'\x89PNG',b'GIF8'):
        k=data[0]^signature[0]
        if bytes(v^k for v in data[:len(signature)])==signature:
            return bytes(v^k for v in data)
    raise ValueError('当前图片格式暂不支持')


def load(group_id,quote_id):
    if not str(quote_id).isdigit():raise ValueError('引用图片的消息 ID 无效')
    with core.db() as c:
        group=c.execute('SELECT * FROM groups WHERE id=? AND enabled=1',(group_id,)).fetchone()
    if not group or not group['remote_id']:raise ValueError('引用图片所属群未绑定')
    settings=json.loads((core.DATA/'local-source.json').read_text('utf8'))
    store=Path(settings['db_storage']);account=store.parent
    table='Msg_'+hashlib.md5(group['remote_id'].encode()).hexdigest()
    record=None
    for path in sorted((store/'message').glob('message_*.db')):
        if not path.stem.removeprefix('message_').isdigit():continue
        with local_source.snapshot(path) as c:
            if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
            r=c.execute('SELECT * FROM "'+table+'" WHERE server_id=?',(quote_id,)).fetchone()
            if r:record=dict(r);break
    if not record or int(record['local_type'])&0xffffffff!=3:
        raise ValueError('本机尚未找到这条引用的原图片消息，请在微信中打开该图片后重试')
    body=local_source.decode(record.get('compress_content') or record.get('message_content'))
    x=ET.fromstring(body[body.index('<'):]);img=x.find('.//img')
    hashes=set()
    if img is not None and re.fullmatch('[0-9a-fA-F]{32}',img.get('md5','')):hashes.add(img.get('md5').lower())
    packed=bytes(record.get('packed_info_data') or b'')
    hashes.update(m.decode().lower() for m in re.findall(rb'(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])',packed))
    if not hashes:raise ValueError('无法确定被引用图片的本地文件标识')
    root=account/'msg/attach';candidates=[]
    for h in hashes:
        candidates.extend(p for p in root.rglob(h+'*.dat') if re.fullmatch(h+r'(?:_[th]|\.t)?\.dat',p.name,re.I))
    if not candidates:raise ValueError('被引用图片尚未下载到本机，请在微信中打开图片后重试')
    kp=core.DATA/'image-key.dpapi'
    keys=json.loads(crypt(kp.read_bytes(),True)) if kp.exists() else {}
    best=None
    for p in candidates:
        if not p.resolve().is_relative_to(root.resolve()) or p.stat().st_size>20*1024*1024:continue
        try:
            payload=decode_dat(p.read_bytes(),keys)
            if payload.startswith(b'wxgf'):
                proc=subprocess.run([sys.executable,str(core.ROOT/'image_decoder.py')],input=payload,
                    capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if proc.returncode:continue
                payload=proc.stdout
            with Image.open(io.BytesIO(payload)) as im:
                if im.width*im.height>25_000_000:continue
                im=ImageOps.exif_transpose(im);im.load();size=im.size
                im=im.convert('RGB');im.thumbnail((2048,2048))
                out=io.BytesIO();im.save(out,format='JPEG',quality=90)
                candidate=(size[0]*size[1],out.getvalue(),size,p.name)
                if best is None or candidate[0]>best[0]:best=candidate
        except Exception:continue
    if best is None:raise ValueError('图片文件暂时无法解码，请打开原图后重试；不会猜测图片内容')
    thumb='_t.dat' in best[3] or '.t.dat' in best[3]
    return {'url':'data:image/jpeg;base64,'+base64.b64encode(best[1]).decode(),
            'description':('仅取得缩略图' if thumb else '已取得本机较大图片')+f'，原始尺寸 {best[2][0]}×{best[2][1]}；模糊内容不要猜测。',
            'width':best[2][0],'height':best[2][1],'thumbnail':thumb}
