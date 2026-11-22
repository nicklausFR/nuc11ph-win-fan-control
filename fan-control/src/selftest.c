// SPDX-License-Identifier: GPL-3.0-only
#include "nuc.h"
#include <stdio.h>
#include <string.h>

#define CHECK(x) do { total++; if(!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); failed++; } } while(0)
int nc_self_test(void) {
    unsigned total=0,failed=0,i; BYTE ec[94]={0},profile[90],copy[94],target[90],temps[15]={45,55},fans[18]={1,5,220,2,5,120};
    NcError error={0}; NcRequest request={NC_MAGIC,NC_PROTOCOL,sizeof(NcRequest),NC_STATUS,{0},{0}};
    NcJson json;
    CHECK(nc_factory(1,profile,&error)==NC_OK); nc_expected(profile,ec,ec);
    CHECK(ec[2]==50 && ec[8]==42); CHECK(nc_capture(ec,target,&error)==NC_OK); CHECK(memcmp(profile,target,90)==0);
    for(i=0;i<5;i++) { ec[i*16+14]=0xa5; ec[i*16+15]=0x5a; }
    ec[80]=7; CHECK(nc_capture(ec,target,&error)==NC_OK); CHECK(target[73]==7);
    nc_expected(target,ec,copy); CHECK(memcmp(copy,ec,94)==0);
    CHECK(nc_thresholds(profile,51,43,target,&error)==NC_OK);
    CHECK(target[2]==27 && target[5]==51 && target[11]==43);
    CHECK(nc_thresholds(profile,56,42,target,&error)==NC_INVALID);
    CHECK(nc_thresholds(profile,50,48,target,&error)==NC_INVALID);
    CHECK(nc_thresholds(profile,0xffffffff,42,target,&error)==NC_INVALID);
    ec[12]=188; CHECK(nc_capture(ec,target,&error)==NC_EXTERNAL_MANUAL);
    CHECK(nc_validate_request(&request,&error)==NC_OK);
    request.command=NC_PROFILES; CHECK(nc_validate_request(&request,&error)==NC_OK);
    request.command=NC_SAFEGUARDS; CHECK(nc_validate_request(&request,&error)==NC_OK);
    request.values[3]=1; CHECK(nc_validate_request(&request,&error)==NC_INVALID); request.values[3]=0;
    request.command=NC_DUTY; request.values[0]=60; request.values[1]=5;
    CHECK(nc_validate_request(&request,&error)==NC_OK);
    request.values[0]=49; CHECK(nc_validate_request(&request,&error)==NC_INVALID);
    request.values[0]=100; CHECK(nc_validate_request(&request,&error)==NC_INVALID);
    request.values[0]=60; request.values[1]=16; CHECK(nc_validate_request(&request,&error)==NC_INVALID);
    request.values[1]=0; CHECK(nc_validate_request(&request,&error)==NC_INVALID);
    request.command=9999; CHECK(nc_validate_request(&request,&error)==NC_INVALID);
    CHECK(nc_thermal(temps,fans,&error)==NC_OK); temps[1]=85; CHECK(nc_thermal(temps,fans,&error)==NC_THERMAL);
    temps[1]=55; temps[0]=80; CHECK(nc_thermal(temps,fans,&error)==NC_THERMAL);
    temps[0]=0; CHECK(nc_thermal(temps,fans,&error)==NC_THERMAL); temps[0]=45;
    fans[1]=0; fans[2]=0; CHECK(nc_thermal(temps,fans,&error)==NC_FAN);
    nc_json_init(&json); nc_json_string(&json,"a\"b\\c\n"); CHECK(strcmp(json.data,"\"a\\\"b\\\\c\\u000a\"")==0);
    nc_json_init(&json); for(i=0;i<NC_MAX_JSON;i++) nc_json_add(&json,"xxxx"); CHECK(json.failed);
    nc_json_init(&json); nc_profiles_json(&json);
    CHECK(!json.failed && strstr(json.data,"\"quiet\"") && strstr(json.data,"\"cool\""));
    {
        NcEngine *catalog_engine=(NcEngine *)calloc(1,sizeof(NcEngine));
        CHECK(catalog_engine!=NULL);
        if(catalog_engine) {
            nc_json_init(&json); nc_safeguards_json(catalog_engine,&json);
            CHECK(!json.failed && json.used<NC_MAX_JSON);
            CHECK(strstr(json.data,"\"write_enabled\":false")!=NULL);
            CHECK(strstr(json.data,"\"cpu_temperature\"")!=NULL && strstr(json.data,"\"local_api\"")!=NULL);
            free(catalog_engine);
        }
    }
    printf("%u checks, %u failures\n",total,failed); return failed?1:0;
}
