// SPDX-License-Identifier: GPL-3.0-only
#ifndef NUC_H
#define NUC_H
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
_Static_assert(sizeof(void *)==8,"Build the Windows x64 target");

#define NC_VERSION "0.3.0"
#define NC_MAGIC 0x3143554eU
#define NC_PROTOCOL 1U
#define NC_EC_SIZE 94
#define NC_PROFILE_SIZE 90
#define NC_MAX_JSON 8192
#define NC_MAX_LEASE 15U
#define NC_MIN_LEASE 1U
#define NC_MIN_DUTY 50U
#define NC_MAX_DUTY 99U
#define NC_MIN_THRESHOLD 1U
#define NC_MAX_THRESHOLD 99U
#define NC_THRESHOLD_DELTA 5U
#define NC_CPU_LIMIT 85U
#define NC_GPU_LIMIT 80U
#define NC_MIN_FAN_RPM 1U
#define NC_MAX_FAN_RPM 11999U
#define NC_MONITOR_INTERVAL_MS 250U
#define NC_PIPE_TIMEOUT_MS 3000U
#define NC_COMMAND_ACK_MS 5000U
#define NC_WRITER L"Global\\NucControl.Writer.NUC11PH"

typedef enum {
    NC_OK, NC_INVALID, NC_READ_ONLY, NC_HARDWARE, NC_UNVERIFIED, NC_BUSY,
    NC_EXTERNAL_MANUAL, NC_THERMAL, NC_FAN, NC_EXPIRED, NC_OWNER,
    NC_NOT_CONFIRMED, NC_RESTORE_FAILED, NC_TIMEOUT, NC_INTERNAL
} NcCode;
typedef struct { NcCode code; DWORD win32; char message[192]; } NcError;
enum { NC_STATUS=1, NC_CAPABILITIES=2, NC_PROFILES=3, NC_SAFEGUARDS=4, NC_DUTY=10, NC_PROFILE=11,
       NC_THRESHOLDS=12, NC_RENEW=13, NC_RESTORE=14, NC_TEST_FAULT=100 };
typedef struct {
    uint32_t magic,version,size,command;
    uint32_t values[4];
    uint8_t lease[16];
} NcRequest;
typedef struct { uint32_t magic,version,size,body_size,status,reserved[3]; } NcResponse;
_Static_assert(sizeof(NcRequest)==48,"Request ABI");
_Static_assert(sizeof(NcResponse)==32,"Response ABI");

typedef struct { char data[NC_MAX_JSON]; size_t used; BOOL failed; } NcJson;
typedef struct {
    WCHAR path[1024],model[128],board[128],bios[256],driver[1024];
    BOOL verified;
#ifdef NUC_TESTING
    HANDLE fake_map;
    BYTE *fake;
#endif
} NcHardware;

typedef struct {
    DWORD magic;
    BYTE original[94],profile[90];
    volatile LONG ready,sequence,ack,code,active,restored,reason;
    DWORD win32;
    char error[192];
    NcRequest request;
    ULONGLONG deadline,request_deadline;
} NcShared;

typedef struct {
    HANDLE map,request_event,reply_event,stop_event,process,writer;
    NcShared *shared;
    DWORD owner_pid;
    BYTE lease[16];
    LONG last_reason;
    BOOL last_restored;
} NcSession;

typedef struct {
    NcHardware hardware;
    NcSession session;
    BOOL allow_write;
    WCHAR owner_sid[192],pipe_name[256];
    HANDLE stop,power,stopped;
    DWORD power_epoch;
    ULONGLONG started;
    BYTE storage_json[4096];
    ULONGLONG storage_stamp;
} NcEngine;

NcCode nc_error(NcError *error,NcCode code,DWORD win32,const char *message);
const char *nc_code_name(NcCode code);
const char *nc_reason_name(LONG reason);
void nc_json_init(NcJson *json);
void nc_json_add(NcJson *json,const char *format,...);
void nc_json_string(NcJson *json,const char *text);
void nc_json_wide(NcJson *json,const WCHAR *text);
BOOL nc_current_sid(WCHAR *output,DWORD chars);
BOOL nc_security(const WCHAR *sid,SECURITY_ATTRIBUTES *sa,PSECURITY_DESCRIPTOR *sd);
BOOL nc_manual(const BYTE ec[94]);
NcCode nc_capture(const BYTE ec[94],BYTE profile[90],NcError *error);
void nc_expected(const BYTE profile[90],const BYTE original[94],BYTE expected[94]);
BOOL nc_valid_profile(const BYTE profile[90]);
NcCode nc_factory(DWORD index,BYTE profile[90],NcError *error);
NcCode nc_thresholds(const BYTE original[90],DWORD primary,DWORD secondary,BYTE target[90],NcError *error);
NcCode nc_validate_request(const NcRequest *request,NcError *error);
NcCode nc_thermal(const BYTE temps[15],const BYTE fans[18],NcError *error);

NcCode nc_hw_open(NcHardware *hardware,NcError *error);
void nc_hw_close(NcHardware *hardware);
NcCode nc_hw_check(NcHardware *hardware,NcError *error);
NcCode nc_hw_ec(NcHardware *hardware,BYTE ec[94],NcError *error);
NcCode nc_hw_temps(NcHardware *hardware,BYTE temps[15],NcError *error);
NcCode nc_hw_fans(NcHardware *hardware,BYTE fans[18],NcError *error);
NcCode nc_hw_profile(NcHardware *hardware,const BYTE profile[90],NcError *error);
NcCode nc_hw_duty(NcHardware *hardware,DWORD percent,NcError *error);
void nc_hw_storage(NcHardware *hardware,NcJson *json);
NcCode nc_hw_restore(NcHardware *hardware,const BYTE profile[90],const BYTE original[94],NcError *error);
NcCode nc_session_command(NcEngine *engine,const NcRequest *request,DWORD client_pid,NcError *error);
NcCode nc_session_restore(NcEngine *engine,NcError *error);
void nc_session_sync(NcSession *session);
void nc_session_tick(NcEngine *engine);
int nc_guardian(int argc,WCHAR **argv);
NcCode nc_serve(NcEngine *engine,NcError *error);
void nc_status_json(NcEngine *engine,NcJson *json,NcError *error);
void nc_capabilities_json(NcEngine *engine,NcJson *json);
void nc_profiles_json(NcJson *json);
void nc_safeguards_json(NcEngine *engine,NcJson *json);
int nc_self_test(void);
#endif
