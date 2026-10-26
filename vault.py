"""Windows current-user DPAPI storage. Never return credentials to the browser."""
import ctypes
from ctypes import wintypes

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

def crypt(data: bytes, decrypt=False) -> bytes:
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    dll = ctypes.windll.crypt32
    if decrypt:
        ok = dll.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = dll.CryptProtectData(ctypes.byref(source), 'WeChat Group Assistant', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError('Windows 凭据加密/解密失败')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)
