#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <string>
#include <fstream>
#include <mutex>
#include <exception>
#include "httplib.h"
#include "json.hpp"
using json=nlohmann::json;
namespace WeixinSend { void SendText(const std::string&,const std::string&); }
void InitRealDll();
extern "C" DWORD WINAPI My_GetFileVersionInfoSizeW(LPCWSTR,LPDWORD);
extern "C" BOOL WINAPI My_GetFileVersionInfoW(LPCWSTR,DWORD,DWORD,LPVOID);
extern "C" BOOL WINAPI My_VerQueryValueW(LPCVOID,LPCWSTR,LPVOID*,PUINT);
static std::mutex send_lock;

std::string version(HMODULE mod){
    wchar_t path[32768];GetModuleFileNameW(mod,path,32768);
    DWORD unused=0,n=My_GetFileVersionInfoSizeW(path,&unused);
    if(!n)return "unknown";
    std::vector<char>b(n);
    if(!My_GetFileVersionInfoW(path,0,n,b.data()))return "unknown";
    VS_FIXEDFILEINFO* info=nullptr;UINT len=0;
    if(!My_VerQueryValueW(b.data(),L"\\",(void**)&info,&len)||len<sizeof(*info))return "unknown";
    return std::to_string(HIWORD(info->dwFileVersionMS))+"."+std::to_string(LOWORD(info->dwFileVersionMS))+"."+
      std::to_string(HIWORD(info->dwFileVersionLS))+"."+std::to_string(LOWORD(info->dwFileVersionLS));
}

DWORD WINAPI serve(void*){
    char token[256]={},config_path[32768]={};
    DWORD n=GetEnvironmentVariableA("WX_ASSIST_TOKEN",token,sizeof(token));
    DWORD p=GetEnvironmentVariableA("WX_ASSIST_ALLOWLIST",config_path,sizeof(config_path));
    if(n<32||n>=sizeof(token)||!p||p>=sizeof(config_path))return 0;
    HMODULE mod=nullptr;
    for(int i=0;i<600 && !mod;i++){mod=GetModuleHandleW(L"Weixin.dll");if(!mod)Sleep(200);}
    if(!mod)return 0;
    auto v=version(mod);
    httplib::Server server;
    server.set_payload_max_length(16384);
    server.new_task_queue=[] { return new httplib::ThreadPool(2); };
    server.set_pre_routing_handler([&](const httplib::Request&req,httplib::Response&res){
        if(req.has_header("Origin")||req.get_header_value("Authorization")!="Bearer "+std::string(token)){
            res.status=403;res.set_content("forbidden","text/plain");
            return httplib::Server::HandlerResponse::Handled;
        }
        return httplib::Server::HandlerResponse::Unhandled;
    });
    server.Get("/status",[&](const httplib::Request&,httplib::Response&res){
        res.set_content(json({{"version",v},{"compatible",v=="4.1.10.27"},{"pid",GetCurrentProcessId()}}).dump(),"application/json");
    });
    server.Post("/send",[&](const httplib::Request&req,httplib::Response&res){
        if(v!="4.1.10.27"){res.status=409;res.set_content("unsupported client version","text/plain");return;}
        try{
            auto d=json::parse(req.body);
            std::string to=d.at("to"),text=d.at("text");
            std::ifstream f(config_path);json allow;f>>allow;
            bool permitted=false;
            for(auto&g:allow.at("groups"))if(g.get<std::string>()==to)permitted=true;
            if(!permitted||text.empty()||text.size()>10000){res.status=403;return;}
            std::lock_guard<std::mutex>lock(send_lock);
            WeixinSend::SendText(to,text);
            // Accepted means only that the native call returned; verify in source DB.
            res.set_content("{\"accepted\":true}","application/json");
        }catch(const std::exception&){res.status=400;res.set_content("invalid request","text/plain");}
    });
    server.listen("127.0.0.1",30001);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID){
    if(reason==DLL_PROCESS_ATTACH){
        DisableThreadLibraryCalls(module);InitRealDll();
        auto cmd=std::wstring(GetCommandLineW());
        if(cmd.find(L"--type=")==std::wstring::npos && cmd.find(L"--crashpad-handler")==std::wstring::npos){
            HANDLE t=CreateThread(nullptr,0,serve,nullptr,0,nullptr);if(t)CloseHandle(t);
        }
    }
    return TRUE;
}
