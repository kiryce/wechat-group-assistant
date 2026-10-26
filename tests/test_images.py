import io
import struct
from PIL import Image
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import core
import quoted_images as images

def test_dat_decoding_and_native_image():
    b=io.BytesIO();Image.new('RGB',(8,8),'red').save(b,format='PNG');raw=b.getvalue()
    keys={'aes':'0123456789abcdef','xor':71}
    head=raw[:32];tail=raw[-10:];middle=raw[32:-10]
    encrypted=struct.pack('<6sLLx',b'\x07\x08V2\x08\x07',len(head),len(tail))+AES.new(keys['aes'].encode(),AES.MODE_ECB).encrypt(pad(head,16))+middle+bytes(v^71 for v in tail)
    assert images.decode_dat(encrypted,keys)==raw
    assert images.decode_dat(raw,{})==raw

def test_quote_metadata_backfill(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path);core.init_db()
    row=dict(id='q',text='question old xml',timestamp=core.now().isoformat(),sender='member')
    mid=core.ingest('demo',[row])[0]
    row.update(text='question [引用图片]',quote_id='123',quote_kind=3)
    assert core.ingest('demo',[row])==[]
    with core.db() as c:
        assert c.execute('SELECT quote_id FROM message_quotes WHERE message_id=?',(mid,)).fetchone()[0]=='123'
        assert 'old xml' not in c.execute('SELECT text FROM messages').fetchone()[0]

def test_unknown_group_never_reads_image(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setattr(core,'DATA',tmp_path);core.init_db()
    with pytest.raises(ValueError,match='所属群'):images.load('other','123')

def test_real_mention_with_image_routes_to_vision(tmp_path,monkeypatch):
    import connectors
    monkeypatch.setattr(core,'DATA',tmp_path);core.init_db()
    core.save_config({'paused':False,'bot_id':'self'})
    with core.db() as c:c.execute('UPDATE groups SET reply_enabled=1,live_after=1')
    mid=core.ingest('demo',[dict(id='q',text='这是什么',sender='member',timestamp=core.now().isoformat(),
        mentions=['self'],quote_id='123',quote_kind=3)])[0]
    image={'url':'data:image/jpeg;base64,test','description':'test'}
    monkeypatch.setattr(images,'load',lambda group,ref:image if (group,ref)==('demo','123') else None)
    calls=[]
    def answer(group,question,**kw):
        calls.append(kw);return {'answer':'图片是一只猫 🐈'}
    monkeypatch.setattr(core,'answer',answer)
    connectors.process_incoming(mid)
    assert calls==[{'image':image}]
    with core.db() as c:assert '猫' in c.execute('SELECT content FROM outbox').fetchone()[0]
