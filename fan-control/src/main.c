// SPDX-License-Identifier: GPL-3.0-only
#include "nuc.h"
#include <powrprof.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

static NcEngine engine;
static SERVICE_STATUS_HANDLE service_handle;
static SERVICE_STATUS service_status;

static ULONG CALLBACK power_callback(PVOID context,ULONG type,PVOID setting) {
    NcEngine *e=(NcEngine *)context; (void)setting;
    if(type==PBT_APMSUSPEND || type==PBT_APMRESUMEAUTOMATIC || type==PBT_APMRESUMESUSPEND) SetEvent(e->power);
    return ERROR_SUCCESS;
}
static BOOL WINAPI console_handler(DWORD type) {
    if(type==CTRL_C_EVENT || type==CTRL_BREAK_EVENT || type==CTRL_CLOSE_EVENT || type==CTRL_SHUTDOWN_EVENT) {
        SetEvent(engine.stop); WaitForSingleObject(engine.stopped,4000); return TRUE;
    }
    return FALSE;
}
static void service_report(DWORD state,DWORD error) {
    ZeroMemory(&service_status,sizeof(service_status)); service_status.dwServiceType=SERVICE_WIN32_OWN_PROCESS;
    service_status.dwCurrentState=state; service_status.dwWin32ExitCode=error;
    if(error==ERROR_SERVICE_SPECIFIC_ERROR) service_status.dwServiceSpecificExitCode=1;
    if(state==SERVICE_RUNNING) service_status.dwControlsAccepted=SERVICE_ACCEPT_STOP|SERVICE_ACCEPT_SHUTDOWN|SERVICE_ACCEPT_POWEREVENT;
    if(state==SERVICE_STOP_PENDING || state==SERVICE_START_PENDING) { service_status.dwWaitHint=15000; service_status.dwCheckPoint=1; }
    SetServiceStatus(service_handle,&service_status);
}
static DWORD WINAPI service_handler(DWORD control,DWORD event,void *data,void *context) {
    (void)data; (void)context;
    if(control==SERVICE_CONTROL_STOP || control==SERVICE_CONTROL_SHUTDOWN) {
        service_report(SERVICE_STOP_PENDING,NO_ERROR); SetEvent(engine.stop); return NO_ERROR;
    }
    if(control==SERVICE_CONTROL_POWEREVENT) { power_callback(&engine,event,NULL); return NO_ERROR; }
    if(control==SERVICE_CONTROL_INTERROGATE) return NO_ERROR;
    return ERROR_CALL_NOT_IMPLEMENTED;
}
static int run(BOOL service) {
    NcError error={0}; NcCode code; HPOWERNOTIFY notification=NULL;
    DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS subscribe={power_callback,&engine};
    engine.started=GetTickCount64();
    code=nc_hw_open(&engine.hardware,&error);
    if(code==NC_OK && engine.allow_write) code=nc_hw_check(&engine.hardware,&error);
    if(code!=NC_OK) {
        fprintf(stderr,"%s (%s, Windows %lu)\n",error.message,nc_code_name(error.code),error.win32);
        SetEvent(engine.stopped); return 1;
    }
    if(PowerRegisterSuspendResumeNotification(DEVICE_NOTIFY_CALLBACK,&subscribe,&notification)!=ERROR_SUCCESS && engine.allow_write) {
        fprintf(stderr,"Cannot register suspend/resume recovery; writes refused.\n"); nc_hw_close(&engine.hardware); SetEvent(engine.stopped); return 1;
    }
    if(service) service_report(SERVICE_RUNNING,NO_ERROR);
    else { wprintf(L"Nuc Engine %hs | %s | %s\n",NC_VERSION,engine.allow_write?L"temporary writes enabled":L"read-only",engine.pipe_name); fflush(stdout); }
    code=nc_serve(&engine,&error);
    if(code!=NC_OK) fprintf(stderr,"%s (%s, Windows %lu)\n",error.message,nc_code_name(error.code),error.win32);
    { NcError restore={0}; if(nc_session_restore(&engine,&restore)!=NC_OK) {
        fprintf(stderr,"%s\n",restore.message); code=NC_RESTORE_FAILED;
    } }
    if(notification) PowerUnregisterSuspendResumeNotification(notification);
    nc_hw_close(&engine.hardware); SetEvent(engine.stopped); return code==NC_OK?0:1;
}
static void WINAPI service_main(DWORD argc,WCHAR **argv) {
    int result; (void)argc; (void)argv;
    service_handle=RegisterServiceCtrlHandlerExW(L"NucControlEngine",service_handler,NULL);
    if(!service_handle) return;
    service_report(SERVICE_START_PENDING,NO_ERROR); result=run(TRUE);
    service_report(SERVICE_STOPPED,result?ERROR_SERVICE_SPECIFIC_ERROR:NO_ERROR);
}
int wmain(int argc,WCHAR **argv) {
    BOOL service=FALSE,status=FALSE; int i,result; WCHAR sid[192];
    if(argc>1 && wcscmp(argv[1],L"--guardian")==0) return nc_guardian(argc,argv);
    if(argc==2 && wcscmp(argv[1],L"self-test")==0) return nc_self_test();
    ZeroMemory(&engine,sizeof(engine));
    if(!nc_current_sid(sid,_countof(sid))) { fprintf(stderr,"Cannot determine Windows account.\n"); return 1; }
    wcscpy_s(engine.owner_sid,_countof(engine.owner_sid),sid);
    for(i=1;i<argc;i++) {
        if(wcscmp(argv[i],L"serve")==0) continue;
        if(wcscmp(argv[i],L"service")==0) service=TRUE;
        else if(wcscmp(argv[i],L"status")==0) status=TRUE;
        else if(wcscmp(argv[i],L"--allow-write")==0) engine.allow_write=TRUE;
        else if(wcscmp(argv[i],L"--owner-sid")==0 && i+1<argc) {
            if(wcscpy_s(engine.owner_sid,_countof(engine.owner_sid),argv[++i])!=0) return 2;
        } else {
            printf("Usage: nuc-engine.exe serve [--allow-write]\n       nuc-engine.exe status\n       nuc-engine.exe self-test\n       nuc-engine.exe service --owner-sid SID [--allow-write]\n"); return 2;
        }
    }
    if((wcscmp(sid,engine.owner_sid)!=0 && wcscmp(sid,L"S-1-5-18")!=0) ||
       (service && wcscmp(engine.owner_sid,L"S-1-5-18")==0)) {
        fprintf(stderr,"An explicit interactive owner SID is required for a SYSTEM service.\n"); return 2;
    }
    if(swprintf_s(engine.pipe_name,_countof(engine.pipe_name),L"\\\\.\\pipe\\NucControl.v1.%s",engine.owner_sid)<0) return 2;
    if(status) {
        NcJson json; NcError error={0};
        if(nc_hw_open(&engine.hardware,&error)!=NC_OK) { fprintf(stderr,"%s\n",error.message); return 1; }
        engine.started=GetTickCount64(); nc_json_init(&json); nc_status_json(&engine,&json,&error); nc_hw_close(&engine.hardware);
        if(error.code!=NC_OK || json.failed) { fprintf(stderr,"%s\n",error.message); return 1; }
        puts(json.data); return 0;
    }
    engine.stop=CreateEventW(NULL,TRUE,FALSE,NULL); engine.power=CreateEventW(NULL,TRUE,FALSE,NULL); engine.stopped=CreateEventW(NULL,TRUE,FALSE,NULL);
    if(!engine.stop||!engine.power||!engine.stopped) return 1;
    if(service) {
        SERVICE_TABLE_ENTRYW table[]={{L"NucControlEngine",service_main},{NULL,NULL}};
        result=StartServiceCtrlDispatcherW(table)?0:1;
    } else { SetConsoleCtrlHandler(console_handler,TRUE); result=run(FALSE); SetConsoleCtrlHandler(console_handler,FALSE); }
    CloseHandle(engine.stop); CloseHandle(engine.power); CloseHandle(engine.stopped); return result;
}
