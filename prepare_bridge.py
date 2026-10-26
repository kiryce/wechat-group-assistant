"""Generate a minimal local-only bridge from the inspected upstream source.
No module hiding, anti-revoke patch, remote callbacks or unrelated routes.
"""
from pathlib import Path
import re
import subprocess
ROOT=Path(__file__).resolve().parent
up=ROOT/'vendor/WeChat-Hook'
out=ROOT/'native_bridge'
out.mkdir(exist_ok=True)
proxy=(up/'src/version_proxy.cpp').read_text('utf8')
proxy=proxy[proxy.index('/* ========================='):proxy.index('void CustomInit(')]
proxy=re.sub(r'(BOOL|DWORD) WINAPI (My_\w+)',r'extern "C" \1 WINAPI \2',proxy)
(out/'proxy.cpp').write_text('#include <windows.h>\n#include <string>\n'+proxy,'utf8')
(out/'version.def').write_bytes((up/'version.def').read_bytes())
src=(up/'src/wx_send.cpp').read_text('utf8')
structs=src[src.index('    struct WeixinString\n'):src.index('    struct InnerStruct1')]
setstr=src[src.index('    inline void SetWeixinString('):src.index('    void BuildSendParam1_sub')]
send=src[src.index('    void BuildTextMessage('):src.index('    void DecodePic(')]
glob=(up/'include/global.h').read_text('utf8')
offsets=glob[glob.index('namespace offset\n'):glob.index('//4.1.5.30')]
prefix='''#include <windows.h>
#include <string>
#include <cstdint>
#include <cstddef>
#include <cstring>
using WeixinCall = __int64(*)(...);
namespace Memory { void Free(void* p) { if(p) VirtualFree(p,0,MEM_RELEASE); } }
namespace WeixinSend {
uintptr_t GetWeixinDllBase(){return (uintptr_t)GetModuleHandleW(L"Weixin.dll");}
uint64_t CalcWxCapacity(uint64_t len){return ((len+1+15)&~15ULL)-1;}
template<typename T> T* HeapAlloc_mb(size_t count){return (T*)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,sizeof(T)*count);}
'''
(out/'send.cpp').write_text(prefix+offsets+'\n#pragma pack(push,1)\n'+structs+'\n#pragma pack(pop)\n'+setstr+send+'\n}\n','utf8')
(out/'upstream.json').write_text(__import__('json').dumps({'repository':'https://github.com/aixed/WeChat-Hook','commit':subprocess.check_output(['git','-C',str(up),'rev-parse','HEAD'],text=True).strip(),'target_version':'4.1.10.27'},indent=2),'utf8')
