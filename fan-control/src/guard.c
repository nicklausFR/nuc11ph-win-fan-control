// SPDX-License-Identifier: GPL-3.0-only
#include "nuc.h"
#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void dispose(NcSession *session) {
    HANDLE handles[6]={session->map,session->request_event,session->reply_event,session->stop_event,session->process,session->writer};
    unsigned i;
    if(session->shared) UnmapViewOfFile(session->shared);
    for(i=0;i<6;i++) if(handles[i]) CloseHandle(handles[i]);
    session->shared=NULL; session->map=NULL; session->request_event=NULL; session->reply_event=NULL;
    session->stop_event=NULL; session->process=NULL; session->writer=NULL; session->owner_pid=0;
    ZeroMemory(session->lease,16);
}
void nc_session_sync(NcSession *session) {
    if(session->shared && InterlockedCompareExchange(&session->shared->restored,0,0)) {
        if(WaitForSingleObject(session->process,1000)!=WAIT_OBJECT_0) return;
        session->last_reason=InterlockedCompareExchange(&session->shared->reason,0,0);
        session->last_restored=TRUE; dispose(session);
    }
}
NcCode nc_session_restore(NcEngine *engine,NcError *error) {
    NcSession *s=&engine->session;
    if(!s->shared) return NC_OK;
    SetEvent(s->stop_event); SetEvent(s->request_event);
    if(WaitForSingleObject(s->process,10000)!=WAIT_OBJECT_0)
        return nc_error(error,NC_RESTORE_FAILED,0,"Recovery is still running; the writer remains locked.");
    if(!InterlockedCompareExchange(&s->shared->restored,0,0)) {
        NcCode code=nc_hw_restore(&engine->hardware,s->shared->profile,s->shared->original,error);
        if(code!=NC_OK) return code;
        InterlockedExchange(&s->shared->restored,1);
        if(!s->shared->reason) InterlockedExchange(&s->shared->reason,8);
    }
    nc_session_sync(s); return NC_OK;
}
void nc_session_tick(NcEngine *engine) {
    NcSession *s=&engine->session; NcError error={0};
    nc_session_sync(s);
    if(s->process && WaitForSingleObject(s->process,0)==WAIT_OBJECT_0) nc_session_restore(engine,&error);
}

static NcCode begin(NcEngine *engine,DWORD client_pid,DWORD ttl,NcError *error) {
    NcSession *s=&engine->session; DWORD last_error;
    HANDLE parent=NULL,owner=NULL,inherit[8]; SECURITY_ATTRIBUTES sa={sizeof(sa),NULL,TRUE};
    STARTUPINFOEXW startup; PROCESS_INFORMATION process; SIZE_T bytes=0;
    WCHAR exe[1024],command[1400]; BOOL attr_ready=FALSE,started=FALSE; NcCode code=NC_INTERNAL;
    ZeroMemory(&startup,sizeof(startup)); ZeroMemory(&process,sizeof(process));
    s->writer=CreateMutexW(&sa,FALSE,NC_WRITER); last_error=GetLastError();
    if(!s->writer) return nc_error(error,NC_BUSY,last_error,"Cannot acquire the controller writer lock.");
    if(last_error==ERROR_ALREADY_EXISTS) { CloseHandle(s->writer); s->writer=NULL;
        return nc_error(error,NC_BUSY,0,"Another controller or its recovery is active."); }
    s->map=CreateFileMappingW(INVALID_HANDLE_VALUE,&sa,PAGE_READWRITE,0,sizeof(NcShared),NULL);
    if(s->map) s->shared=(NcShared *)MapViewOfFile(s->map,FILE_MAP_ALL_ACCESS,0,0,sizeof(NcShared));
    if(!s->shared) goto failure;
    ZeroMemory(s->shared,sizeof(*s->shared)); s->shared->magic=NC_MAGIC;
    code=nc_hw_ec(&engine->hardware,s->shared->original,error); if(code!=NC_OK) goto failure;
    code=nc_capture(s->shared->original,s->shared->profile,error); if(code!=NC_OK) goto failure;
    code=NC_INTERNAL;
    s->request_event=CreateEventW(&sa,FALSE,FALSE,NULL); s->reply_event=CreateEventW(&sa,FALSE,FALSE,NULL);
    s->stop_event=CreateEventW(&sa,TRUE,FALSE,NULL);
    parent=OpenProcess(SYNCHRONIZE,TRUE,GetCurrentProcessId()); owner=OpenProcess(SYNCHRONIZE,TRUE,client_pid);
    if(!s->request_event||!s->reply_event||!s->stop_event||!parent||!owner) goto failure;
    if(BCryptGenRandom(NULL,s->lease,16,BCRYPT_USE_SYSTEM_PREFERRED_RNG)<0) goto failure;
    if(!GetModuleFileNameW(NULL,exe,_countof(exe))) goto failure;
    if(!SetHandleInformation(engine->power,HANDLE_FLAG_INHERIT,HANDLE_FLAG_INHERIT)) goto failure;
    inherit[0]=s->map; inherit[1]=s->request_event; inherit[2]=s->reply_event; inherit[3]=s->stop_event;
    inherit[4]=parent; inherit[5]=owner; inherit[6]=s->writer; inherit[7]=engine->power;
    if(swprintf_s(command,_countof(command),L"\"%s\" --guardian %llu %llu %llu %llu %llu %llu %llu %llu",exe,
        (unsigned long long)(uintptr_t)inherit[0],(unsigned long long)(uintptr_t)inherit[1],
        (unsigned long long)(uintptr_t)inherit[2],(unsigned long long)(uintptr_t)inherit[3],
        (unsigned long long)(uintptr_t)inherit[4],(unsigned long long)(uintptr_t)inherit[5],
        (unsigned long long)(uintptr_t)inherit[6],(unsigned long long)(uintptr_t)inherit[7])<0) goto failure;
    InitializeProcThreadAttributeList(NULL,1,0,&bytes);
    startup.lpAttributeList=(LPPROC_THREAD_ATTRIBUTE_LIST)HeapAlloc(GetProcessHeap(),0,bytes);
    if(!startup.lpAttributeList || !InitializeProcThreadAttributeList(startup.lpAttributeList,1,0,&bytes)) goto failure;
    attr_ready=TRUE;
    if(!UpdateProcThreadAttribute(startup.lpAttributeList,0,PROC_THREAD_ATTRIBUTE_HANDLE_LIST,inherit,sizeof(inherit),NULL,NULL)) goto failure;
    startup.StartupInfo.cb=sizeof(startup);
    s->shared->deadline=GetTickCount64()+(ULONGLONG)ttl*1000;
    if(!CreateProcessW(exe,command,NULL,NULL,TRUE,CREATE_NO_WINDOW|EXTENDED_STARTUPINFO_PRESENT,NULL,NULL,&startup.StartupInfo,&process)) goto failure;
    started=TRUE; s->process=process.hProcess; CloseHandle(process.hThread); s->owner_pid=client_pid;
    if(WaitForSingleObject(s->reply_event,NC_COMMAND_ACK_MS)!=WAIT_OBJECT_0 || !s->shared->ready) {
        code=nc_error(error,NC_INTERNAL,s->shared->win32,"The C recovery process did not become ready."); goto failure;
    }
    code=NC_OK;
failure:
    last_error=GetLastError();
    if(attr_ready) DeleteProcThreadAttributeList(startup.lpAttributeList);
    if(startup.lpAttributeList) HeapFree(GetProcessHeap(),0,startup.lpAttributeList);
    if(parent) CloseHandle(parent); if(owner) CloseHandle(owner);
    SetHandleInformation(engine->power,HANDLE_FLAG_INHERIT,0);
    if(code!=NC_OK) {
        if(started) { NcError ignored={0}; nc_session_restore(engine,&ignored); }
        else dispose(s);
        if(!error->code) nc_error(error,NC_INTERNAL,last_error,"Cannot launch the C recovery process.");
    }
    return code;
}

NcCode nc_session_command(NcEngine *engine,const NcRequest *r,DWORD client_pid,NcError *error) {
    NcSession *s=&engine->session; DWORD ttl; LONG sequence; NcCode code;
    nc_session_tick(engine);
    if(r->command==NC_RESTORE) return nc_session_restore(engine,error);
    if(!engine->allow_write) return nc_error(error,NC_READ_ONLY,0,"Start with --allow-write to enable temporary control.");
    code=nc_hw_check(&engine->hardware,error); if(code!=NC_OK) return code;
    if(s->shared && (s->owner_pid!=client_pid || memcmp(r->lease,s->lease,16)!=0))
        return nc_error(error,NC_OWNER,0,"The active session belongs to a different client or lease.");
    if(r->command==NC_RENEW && !s->shared) return nc_error(error,NC_EXPIRED,0,"No active lease to renew.");
    ttl=r->command==NC_THRESHOLDS?r->values[2]:(r->command==NC_RENEW?r->values[0]:r->values[1]);
    if(!s->shared) { code=begin(engine,client_pid,ttl,error); if(code!=NC_OK) return code; }
    if(!s->shared) return nc_error(error,NC_INTERNAL,0,"The control session was not initialized.");
    if(s->shared->restored || WaitForSingleObject(s->process,0)!=WAIT_TIMEOUT)
        return nc_error(error,NC_EXPIRED,0,"Recovery has already started.");
    s->shared->request=*r; s->shared->request_deadline=GetTickCount64()+(ULONGLONG)ttl*1000;
    sequence=InterlockedIncrement(&s->shared->sequence); ResetEvent(s->reply_event); SetEvent(s->request_event);
    if(WaitForSingleObject(s->reply_event,NC_COMMAND_ACK_MS)!=WAIT_OBJECT_0 || s->shared->ack!=sequence) {
        NcError ignored={0}; nc_session_restore(engine,&ignored);
        return nc_error(error,NC_TIMEOUT,0,"C controller did not acknowledge the command; restoration requested.");
    }
    code=(NcCode)InterlockedCompareExchange(&s->shared->code,0,0);
    if(code!=NC_OK) {
        DWORD win32=s->shared->win32; char message[192]; NcError ignored={0};
        strcpy_s(message,sizeof(message),s->shared->error); nc_session_restore(engine,&ignored);
        return nc_error(error,code,win32,message);
    }
    return NC_OK;
}

static NcCode execute(NcHardware *hardware,NcShared *s,const NcRequest *r,HANDLE stop,HANDLE power,NcError *error) {
    BYTE target[90],expected[94],actual[94],temps[15],fans[18]; NcCode code; DWORD kind=r->command;
    if(kind==NC_RENEW) kind=(DWORD)InterlockedCompareExchange(&s->active,0,0);
    if(kind!=NC_DUTY && kind!=NC_PROFILE && kind!=NC_THRESHOLDS)
        return nc_error(error,NC_EXPIRED,0,"No active command.");
    if(kind==NC_DUTY || kind==NC_THRESHOLDS) {
        code=nc_hw_temps(hardware,temps,error); if(code!=NC_OK) return code;
        code=nc_hw_fans(hardware,fans,error); if(code!=NC_OK) return code;
        code=nc_thermal(temps,fans,error); if(code!=NC_OK) return code;
    }
    if(r->command!=NC_RENEW) {
        code=nc_hw_check(hardware,error); if(code!=NC_OK) return code;
        if(GetTickCount64()>=s->request_deadline || WaitForSingleObject(stop,0)==WAIT_OBJECT_0 || WaitForSingleObject(power,0)==WAIT_OBJECT_0)
            return nc_error(error,NC_EXPIRED,0,"Command expired before the write.");
        if(kind==NC_DUTY) {
            code=nc_hw_ec(hardware,expected,error); if(code!=NC_OK) return code;
            expected[12]=(BYTE)(128|r->values[0]);
            code=nc_hw_duty(hardware,r->values[0],error); if(code!=NC_OK) return code;
            code=nc_hw_ec(hardware,actual,error); if(code!=NC_OK) return code;
            if(memcmp(actual,expected,94)!=0) return nc_error(error,NC_NOT_CONFIRMED,0,"Manual duty or surrounding settings do not match EC readback.");
        } else {
            code=kind==NC_PROFILE?nc_factory(r->values[0],target,error):nc_thresholds(s->profile,r->values[0],r->values[1],target,error);
            if(code!=NC_OK) return code;
            code=nc_hw_profile(hardware,target,error); if(code!=NC_OK) return code;
            code=nc_hw_ec(hardware,actual,error); if(code!=NC_OK) return code;
            nc_expected(target,s->original,expected);
            if(memcmp(expected,actual,94)!=0) return nc_error(error,NC_NOT_CONFIRMED,0,"Automatic settings were not confirmed by EC readback.");
        }
    }
    s->deadline=s->request_deadline; InterlockedExchange(&s->active,(LONG)kind); return NC_OK;
}

int nc_guardian(int argc,WCHAR **argv) {
    HANDLE inherited[8]={0},waits[5]; NcShared *s; NcHardware hardware; NcError error={0};
    unsigned i; LONG sequence=0,reason=0; NcCode code; BOOL opened=FALSE;
    if(argc!=10) return 2;
    for(i=0;i<8;i++) {
        WCHAR *end=NULL; unsigned long long value=wcstoull(argv[i+2],&end,10); DWORD flags;
        if(!value || !end || *end) return 2;
        inherited[i]=(HANDLE)(uintptr_t)value;
        if(!GetHandleInformation(inherited[i],&flags)) return 2;
        SetHandleInformation(inherited[i],HANDLE_FLAG_INHERIT,0);
    }
    s=(NcShared *)MapViewOfFile(inherited[0],FILE_MAP_ALL_ACCESS,0,0,sizeof(NcShared));
    if(!s || s->magic!=NC_MAGIC) return 2;
    { BYTE expected[94]; nc_expected(s->profile,s->original,expected);
      if(!nc_valid_profile(s->profile) || nc_manual(s->original) || memcmp(expected,s->original,94)!=0) return 2; }
    code=nc_hw_open(&hardware,&error);
    if(code==NC_OK) { opened=TRUE; code=nc_hw_check(&hardware,&error); }
    if(code!=NC_OK) {
        s->win32=error.win32; strcpy_s(s->error,sizeof(s->error),error.message);
        InterlockedExchange(&s->code,(LONG)code); SetEvent(inherited[2]);
    } else {
        InterlockedExchange(&s->ready,1); SetEvent(inherited[2]);
        waits[0]=inherited[1]; waits[1]=inherited[3]; waits[2]=inherited[4]; waits[3]=inherited[5]; waits[4]=inherited[7];
        for(;;) {
            DWORD wait=WaitForMultipleObjects(5,waits,FALSE,NC_MONITOR_INTERVAL_MS); ULONGLONG now=GetTickCount64();
            if(WaitForSingleObject(inherited[4],0)!=WAIT_TIMEOUT) reason=3;
            else if(WaitForSingleObject(inherited[5],0)!=WAIT_TIMEOUT) reason=4;
            else if(WaitForSingleObject(inherited[7],0)==WAIT_OBJECT_0) reason=9;
            else if(WaitForSingleObject(inherited[3],0)==WAIT_OBJECT_0) reason=1;
            else if(now>=s->deadline) reason=2;
            if(reason) break;
            if(wait==WAIT_OBJECT_0 && s->sequence>sequence) {
                NcRequest request=s->request; sequence=s->sequence;
                code=nc_validate_request(&request,&error);
                if(code==NC_OK) code=execute(&hardware,s,&request,inherited[3],inherited[7],&error);
                if(code!=NC_OK) { s->win32=error.win32; strcpy_s(s->error,sizeof(s->error),error.message); }
                InterlockedExchange(&s->code,(LONG)code); InterlockedExchange(&s->ack,sequence); SetEvent(inherited[2]);
                if(code!=NC_OK) { reason=7; break; }
            }
            if(s->active==NC_DUTY || s->active==NC_THRESHOLDS) {
                BYTE temps[15],fans[18];
                code=nc_hw_temps(&hardware,temps,&error);
                if(code==NC_OK) code=nc_hw_fans(&hardware,fans,&error);
                if(code==NC_OK) code=nc_thermal(temps,fans,&error);
                if(code!=NC_OK) { reason=code==NC_FAN?6:5; break; }
            }
            if(wait==WAIT_FAILED) { reason=7; break; }
        }
    }
    InterlockedExchange(&s->reason,reason?reason:7);
    if(opened) {
        while(nc_hw_restore(&hardware,s->profile,s->original,&error)!=NC_OK) {
            s->win32=error.win32; strcpy_s(s->error,sizeof(s->error),error.message);
            InterlockedExchange(&s->code,NC_RESTORE_FAILED); Sleep(1000);
        }
        InterlockedExchange(&s->active,0); InterlockedExchange(&s->restored,1); SetEvent(inherited[2]);
        nc_hw_close(&hardware);
    }
    UnmapViewOfFile(s);
    /* This is a dedicated process. Keep its inherited references, including the
       writer lock, alive through CRT teardown; Windows closes them on exit. */
    return opened?0:1;
}
