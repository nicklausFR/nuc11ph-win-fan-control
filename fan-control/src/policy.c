// SPDX-License-Identifier: GPL-3.0-only
#include "nuc.h"
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <sddl.h>

NcCode nc_error(NcError *error,NcCode code,DWORD win32,const char *message) {
    if(error) { error->code=code; error->win32=win32; strcpy_s(error->message,sizeof(error->message),message); }
    return code;
}
const char *nc_code_name(NcCode code) {
    static const char *names[]={"ok","invalid_request","read_only","hardware_error","unverified_hardware",
        "writer_busy","external_manual","temperature_limit_or_unavailable","fan_unavailable","lease_expired",
        "lease_owner_mismatch","write_not_confirmed","restoration_failed","timeout","internal_error"};
    return (unsigned)code<_countof(names)?names[code]:"internal_error";
}
const char *nc_reason_name(LONG reason) {
    static const char *names[]={"none","client_release","lease_expired","engine_exited","client_exited",
        "temperature_limit_or_unavailable","fan_unavailable","command_failed","engine_stopping","power_change"};
    return (unsigned)reason<_countof(names)?names[reason]:"unknown";
}
void nc_json_init(NcJson *json) { ZeroMemory(json,sizeof(*json)); }
void nc_json_add(NcJson *json,const char *format,...) {
    va_list args; int count;
    if(json->failed) return;
    va_start(args,format);
    count=vsnprintf(json->data+json->used,sizeof(json->data)-json->used,format,args);
    va_end(args);
    if(count<0 || (size_t)count>=sizeof(json->data)-json->used) { json->failed=TRUE; return; }
    json->used+=(size_t)count;
}
void nc_json_string(NcJson *json,const char *text) {
    const unsigned char *p=(const unsigned char *)text;
    nc_json_add(json,"\"");
    for(;*p;p++) {
        if(*p=='"'||*p=='\\') nc_json_add(json,"\\%c",*p);
        else if(*p<32) nc_json_add(json,"\\u%04x",(unsigned)*p);
        else nc_json_add(json,"%c",*p);
    }
    nc_json_add(json,"\"");
}
void nc_json_wide(NcJson *json,const WCHAR *text) {
    char buffer[4096];
    if(!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,text,-1,buffer,sizeof(buffer),NULL,NULL)) {
        json->failed=TRUE; return;
    }
    nc_json_string(json,buffer);
}
BOOL nc_current_sid(WCHAR *output,DWORD chars) {
    HANDLE token=NULL; DWORD size=0; TOKEN_USER *user=NULL; LPWSTR sid=NULL; BOOL ok=FALSE;
    if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token)) return FALSE;
    GetTokenInformation(token,TokenUser,NULL,0,&size);
    user=(TOKEN_USER *)HeapAlloc(GetProcessHeap(),0,size);
    if(user && GetTokenInformation(token,TokenUser,user,size,&size) && ConvertSidToStringSidW(user->User.Sid,&sid))
        ok=wcscpy_s(output,chars,sid)==0;
    if(sid) LocalFree(sid);
    if(user) HeapFree(GetProcessHeap(),0,user);
    CloseHandle(token); return ok;
}
BOOL nc_security(const WCHAR *sid,SECURITY_ATTRIBUTES *sa,PSECURITY_DESCRIPTOR *sd) {
    WCHAR sddl[512],account[256],domain[256]; PSID parsed=NULL;
    DWORD account_size=_countof(account),domain_size=_countof(domain); SID_NAME_USE kind;
    if(!ConvertStringSidToSidW(sid,&parsed)) return FALSE;
    if(!IsValidSid(parsed)) { LocalFree(parsed); return FALSE; }
    if(!LookupAccountSidW(NULL,parsed,account,&account_size,domain,&domain_size,&kind) || kind!=SidTypeUser) {
        LocalFree(parsed); return FALSE;
    }
    LocalFree(parsed);
    if(swprintf_s(sddl,_countof(sddl),L"D:P(A;;GA;;;SY)(A;;GA;;;%s)",sid)<0) return FALSE;
    if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,SDDL_REVISION_1,sd,NULL)) return FALSE;
    sa->nLength=sizeof(*sa); sa->lpSecurityDescriptor=*sd; sa->bInheritHandle=FALSE; return TRUE;
}
BOOL nc_manual(const BYTE ec[94]) {
    unsigned i; for(i=0;i<6;i++) if(ec[i*16+12]&128) return TRUE; return FALSE;
}
BOOL nc_valid_profile(const BYTE profile[90]) {
    unsigned i;
    if(profile[0]!=0 || profile[1]!=1 || (profile[2]!=26 && profile[2]!=27)) return FALSE;
    for(i=0;i<6;i++) if((profile[3+i*14+12]&128) || profile[3+i*14+3]>100 || profile[3+i*14+9]>100) return FALSE;
    return TRUE;
}
NcCode nc_capture(const BYTE ec[94],BYTE profile[90],NcError *error) {
    unsigned i;
    if(nc_manual(ec)) return nc_error(error,NC_EXTERNAL_MANUAL,0,"An automatic baseline is required.");
    ZeroMemory(profile,90); profile[1]=1; profile[2]=26;
    for(i=0;i<6;i++) memcpy(profile+3+i*14,ec+i*16,14);
    if(!nc_valid_profile(profile)) return nc_error(error,NC_INVALID,0,"Invalid baseline profile.");
    return NC_OK;
}
void nc_expected(const BYTE profile[90],const BYTE original[94],BYTE expected[94]) {
    unsigned i; if(expected!=original) memcpy(expected,original,94);
    for(i=0;i<6;i++) memcpy(expected+i*16,profile+3+i*14,14);
}
NcCode nc_factory(DWORD index,BYTE profile[90],NcError *error) {
    static const BYTE bodies[3][14]={
        {1,0,55,30,1,55,6,0,42,30,1,42,0,198},
        {1,0,50,30,1,50,6,0,42,35,1,42,0,198},
        {1,0,50,40,1,50,6,0,42,45,1,42,0,208}};
    if(index>2) return nc_error(error,NC_INVALID,0,"Profile must be quiet, balanced or cool.");
    ZeroMemory(profile,90); profile[1]=1; profile[2]=26; memcpy(profile+3,bodies[index],14); return NC_OK;
}
NcCode nc_thresholds(const BYTE original[90],DWORD primary,DWORD secondary,BYTE target[90],NcError *error) {
    if(!nc_valid_profile(original) || original[3]!=1 || original[9]!=6 ||
        primary<NC_MIN_THRESHOLD || primary>NC_MAX_THRESHOLD || secondary<NC_MIN_THRESHOLD || secondary>NC_MAX_THRESHOLD ||
        (int)primary-(int)original[5]>(int)NC_THRESHOLD_DELTA || (int)original[5]-(int)primary>(int)NC_THRESHOLD_DELTA ||
        (int)secondary-(int)original[11]>(int)NC_THRESHOLD_DELTA || (int)original[11]-(int)secondary>(int)NC_THRESHOLD_DELTA)
        return nc_error(error,NC_INVALID,0,"Encoded thresholds must stay within +/-5 of the original baseline.");
    memcpy(target,original,90); target[2]=27; target[5]=(BYTE)primary; target[11]=(BYTE)secondary; return NC_OK;
}
NcCode nc_validate_request(const NcRequest *r,NcError *error) {
    DWORD ttl=0; BOOL zero_lease=TRUE; unsigned i;
    if(r->magic!=NC_MAGIC || r->version!=NC_PROTOCOL || r->size!=sizeof(*r))
        return nc_error(error,NC_INVALID,0,"Invalid protocol header.");
    for(i=0;i<16;i++) if(r->lease[i]) zero_lease=FALSE;
    switch(r->command) {
        case NC_STATUS: case NC_CAPABILITIES: case NC_PROFILES: case NC_SAFEGUARDS: case NC_RESTORE:
            if(r->values[0]||r->values[1]||r->values[2]||r->values[3]||!zero_lease) goto invalid;
            return NC_OK;
        case NC_DUTY:
            if(r->values[0]<NC_MIN_DUTY || r->values[0]>NC_MAX_DUTY || r->values[2] || r->values[3]) goto invalid;
            ttl=r->values[1]; break;
        case NC_PROFILE:
            if(r->values[0]>2 || r->values[2] || r->values[3]) goto invalid;
            ttl=r->values[1]; break;
        case NC_THRESHOLDS:
            if(r->values[0]<NC_MIN_THRESHOLD || r->values[0]>NC_MAX_THRESHOLD || r->values[1]<NC_MIN_THRESHOLD || r->values[1]>NC_MAX_THRESHOLD || r->values[3]) goto invalid;
            ttl=r->values[2]; break;
        case NC_RENEW:
            if(zero_lease || r->values[1]||r->values[2]||r->values[3]) goto invalid;
            ttl=r->values[0]; break;
#ifdef NUC_TESTING
        case NC_TEST_FAULT:
            if(r->values[0]>4 || r->values[1]>255 || r->values[2] || r->values[3]) goto invalid;
            return NC_OK;
#endif
        default: goto invalid;
    }
    if(ttl<NC_MIN_LEASE || ttl>NC_MAX_LEASE) goto invalid;
    return NC_OK;
invalid:
    return nc_error(error,NC_INVALID,0,"Unknown command, invalid range or nonzero reserved field.");
}
NcCode nc_thermal(const BYTE temps[15],const BYTE fans[18],NcError *error) {
    unsigned i; BOOL cpu=FALSE,gpu=FALSE;
    if(temps[1]==0 || temps[1]>=NC_CPU_LIMIT || temps[0]==0 || temps[0]>=NC_GPU_LIMIT)
        return nc_error(error,NC_THERMAL,0,"CPU/GPU temperature unavailable or at the manual-control limit (85/80 C).");
    for(i=0;i<18;i+=3) {
        unsigned rpm=(unsigned)fans[i+1]*256+fans[i+2];
        if(rpm>=NC_MIN_FAN_RPM && rpm<=NC_MAX_FAN_RPM) { if(fans[i]==1) cpu=TRUE; if(fans[i]==2) gpu=TRUE; }
    }
    if(!cpu||!gpu) return nc_error(error,NC_FAN,0,"Both confirmed fan RPM readings are required.");
    return NC_OK;
}
