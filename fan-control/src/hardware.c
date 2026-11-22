// SPDX-License-Identifier: GPL-3.0-only
#include "nuc.h"
#include <setupapi.h>
#include <bcrypt.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

static const GUID nc_guid={0x1acd3a40,0xc4db,0x4542,{0x9c,0x73,0x98,0x98,0xb0,0xd8,0xff,0x6e}};
static DWORD read32(const BYTE *p) { DWORD value; memcpy(&value,p,4); return value; }
static BOOL reg_string(HKEY root,const WCHAR *key,const WCHAR *name,WCHAR *out,DWORD chars) {
    DWORD bytes=chars*sizeof(WCHAR); LONG result;
    ZeroMemory(out,bytes);
    result=RegGetValueW(root,key,name,RRF_RT_REG_SZ|RRF_RT_REG_EXPAND_SZ,NULL,out,&bytes);
    return result==ERROR_SUCCESS && bytes>=sizeof(WCHAR) && out[chars-1]==0;
}

#ifndef NUC_TESTING
static BOOL driver_hash(const WCHAR *path) {
    static const BYTE expected[32]={0x49,0x52,0x6c,0x5e,0x90,0x89,0xac,0x9a,0x7e,0x2e,0x0e,0xf7,0x63,0x29,0xa9,0x7a,
        0xc2,0x3f,0x34,0xd0,0x2e,0x89,0x13,0x79,0x68,0x14,0xb8,0x37,0x7a,0xcc,0x1e,0xed};
    BCRYPT_ALG_HANDLE algorithm=NULL; BCRYPT_HASH_HANDLE hash=NULL;
    HANDLE file=INVALID_HANDLE_VALUE; BYTE buffer[4096],digest[32]; DWORD count; BOOL ok=FALSE;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,NULL,0)<0) goto end;
    if(BCryptCreateHash(algorithm,&hash,NULL,0,NULL,0,0)<0) goto end;
    file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,0,NULL);
    if(file==INVALID_HANDLE_VALUE) goto end;
    for(;;) {
        if(!ReadFile(file,buffer,sizeof(buffer),&count,NULL)) goto end;
        if(!count) break;
        if(BCryptHashData(hash,buffer,count,0)<0) goto end;
    }
    if(BCryptFinishHash(hash,digest,sizeof(digest),0)<0) goto end;
    ok=memcmp(digest,expected,32)==0;
end:
    if(file!=INVALID_HANDLE_VALUE) CloseHandle(file);
    if(hash) BCryptDestroyHash(hash);
    if(algorithm) BCryptCloseAlgorithmProvider(algorithm,0);
    return ok;
}
static NcCode identity(NcHardware *h,NcError *error) {
    WCHAR raw[1024],root[MAX_PATH],lower[1024];
    if(!reg_string(HKEY_LOCAL_MACHINE,L"HARDWARE\\DESCRIPTION\\System\\BIOS",L"SystemProductName",h->model,_countof(h->model)) ||
       !reg_string(HKEY_LOCAL_MACHINE,L"HARDWARE\\DESCRIPTION\\System\\BIOS",L"BaseBoardProduct",h->board,_countof(h->board)) ||
       !reg_string(HKEY_LOCAL_MACHINE,L"HARDWARE\\DESCRIPTION\\System\\BIOS",L"BIOSVersion",h->bios,_countof(h->bios)) ||
       !reg_string(HKEY_LOCAL_MACHINE,L"SYSTEM\\CurrentControlSet\\Services\\PerformanceDriver",L"ImagePath",raw,_countof(raw)))
        return nc_error(error,NC_HARDWARE,GetLastError(),"Cannot identify board/BIOS/Intel driver.");
    if(_wcsnicmp(raw,L"\\SystemRoot\\",12)!=0 || !GetWindowsDirectoryW(root,_countof(root)) ||
       swprintf_s(h->driver,_countof(h->driver),L"%s\\%s",root,raw+12)<0)
        return nc_error(error,NC_UNVERIFIED,0,"Unexpected Intel driver path.");
    wcscpy_s(lower,_countof(lower),h->driver); _wcslwr_s(lower,_countof(lower));
    if(!wcsstr(lower,L"\\driverstore\\filerepository\\performancedriver.inf_amd64_") ||
       !wcsrchr(lower,L'\\') || wcscmp(wcsrchr(lower,L'\\'),L"\\performancedriver.sys")!=0)
        return nc_error(error,NC_UNVERIFIED,0,"Unexpected Intel driver location.");
    h->verified=wcscmp(h->model,L"NUC11PHi7")==0 && wcscmp(h->board,L"NUC11PHBi7")==0 &&
        wcscmp(h->bios,L"PHTGL579.0079.2024.1014.1949")==0 && driver_hash(h->driver);
    return NC_OK;
}
#endif

NcCode nc_hw_open(NcHardware *h,NcError *error) {
    ZeroMemory(h,sizeof(*h));
#ifdef NUC_TESTING
    {
        WCHAR name[128]; BOOL created=FALSE;
        if(!GetEnvironmentVariableW(L"NUC_ENGINE_TEST_MAP",name,_countof(name))) {
            swprintf_s(name,_countof(name),L"Local\\NucControl.Test.%lu",GetCurrentProcessId());
            SetEnvironmentVariableW(L"NUC_ENGINE_TEST_MAP",name); created=TRUE;
        }
        h->fake_map=CreateFileMappingW(INVALID_HANDLE_VALUE,NULL,PAGE_READWRITE,0,256,name);
        if(!h->fake_map) return nc_error(error,NC_INTERNAL,GetLastError(),"Test mapping creation failed.");
        h->fake=(BYTE *)MapViewOfFile(h->fake_map,FILE_MAP_ALL_ACCESS,0,0,256);
        if(!h->fake) { CloseHandle(h->fake_map); return nc_error(error,NC_INTERNAL,GetLastError(),"Test mapping failed."); }
        if(created) {
            BYTE profile[90]; nc_factory(1,profile,NULL); ZeroMemory(h->fake,256); nc_expected(profile,h->fake,h->fake);
            h->fake[94]=45; h->fake[95]=55; h->fake[96]=45;
            h->fake[109]=1; h->fake[110]=5; h->fake[111]=220;
            h->fake[112]=2; h->fake[113]=5; h->fake[114]=120;
        }
        wcscpy_s(h->model,_countof(h->model),L"SIMULATED NUC"); h->verified=TRUE; return NC_OK;
    }
#else
    {
        HDEVINFO devices; DWORD i; NcCode code=identity(h,error);
        if(code!=NC_OK) return code;
        devices=SetupDiGetClassDevsW(&nc_guid,NULL,NULL,DIGCF_PRESENT|DIGCF_DEVICEINTERFACE);
        if(devices==INVALID_HANDLE_VALUE) return nc_error(error,NC_HARDWARE,GetLastError(),"Intel device enumeration failed.");
        for(i=0;i<16;i++) {
            SP_DEVICE_INTERFACE_DATA interface_data={0}; SP_DEVINFO_DATA info={0}; DWORD size=0;
            PSP_DEVICE_INTERFACE_DETAIL_DATA_W detail=NULL;
            WCHAR instance[512],service[128];
            interface_data.cbSize=sizeof(interface_data); info.cbSize=sizeof(info);
            if(!SetupDiEnumDeviceInterfaces(devices,NULL,&nc_guid,i,&interface_data)) break;
            SetupDiGetDeviceInterfaceDetailW(devices,&interface_data,NULL,0,&size,NULL);
            if(size<8 || size>4096) continue;
            detail=(PSP_DEVICE_INTERFACE_DETAIL_DATA_W)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,size);
            if(!detail) break;
            detail->cbSize=sizeof(*detail);
            if(SetupDiGetDeviceInterfaceDetailW(devices,&interface_data,detail,size,NULL,&info) &&
               SetupDiGetDeviceInstanceIdW(devices,&info,instance,_countof(instance),NULL) &&
               SetupDiGetDeviceRegistryPropertyW(devices,&info,SPDRP_SERVICE,NULL,(BYTE *)service,sizeof(service),NULL) &&
               _wcsnicmp(instance,L"ACPI\\INTC1036\\",14)==0 && _wcsicmp(service,L"PerformanceDriver")==0)
                wcscpy_s(h->path,_countof(h->path),detail->DevicePath);
            HeapFree(GetProcessHeap(),0,detail);
            if(h->path[0]) break;
        }
        SetupDiDestroyDeviceInfoList(devices);
        if(!h->path[0]) return nc_error(error,NC_HARDWARE,ERROR_NOT_FOUND,"Intel INTC1036 interface not found.");
        return NC_OK;
    }
#endif
}
void nc_hw_close(NcHardware *h) {
#ifdef NUC_TESTING
    if(h->fake) UnmapViewOfFile(h->fake);
    if(h->fake_map) CloseHandle(h->fake_map);
#else
    (void)h;
#endif
}
NcCode nc_hw_check(NcHardware *h,NcError *error) {
#ifdef NUC_TESTING
    (void)h; (void)error; return NC_OK;
#else
    HKEY key; LONG result; DWORD start=0,size=sizeof(start); SC_HANDLE scm=NULL,service=NULL;
    SERVICE_STATUS_PROCESS state; DWORD needed; NcCode code=identity(h,error);
    if(code!=NC_OK) return code;
    if(!h->verified) return nc_error(error,NC_UNVERIFIED,0,"Board, BIOS or Intel driver hash does not match the validated configuration.");
    result=RegOpenKeyW(HKEY_LOCAL_MACHINE,L"SYSTEM\\CurrentControlSet\\Services\\WinRing0_1_2_0",&key);
    if(result==ERROR_SUCCESS) { RegCloseKey(key); return nc_error(error,NC_UNVERIFIED,0,"WinRing0 must remain absent."); }
    if(result!=ERROR_FILE_NOT_FOUND) return nc_error(error,NC_UNVERIFIED,(DWORD)result,"Cannot verify WinRing0 absence.");
    result=RegGetValueW(HKEY_LOCAL_MACHINE,L"SYSTEM\\CurrentControlSet\\Services\\NucSoftwareStudioService",L"Start",RRF_RT_REG_DWORD,NULL,&start,&size);
    if(result!=ERROR_SUCCESS || start!=4) return nc_error(error,NC_UNVERIFIED,(DWORD)result,"NUC Software Studio must remain disabled.");
    scm=OpenSCManagerW(NULL,NULL,SC_MANAGER_CONNECT);
    if(scm) service=OpenServiceW(scm,L"NucSoftwareStudioService",SERVICE_QUERY_STATUS);
    if(!service || !QueryServiceStatusEx(service,SC_STATUS_PROCESS_INFO,(BYTE *)&state,sizeof(state),&needed)) {
        DWORD e=GetLastError(); if(service) CloseServiceHandle(service); if(scm) CloseServiceHandle(scm);
        return nc_error(error,NC_HARDWARE,e,"Cannot check NUC Software Studio state.");
    }
    CloseServiceHandle(service); CloseServiceHandle(scm);
    if(state.dwCurrentState!=SERVICE_STOPPED) return nc_error(error,NC_UNVERIFIED,0,"NUC Software Studio must remain stopped.");
    return NC_OK;
#endif
}

#ifndef NUC_TESTING
static NcCode intel(NcHardware *h,DWORD command,const BYTE *input,DWORD input_size,BYTE *output,DWORD output_size,BOOL write,NcError *error) {
    HANDLE file,mutex; DWORD returned=0,wait,error_code=0; BOOL ok;
    mutex=CreateMutexW(NULL,FALSE,L"Global\\Access_ISABUS.HTP.Method");
    if(!mutex && GetLastError()==ERROR_ACCESS_DENIED) mutex=OpenMutexW(SYNCHRONIZE|MUTEX_MODIFY_STATE,FALSE,L"Global\\Access_ISABUS.HTP.Method");
    if(!mutex) return nc_error(error,NC_HARDWARE,GetLastError(),"Cannot open the Intel bus lock.");
    wait=WaitForSingleObject(mutex,2000);
    if(wait!=WAIT_OBJECT_0 && wait!=WAIT_ABANDONED) {
        CloseHandle(mutex); return nc_error(error,NC_BUSY,0,"Intel bus busy.");
    }
    /* The device can reject a concurrent open even with FILE_SHARE_*.
       Serialize the entire open/IOCTL/close lifetime across both processes. */
    file=CreateFileW(h->path,GENERIC_READ|(write?GENERIC_WRITE:0),FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
    if(file==INVALID_HANDLE_VALUE) {
        error_code=GetLastError(); ReleaseMutex(mutex); CloseHandle(mutex);
        return nc_error(error,NC_HARDWARE,error_code,"Cannot open the Intel device interface.");
    }
    ok=DeviceIoControl(file,command,(void *)input,input_size,output,output_size,&returned,NULL);
    if(!ok) error_code=GetLastError();
    CloseHandle(file); ReleaseMutex(mutex); CloseHandle(mutex);
    if(!ok) return nc_error(error,NC_HARDWARE,error_code,"Intel IOCTL failed.");
    if(output_size && returned!=output_size) return nc_error(error,NC_HARDWARE,0,"Truncated Intel IOCTL response.");
    return NC_OK;
}
#endif
NcCode nc_hw_ec(NcHardware *h,BYTE ec[94],NcError *error) {
#ifdef NUC_TESTING
    (void)error; memcpy(ec,h->fake,94); return NC_OK;
#else
    return intel(h,0x9c406020,NULL,0,ec,94,FALSE,error);
#endif
}
NcCode nc_hw_temps(NcHardware *h,BYTE temps[15],NcError *error) {
#ifdef NUC_TESTING
    (void)error; memcpy(temps,h->fake+94,15); return NC_OK;
#else
    return intel(h,0x9c406048,NULL,0,temps,15,FALSE,error);
#endif
}
NcCode nc_hw_fans(NcHardware *h,BYTE fans[18],NcError *error) {
#ifdef NUC_TESTING
    (void)error; memcpy(fans,h->fake+109,18); return NC_OK;
#else
    BYTE selector=2; return intel(h,0x9c406024,&selector,1,fans,18,FALSE,error);
#endif
}
NcCode nc_hw_profile(NcHardware *h,const BYTE profile[90],NcError *error) {
    if(!nc_valid_profile(profile)) return nc_error(error,NC_INVALID,0,"Invalid automatic profile.");
#ifdef NUC_TESTING
    nc_expected(profile,h->fake,h->fake); return NC_OK;
#else
    return intel(h,0x9c40a01c,profile,90,NULL,0,TRUE,error);
#endif
}
NcCode nc_hw_duty(NcHardware *h,DWORD percent,NcError *error) {
    if(percent<NC_MIN_DUTY || percent>NC_MAX_DUTY) return nc_error(error,NC_INVALID,0,"Manual duty must be 50..99 percent.");
#ifdef NUC_TESTING
    h->fake[12]=(BYTE)(128|percent); return NC_OK;
#else
    BYTE payload[3]={2,1,(BYTE)percent}; return intel(h,0x9c40a040,payload,3,NULL,0,TRUE,error);
#endif
}
NcCode nc_hw_restore(NcHardware *h,const BYTE profile[90],const BYTE original[94],NcError *error) {
    BYTE current[94]; unsigned i;
    for(i=0;i<3;i++) {
        if(nc_hw_ec(h,current,error)==NC_OK && memcmp(current,original,94)==0) return NC_OK;
        if(nc_hw_profile(h,profile,error)==NC_OK && nc_hw_ec(h,current,error)==NC_OK && memcmp(current,original,94)==0) return NC_OK;
        Sleep(100);
    }
    return nc_error(error,NC_RESTORE_FAILED,error?error->win32:0,"Original 94-byte configuration has not been restored.");
}

void nc_hw_storage(NcHardware *h,NcJson *json) {
#ifdef NUC_TESTING
    (void)h; nc_json_add(json,"[{\"id\":\"ssd_0\",\"model\":\"SIMULATED\",\"available\":true,\"sensors\":[{\"index\":0,\"celsius\":35}]}]");
#else
    unsigned disk; BOOL first=TRUE; (void)h; nc_json_add(json,"[");
    for(disk=0;disk<16;disk++) {
        WCHAR path[64]; HANDLE file; BYTE descriptor[4096],query[560]={0},log[560];
        DWORD returned=0,offset; BOOL ok;
        DWORD property[3]={0};
        swprintf_s(path,_countof(path),L"\\\\.\\PhysicalDrive%u",disk);
        file=CreateFileW(path,0,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
        if(file==INVALID_HANDLE_VALUE) continue;
        ok=DeviceIoControl(file,0x2d1400,property,sizeof(property),descriptor,sizeof(descriptor),&returned,NULL);
        if(!ok || returned<36 || returned>sizeof(descriptor) || read32(descriptor+28)!=17) { CloseHandle(file); continue; }
        offset=read32(descriptor+16);
        if(offset<36 || offset>=returned || !memchr(descriptor+offset,0,returned-offset)) { CloseHandle(file); continue; }
        if(!first) nc_json_add(json,","); first=FALSE;
        nc_json_add(json,"{\"id\":\"ssd_%u\",\"model\":",disk); nc_json_string(json,(char *)descriptor+offset);
        { DWORD v=50; memcpy(query,&v,4); v=3; memcpy(query+8,&v,4); v=2; memcpy(query+12,&v,4); memcpy(query+16,&v,4);
          v=40; memcpy(query+24,&v,4); v=512; memcpy(query+28,&v,4); }
        ok=DeviceIoControl(file,0x2d1400,query,sizeof(query),log,sizeof(log),&returned,NULL); CloseHandle(file);
        if(ok && returned>=48 && returned<=sizeof(log) && read32(log)==48 && read32(log+4)==48 && read32(log+8)==3 && read32(log+12)==2) {
            DWORD data_offset=read32(log+24),length=read32(log+28); unsigned sensor; BOOL first_sensor=TRUE;
            if(data_offset>=40 && length>=512 && data_offset<=returned-8 && length<=returned-8-data_offset) {
                nc_json_add(json,",\"available\":true,\"sensors\":[");
                for(sensor=0;sensor<9;sensor++) {
                    DWORD at=8+data_offset+(sensor?200+2*(sensor-1):1);
                    unsigned kelvin=(unsigned)log[at]+256U*log[at+1];
                    if(kelvin<223 || kelvin>=398) continue;
                    if(!first_sensor) nc_json_add(json,","); first_sensor=FALSE;
                    nc_json_add(json,"{\"index\":%u,\"celsius\":%d}",sensor,(int)kelvin-273);
                }
                nc_json_add(json,"]}"); continue;
            }
        }
        nc_json_add(json,",\"available\":false,\"sensors\":[]}");
    }
    nc_json_add(json,"]");
#endif
}
