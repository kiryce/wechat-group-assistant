"""Isolated WXGF decoder using the installed compatible WeChat codec."""
import ctypes
import sys
from pathlib import Path

if __name__=='__main__':
    data=sys.stdin.buffer.read(20*1024*1024+1)
    if not data.startswith(b'wxgf') or len(data)>20*1024*1024:sys.exit(2)
    dll=Path(__file__).parent/'private/client-4.1.10.27/runtime/4.1.10.27/VoipEngine.dll'
    lib=ctypes.WinDLL(str(dll))
    fn=lib.wxam_dec_wxam2pic_5
    fn.argtypes=[ctypes.c_int64,ctypes.c_int,ctypes.c_int64,ctypes.POINTER(ctypes.c_int),ctypes.c_int64]
    fn.restype=ctypes.c_int64
    class Config(ctypes.Structure):
        _fields_=[('mode',ctypes.c_int),('reserved',ctypes.c_int)]
    src=ctypes.create_string_buffer(data)
    for mode in (0,3):
        dst=ctypes.create_string_buffer(32*1024*1024);n=ctypes.c_int(len(dst));config=Config(mode,0)
        result=fn(ctypes.addressof(src),len(data),ctypes.addressof(dst),ctypes.byref(n),ctypes.addressof(config))
        if result==0 and 0<n.value<=len(dst):
            sys.stdout.buffer.write(dst.raw[:n.value]);sys.exit(0)
    sys.exit(3)
