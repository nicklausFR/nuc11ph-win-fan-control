// SPDX-License-Identifier: GPL-3.0-only
#include "nuc.h"
#include <psapi.h>
#include <stdio.h>
#include <string.h>

static BOOL pipe_io(HANDLE pipe,HANDLE stop,void *buffer,DWORD size,BOOL write) {
    BYTE *at=(BYTE *)buffer; ULONGLONG deadline=GetTickCount64()+NC_PIPE_TIMEOUT_MS;
    while(size) {
        OVERLAPPED operation; DWORD done=0,result; HANDLE waits[2]; BOOL ok;
        ZeroMemory(&operation,sizeof(operation)); operation.hEvent=CreateEventW(NULL,TRUE,FALSE,NULL);
        if(!operation.hEvent) return FALSE;
        ok=write?WriteFile(pipe,at,size,&done,&operation):ReadFile(pipe,at,size,&done,&operation);
        if(!ok && GetLastError()==ERROR_IO_PENDING) {
            ULONGLONG now=GetTickCount64(); DWORD remaining=now<deadline?(DWORD)(deadline-now):0;
            waits[0]=operation.hEvent; waits[1]=stop;
            result=WaitForMultipleObjects(2,waits,FALSE,remaining);
            if(result!=WAIT_OBJECT_0) {
                CancelIoEx(pipe,&operation); GetOverlappedResult(pipe,&operation,&done,TRUE);
                CloseHandle(operation.hEvent); return FALSE;
            }
            ok=GetOverlappedResult(pipe,&operation,&done,FALSE);
        }
        CloseHandle(operation.hEvent);
        if(!ok || !done || done>size) return FALSE;
        at+=done; size-=done;
    }
    return TRUE;
}
static void control_json(NcEngine *engine,NcJson *json,const BYTE ec[94]) {
    NcSession *s=&engine->session; unsigned i; ULONGLONG now=GetTickCount64();
    nc_session_tick(engine);
    nc_json_add(json,"{\"mode\":");
    if(s->shared) {
        LONG mode=InterlockedCompareExchange(&s->shared->active,0,0);
        nc_json_string(json,mode==NC_DUTY?"duty":(mode==NC_PROFILE?"profile":(mode==NC_THRESHOLDS?"thresholds":"recovering")));
        nc_json_add(json,",\"active_lease\":\"");
        for(i=0;i<16;i++) nc_json_add(json,"%02x",s->lease[i]);
        nc_json_add(json,"\",\"owner_pid\":%lu,\"guardian_pid\":%lu,\"remaining_ms\":%llu",s->owner_pid,
            GetProcessId(s->process),(unsigned long long)(s->shared->deadline>now?s->shared->deadline-now:0));
    } else {
        nc_json_string(json,nc_manual(ec)?"external_manual":"automatic");
        nc_json_add(json,",\"active_lease\":null,\"owner_pid\":null,\"guardian_pid\":null,\"remaining_ms\":0");
    }
    nc_json_add(json,",\"last_restoration\":{\"restored\":%s,\"reason\":",s->last_restored?"true":"false");
    nc_json_string(json,nc_reason_name(s->last_reason)); nc_json_add(json,"}}");
}
void nc_capabilities_json(NcEngine *engine,NcJson *json) {
    nc_json_add(json,"{\"engine\":\"C\",\"version\":\"%s\",\"protocol_version\":1,\"hardware_verified\":%s,\"write_enabled\":%s,",
        NC_VERSION,engine->hardware.verified?"true":"false",engine->allow_write?"true":"false");
#ifdef NUC_TESTING
    nc_json_add(json,"\"simulated\":true,");
#else
    nc_json_add(json,"\"simulated\":false,");
#endif
    nc_json_add(json,"\"model\":"); nc_json_wide(json,engine->hardware.model);
    nc_json_add(json,",\"profiles\":[\"quiet\",\"balanced\",\"cool\"],\"manual_control\":\"shared_fan_channel_1\","
        "\"independent_fan_control\":false,\"duty_percent_range\":[%u,%u],\"max_lease_seconds\":%u,"
        "\"manual_cpu_limit_celsius\":%u,\"manual_gpu_limit_celsius\":%u,"
        "\"threshold_units\":\"firmware_encoded\",\"threshold_celsius_conversion_verified\":false,"
        "\"cpu_gpu_temperatures\":true,\"nvme_temperatures\":true,\"motherboard_temperature_identified\":false,\"rgb_supported\":false,"
        "\"discovery\":{\"profiles\":true,\"safeguards\":true}}",NC_MIN_DUTY,NC_MAX_DUTY,NC_MAX_LEASE,NC_CPU_LIMIT,NC_GPU_LIMIT);
}

void nc_profiles_json(NcJson *json) {
    static const char *ids[]={"quiet","balanced","cool"};
    static const char *labels[]={"Silencieux","Équilibré","Refroidissement"};
    unsigned i; BYTE profile[90];
    nc_json_add(json,"{\"schema_version\":1,\"profiles\":[");
    for(i=0;i<_countof(ids);i++) {
        if(i) nc_json_add(json,",");
        nc_factory(i,profile,NULL);
        nc_json_add(json,"{\"id\":"); nc_json_string(json,ids[i]);
        nc_json_add(json,",\"label\":"); nc_json_string(json,labels[i]);
        nc_json_add(json,",\"source\":\"captured_oem_profile\",\"regulation\":\"firmware\","
            "\"minimum_duty_percent\":{\"cpu\":%u,\"nvidia_gpu\":%u},"
            "\"thresholds\":{\"units\":\"firmware_encoded\",\"celsius_conversion_verified\":false,"
            "\"primary_min_encoded\":%u,\"secondary_min_encoded\":%u}}",profile[6],profile[12],profile[5],profile[11]);
    }
    nc_json_add(json,"]}");
}

#define NC_ALL_WRITES "[\"duty\",\"profile\",\"thresholds\",\"renew\"]"
#define NC_ALL_LEASES "[\"duty\",\"profile\",\"thresholds\"]"
#define NC_THERMAL_MODES "[\"duty\",\"thresholds\"]"
static void safeguard(NcJson *json,const char *id,const char *label,const char *applies,const char *action,const char *limit) {
    nc_json_add(json,"{\"id\":"); nc_json_string(json,id);
    nc_json_add(json,",\"label\":"); nc_json_string(json,label);
    nc_json_add(json,",\"applies_to\":%s,\"action\":",applies); nc_json_string(json,action);
    if(limit) { nc_json_add(json,",\"limit_ref\":"); nc_json_string(json,limit); }
    nc_json_add(json,"}");
}
void nc_safeguards_json(NcEngine *engine,NcJson *json) {
    nc_json_add(json,"{\"schema_version\":1,\"write_enabled\":%s,\"configurable_via_api\":false,"
        "\"semantics\":\"configured_policy_not_live_health\",\"limits\":{"
        "\"duty_percent\":{\"minimum\":%u,\"maximum\":%u},"
        "\"lease_seconds\":{\"minimum\":%u,\"maximum\":%u},"
        "\"encoded_thresholds\":{\"minimum\":%u,\"maximum\":%u,\"maximum_delta_from_baseline\":%u,\"units\":\"firmware_encoded\"},"
        "\"cpu_temperature_celsius\":{\"restore_at_or_above\":%u},"
        "\"nvidia_gpu_temperature_celsius\":{\"restore_at_or_above\":%u},"
        "\"fan_rpm\":{\"minimum\":%u,\"maximum\":%u},"
        "\"nominal_monitor_interval_ms\":%u,\"pipe_io_timeout_ms\":%u,\"command_ack_timeout_ms\":%u},"
        "\"rules\":[",engine->allow_write?"true":"false",NC_MIN_DUTY,NC_MAX_DUTY,NC_MIN_LEASE,NC_MAX_LEASE,
        NC_MIN_THRESHOLD,NC_MAX_THRESHOLD,NC_THRESHOLD_DELTA,NC_CPU_LIMIT,NC_GPU_LIMIT,NC_MIN_FAN_RPM,NC_MAX_FAN_RPM,
        NC_MONITOR_INTERVAL_MS,NC_PIPE_TIMEOUT_MS,NC_COMMAND_ACK_MS);
    safeguard(json,"read_only_default","Écriture autorisée uniquement au lancement explicite",NC_ALL_WRITES,"reject",NULL);
    nc_json_add(json,","); safeguard(json,"hardware_pin","Modèle, carte, BIOS et SHA-256 du pilote vérifiés",NC_ALL_WRITES,"reject",NULL);
    nc_json_add(json,","); safeguard(json,"winring0_absent","Présence du service WinRing0 interdite",NC_ALL_WRITES,"reject",NULL);
    nc_json_add(json,","); safeguard(json,"oem_service_disabled","NUC Software Studio arrêté et désactivé",NC_ALL_WRITES,"reject",NULL);
    nc_json_add(json,","); safeguard(json,"exclusive_writer","Un seul contrôleur, récupération comprise",NC_ALL_LEASES,"serialize",NULL);
    nc_json_add(json,","); safeguard(json,"bus_serialization","Ouverture, commande et fermeture Intel sérialisées",NC_ALL_LEASES,"serialize",NULL);
    nc_json_add(json,","); safeguard(json,"automatic_baseline","Sauvegarde automatique obligatoire avant la première écriture",NC_ALL_LEASES,"reject",NULL);
    nc_json_add(json,","); safeguard(json,"duty_bounds","Consigne du canal partagé bornée", "[\"duty\"]","reject","duty_percent");
    nc_json_add(json,","); safeguard(json,"threshold_bounds","Seuils encodés bornés autour de la sauvegarde initiale","[\"thresholds\"]","reject","encoded_thresholds");
    nc_json_add(json,","); safeguard(json,"lease_expiry","Expiration sans renouvellement",NC_ALL_LEASES,"restore","lease_seconds");
    nc_json_add(json,","); safeguard(json,"lease_ownership","Mises à jour liées au processus client et à sa session",NC_ALL_WRITES,"reject",NULL);
    nc_json_add(json,","); safeguard(json,"client_exit","Arrêt du processus client",NC_ALL_LEASES,"restore",NULL);
    nc_json_add(json,","); safeguard(json,"engine_exit","Arrêt du moteur principal",NC_ALL_LEASES,"restore",NULL);
    nc_json_add(json,","); safeguard(json,"guardian_exit","Arrêt du processus de récupération, moteur encore actif",NC_ALL_LEASES,"restore",NULL);
    nc_json_add(json,","); safeguard(json,"power_event","Notification de veille ou de reprise",NC_ALL_LEASES,"restore",NULL);
    nc_json_add(json,","); safeguard(json,"cpu_temperature","Limite thermique du CPU",NC_THERMAL_MODES,"reject_or_restore","cpu_temperature_celsius");
    nc_json_add(json,","); safeguard(json,"nvidia_gpu_temperature","Limite thermique du GPU NVIDIA",NC_THERMAL_MODES,"reject_or_restore","nvidia_gpu_temperature_celsius");
    nc_json_add(json,","); safeguard(json,"temperature_available","Lectures CPU et GPU NVIDIA disponibles et valides",NC_THERMAL_MODES,"reject_or_restore",NULL);
    nc_json_add(json,","); safeguard(json,"fan_rpm_available","Tr/min CPU et GPU NVIDIA disponibles et valides",NC_THERMAL_MODES,"reject_or_restore","fan_rpm");
    nc_json_add(json,","); safeguard(json,"write_readback","Relecture complète après modification",NC_ALL_LEASES,"verify",NULL);
    nc_json_add(json,","); safeguard(json,"restore_readback_retry","94 octets restaurés et vérifiés, nouvelles tentatives en cas d’échec",NC_ALL_LEASES,"retry_restore",NULL);
    nc_json_add(json,","); safeguard(json,"local_api","Accès Windows local restreint, trames validées et délais bornés","[\"all_requests\"]","restrict_access","pipe_io_timeout_ms");
    nc_json_add(json,"],\"limitations\":[\"simultaneous_engine_and_guardian_exit\",\"blocked_driver_or_windows_failure\",\"monitoring_is_not_hard_real_time\"],"
        "\"validation\":{\"long_run_tested\":false,\"real_suspend_resume_tested\":false}}");
}
void nc_status_json(NcEngine *engine,NcJson *json,NcError *error) {
    BYTE ec[94],temps[15],fans[18]; unsigned i; BOOL first=TRUE; NcCode code;
    static const char *temp_names[]={"nvidia_gpu","cpu","integrated_gpu"};
    PROCESS_MEMORY_COUNTERS_EX memory; FILETIME creation,exit_time,kernel,user;
    ULARGE_INTEGER kt,ut; NcJson storage;
    code=nc_hw_ec(&engine->hardware,ec,error); if(code!=NC_OK) return;
    code=nc_hw_temps(&engine->hardware,temps,error); if(code!=NC_OK) return;
    code=nc_hw_fans(&engine->hardware,fans,error); if(code!=NC_OK) return;
    nc_json_add(json,"{\"engine\":\"C\",\"version\":\"%s\",\"engine_pid\":%lu,\"uptime_ms\":%llu,\"temperatures\":[",
        NC_VERSION,GetCurrentProcessId(),(unsigned long long)(GetTickCount64()-engine->started));
    for(i=0;i<3;i++) {
        if(i) nc_json_add(json,",");
        nc_json_add(json,"{\"id\":\"%s\",\"available\":%s,\"celsius\":",temp_names[i],temps[i]>0 && temps[i]<=125?"true":"false");
        if(temps[i]>0 && temps[i]<=125) nc_json_add(json,"%u",temps[i]); else nc_json_add(json,"null");
        nc_json_add(json,"}");
    }
    nc_json_add(json,"],\"fans\":[");
    for(i=0;i<18;i+=3) {
        const char *name=NULL;
        switch(fans[i]) { case 1:name="cpu";break;case 2:name="nvidia_gpu";break;case 3:name="system_1";break;
            case 4:name="system_2";break;case 5:name="pump";break;case 7:name="cpu_2";break;default:break; }
        if(!name) continue;
        if(!first) nc_json_add(json,","); first=FALSE;
        nc_json_add(json,"{\"id\":\"%s\",\"rpm\":%u}",name,(unsigned)fans[i+1]*256+fans[i+2]);
    }
    nc_json_add(json,"],\"thresholds\":{\"units\":\"firmware_encoded\",\"celsius_conversion_verified\":false,"
        "\"primary_min_encoded\":%u,\"secondary_min_encoded\":%u,\"primary_min_duty_percent\":%u,\"secondary_min_duty_percent\":%u},"
        "\"manual_channels\":[",ec[2],ec[8],ec[3],ec[9]);
    first=TRUE;
    for(i=0;i<6;i++) if(ec[i*16+12]&128) {
        if(!first) nc_json_add(json,","); first=FALSE;
        nc_json_add(json,"{\"channel\":%u,\"duty_percent\":%u}",i+1,ec[i*16+12]&127);
    }
    nc_json_add(json,"],\"ec_hex\":\""); for(i=0;i<94;i++) nc_json_add(json,"%02x",ec[i]);
    nc_json_add(json,"\",\"control\":"); control_json(engine,json,ec);
    if(!engine->storage_stamp || GetTickCount64()-engine->storage_stamp>=10000) {
        nc_json_init(&storage); nc_hw_storage(&engine->hardware,&storage);
        if(storage.failed || storage.used>=sizeof(engine->storage_json)) strcpy_s((char *)engine->storage_json,sizeof(engine->storage_json),"[]");
        else strcpy_s((char *)engine->storage_json,sizeof(engine->storage_json),storage.data);
        engine->storage_stamp=GetTickCount64();
    }
    nc_json_add(json,",\"ssds\":%s",engine->storage_json);
    ZeroMemory(&memory,sizeof(memory)); memory.cb=sizeof(memory);
    if(GetProcessMemoryInfo(GetCurrentProcess(),(PROCESS_MEMORY_COUNTERS *)&memory,sizeof(memory)))
        nc_json_add(json,",\"working_set_bytes\":%llu,\"private_bytes\":%llu",(unsigned long long)memory.WorkingSetSize,(unsigned long long)memory.PrivateUsage);
    if(GetProcessTimes(GetCurrentProcess(),&creation,&exit_time,&kernel,&user)) {
        kt.LowPart=kernel.dwLowDateTime; kt.HighPart=kernel.dwHighDateTime;
        ut.LowPart=user.dwLowDateTime; ut.HighPart=user.dwHighDateTime;
        nc_json_add(json,",\"cpu_time_100ns\":%llu",(unsigned long long)(kt.QuadPart+ut.QuadPart));
    }
    nc_json_add(json,"}");
}

static void dispatch(NcEngine *engine,const NcRequest *r,DWORD client_pid,NcJson *json,NcError *error) {
    NcCode code=nc_validate_request(r,error); if(code!=NC_OK) return;
    if(r->command==NC_STATUS) { nc_status_json(engine,json,error); return; }
    if(r->command==NC_CAPABILITIES) { nc_capabilities_json(engine,json); return; }
    if(r->command==NC_PROFILES) { nc_profiles_json(json); return; }
    if(r->command==NC_SAFEGUARDS) { nc_safeguards_json(engine,json); return; }
#ifdef NUC_TESTING
    if(r->command==NC_TEST_FAULT) {
        switch(r->values[0]) {
            case 0: engine->hardware.fake[95]=(BYTE)r->values[1]; break;
            case 1: engine->hardware.fake[94]=(BYTE)r->values[1]; break;
            case 2: engine->hardware.fake[110]=0; engine->hardware.fake[111]=0; break;
            case 3: engine->hardware.fake[110]=5; engine->hardware.fake[111]=220; break;
            case 4: SetEvent(engine->power); break;
            default: break;
        }
        nc_json_add(json,"{\"test_only\":true}"); return;
    }
#endif
    code=nc_session_command(engine,r,client_pid,error); if(code!=NC_OK) return;
    { BYTE ec[94]; code=nc_hw_ec(&engine->hardware,ec,error); if(code!=NC_OK) return;
      if(r->command==NC_RESTORE && nc_manual(ec)) { nc_error(error,NC_EXTERNAL_MANUAL,0,"External manual control has no saved automatic baseline in this engine."); return; }
      nc_json_add(json,"{\"verified\":true,\"control\":"); control_json(engine,json,ec); nc_json_add(json,"}"); }
}

NcCode nc_serve(NcEngine *engine,NcError *error) {
    SECURITY_ATTRIBUTES sa; PSECURITY_DESCRIPTOR sd=NULL; HANDLE pipe;
    if(!nc_security(engine->owner_sid,&sa,&sd)) return nc_error(error,NC_INTERNAL,GetLastError(),"Cannot create the pipe security descriptor.");
    pipe=CreateNamedPipeW(engine->pipe_name,PIPE_ACCESS_DUPLEX|FILE_FLAG_OVERLAPPED|FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE|PIPE_READMODE_BYTE|PIPE_WAIT|PIPE_REJECT_REMOTE_CLIENTS,1,NC_MAX_JSON+32,48,NC_PIPE_TIMEOUT_MS,&sa);
    LocalFree(sd);
    if(pipe==INVALID_HANDLE_VALUE) return nc_error(error,NC_BUSY,GetLastError(),"The local API is already active or inaccessible.");
    for(;;) {
        OVERLAPPED connect; BOOL connected; DWORD result; HANDLE waits[3]; BOOL stop=FALSE;
        ZeroMemory(&connect,sizeof(connect)); connect.hEvent=CreateEventW(NULL,TRUE,FALSE,NULL);
        if(!connect.hEvent) { CloseHandle(pipe); return nc_error(error,NC_INTERNAL,GetLastError(),"Cannot create connection event."); }
        connected=ConnectNamedPipe(pipe,&connect); result=connected?ERROR_SUCCESS:GetLastError();
        if(!connected && result==ERROR_PIPE_CONNECTED) { connected=TRUE; SetEvent(connect.hEvent); }
        else if(!connected && result!=ERROR_IO_PENDING) { CloseHandle(connect.hEvent); break; }
        waits[0]=connect.hEvent; waits[1]=engine->stop; waits[2]=engine->power;
        while(!connected) {
            result=WaitForMultipleObjects(3,waits,FALSE,NC_MONITOR_INTERVAL_MS); nc_session_tick(engine);
            if(result==WAIT_OBJECT_0) { DWORD ignored; connected=GetOverlappedResult(pipe,&connect,&ignored,FALSE); break; }
            if(result==WAIT_OBJECT_0+1 || result==WAIT_FAILED) { stop=TRUE; break; }
            if(result==WAIT_OBJECT_0+2) { NcError ignored={0}; nc_session_restore(engine,&ignored); ResetEvent(engine->power); }
        }
        if(!connected) { DWORD ignored; CancelIoEx(pipe,&connect); GetOverlappedResult(pipe,&connect,&ignored,TRUE); }
        CloseHandle(connect.hEvent);
        if(stop || WaitForSingleObject(engine->stop,0)==WAIT_OBJECT_0) break;
        if(connected) {
            NcRequest request; NcResponse response={NC_MAGIC,NC_PROTOCOL,sizeof(NcResponse),0,0,{0}};
            NcJson json; NcError request_error={0}; ULONG client_pid=0;
            nc_json_init(&json);
            if(pipe_io(pipe,engine->stop,&request,sizeof(request),FALSE)) {
                if(!GetNamedPipeClientProcessId(pipe,&client_pid)) nc_error(&request_error,NC_OWNER,GetLastError(),"Cannot identify the API client.");
                else dispatch(engine,&request,client_pid,&json,&request_error);
                if(json.failed) nc_error(&request_error,NC_INTERNAL,0,"Response exceeds its fixed size limit.");
                if(request_error.code!=NC_OK) {
                    nc_json_init(&json); nc_json_add(&json,"{\"error\":{\"code\":"); nc_json_string(&json,nc_code_name(request_error.code));
                    nc_json_add(&json,",\"win32\":%lu,\"message\":",request_error.win32); nc_json_string(&json,request_error.message); nc_json_add(&json,"}}");
                }
                response.status=(DWORD)request_error.code; response.body_size=(DWORD)json.used;
                if(pipe_io(pipe,engine->stop,&response,sizeof(response),TRUE) &&
                   pipe_io(pipe,engine->stop,json.data,(DWORD)json.used,TRUE)) {
                    BYTE acknowledgement=0;
                    /* DisconnectNamedPipe discards unread buffered data. Bound the drain
                       with a client acknowledgement instead of an unbounded flush. */
                    pipe_io(pipe,engine->stop,&acknowledgement,1,FALSE);
                }
            }
        }
        DisconnectNamedPipe(pipe);
    }
    CloseHandle(pipe); return NC_OK;
}
