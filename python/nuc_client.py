# SPDX-License-Identifier: GPL-3.0-only
"""Python UI SDK. Only named-pipe IPC; all hardware access and recovery live in C."""
import ctypes as C
import json
import os
import struct
import time
import threading
from contextlib import contextmanager

MAGIC, VERSION, MAX_JSON = 0x3143554E, 1, 8192
REQUEST = struct.Struct("<8I16s")
RESPONSE = struct.Struct("<8I")
U32, PTR, BOOL = C.c_uint32, C.c_void_p, C.c_int32


class NucError(RuntimeError):
    def __init__(self, message, *, code="transport_error", win32=0):
        super().__init__(message)
        self.code, self.win32 = code, win32


class Overlapped(C.Structure):
    _fields_ = [("Internal",C.c_size_t),("InternalHigh",C.c_size_t),
                ("Offset",U32),("OffsetHigh",U32),("hEvent",PTR)]


def integer(value, name, low, high):
    if type(value) is not int or not low<=value<=high:
        raise ValueError(f"{name}: entier attendu entre {low} et {high}")
    return value


class WindowsPipe:
    def __init__(self):
        if os.name!="nt" or C.sizeof(PTR)!=8:
            raise OSError("Windows et Python 64 bits requis")
        self.kernel = C.WinDLL("kernel32",use_last_error=True)
        self.advapi = C.WinDLL("advapi32",use_last_error=True)
        self.close = self.bind(self.kernel,"CloseHandle",BOOL,[PTR])
        self.create_file = self.bind(self.kernel,"CreateFileW",PTR,[C.c_wchar_p,U32,U32,PTR,U32,U32,PTR])
        self.wait_pipe = self.bind(self.kernel,"WaitNamedPipeW",BOOL,[C.c_wchar_p,U32])
        self.event = self.bind(self.kernel,"CreateEventW",PTR,[PTR,BOOL,BOOL,C.c_wchar_p])
        self.wait = self.bind(self.kernel,"WaitForSingleObject",U32,[PTR,U32])
        self.read = self.bind(self.kernel,"ReadFile",BOOL,[PTR,PTR,U32,C.POINTER(U32),C.POINTER(Overlapped)])
        self.write = self.bind(self.kernel,"WriteFile",BOOL,[PTR,PTR,U32,C.POINTER(U32),C.POINTER(Overlapped)])
        self.result = self.bind(self.kernel,"GetOverlappedResult",BOOL,[PTR,C.POINTER(Overlapped),C.POINTER(U32),BOOL])
        self.cancel = self.bind(self.kernel,"CancelIoEx",BOOL,[PTR,C.POINTER(Overlapped)])

    @staticmethod
    def bind(dll,name,result,args):
        function = getattr(dll,name)
        function.restype, function.argtypes = result,args
        return function

    @staticmethod
    def failure(message):
        error = C.get_last_error()
        raise NucError(f"{message} (Windows {error})",win32=error)

    def sid(self):
        current = self.bind(self.kernel,"GetCurrentProcess",PTR,[])()
        open_token = self.bind(self.advapi,"OpenProcessToken",BOOL,[PTR,U32,C.POINTER(PTR)])
        token_info = self.bind(self.advapi,"GetTokenInformation",BOOL,[PTR,U32,PTR,U32,C.POINTER(U32)])
        convert = self.bind(self.advapi,"ConvertSidToStringSidW",BOOL,[PTR,C.POINTER(PTR)])
        free = self.bind(self.kernel,"LocalFree",PTR,[PTR])
        token, needed, text = PTR(),U32(),PTR()
        if not open_token(current,8,C.byref(token)):
            self.failure("Identité Windows inaccessible")
        try:
            token_info(token,1,None,0,C.byref(needed))
            buffer = C.create_string_buffer(needed.value)
            if not token_info(token,1,buffer,len(buffer),C.byref(needed)) or not convert(PTR.from_buffer(buffer),C.byref(text)):
                self.failure("SID Windows inaccessible")
            try:
                return C.wstring_at(text)
            finally:
                free(text)
        finally:
            self.close(token)

    @contextmanager
    def connect(self,path,timeout):
        end = time.monotonic()+timeout
        while True:
            handle = self.create_file(path,0xC0000000,0,None,3,0x40000000,None)
            if handle not in (None,C.c_void_p(-1).value):
                break
            error = C.get_last_error()
            if error!=231 or time.monotonic()>=end:
                self.failure("Moteur C inaccessible")
            self.wait_pipe(path, min(100,max(1,int((end-time.monotonic())*1000))))
        try:
            yield handle
        finally:
            self.close(handle)

    def io(self,handle,data_or_size,write,timeout):
        buffer = C.create_string_buffer(data_or_size,len(data_or_size)) if write else C.create_string_buffer(data_or_size)
        total = len(buffer)
        offset = 0
        end = time.monotonic()+timeout
        while offset<total:
            operation = Overlapped(hEvent=self.event(None,True,False,None))
            if not operation.hEvent:
                self.failure("Événement IPC inaccessible")
            done = U32()
            try:
                function = self.write if write else self.read
                ok = function(handle,C.byref(buffer,offset),total-offset,C.byref(done),C.byref(operation))
                if not ok and C.get_last_error()==997:
                    wait_ms = max(0,int((end-time.monotonic())*1000))
                    if self.wait(operation.hEvent,wait_ms)!=0:
                        self.cancel(handle,C.byref(operation))
                        self.result(handle,C.byref(operation),C.byref(done),True)
                        raise NucError("Délai IPC dépassé",code="timeout")
                    ok = self.result(handle,C.byref(operation),C.byref(done),False)
                if not ok or not 0<done.value<=total-offset:
                    self.failure("Échange IPC interrompu")
                offset += done.value
            finally:
                self.close(operation.hEvent)
        return buffer.raw if not write else None


class NucClient:
    """Keep this object in the Python UI process; leases are tied to that process."""
    def __init__(self, *, timeout=15):
        self._api = WindowsPipe()
        self.pipe_name = r"\\.\pipe\NucControl.v1."+self._api.sid()
        self.timeout = timeout
        self._lease = bytes(16)
        self._owns_lease = False
        self._lock = threading.RLock()

    def _raw(self,request):
        with self._lock:
            return self._exchange(request)

    def _exchange(self,request):
        with self._api.connect(self.pipe_name,self.timeout) as handle:
            self._api.io(handle,request,True,self.timeout)
            header = RESPONSE.unpack(self._api.io(handle,RESPONSE.size,False,self.timeout))
            magic,version,size,length,status,*reserved = header
            if (magic,version,size)!=(MAGIC,VERSION,RESPONSE.size) or not 2<=length<=MAX_JSON or any(reserved):
                raise NucError("Réponse du moteur invalide",code="invalid_response")
            body = json.loads(self._api.io(handle,length,False,self.timeout))
            self._api.io(handle,b"\xac",True,self.timeout)
        if status:
            error = body["error"]
            raise NucError(error["message"],code=error["code"],win32=error.get("win32",0))
        return body

    def _call(self,command,values=(),*,lease=False):
        with self._lock:
            values = tuple(values)+(0,)*(4-len(values))
            body = self._exchange(REQUEST.pack(MAGIC,VERSION,REQUEST.size,command,*values,self._lease if lease else bytes(16)))
            control = body.get("control")
            if control is not None:
                active = control.get("active_lease")
                if command in (10,11,12,13) and active and control.get("owner_pid")==os.getpid():
                    self._lease = bytes.fromhex(active)
                    self._owns_lease = True
                elif command==14 or (self._owns_lease and (not active or active!=self._lease.hex() or control.get("owner_pid")!=os.getpid())):
                    self._lease = bytes(16)
                    self._owns_lease = False
            return body

    def capabilities(self):
        return self._call(2)

    def profiles(self):
        """List captured OEM profiles; querying does not select a profile."""
        return self._call(3)["profiles"]

    def safeguards(self):
        """Read fixed engine policy and its scope; not a live health report."""
        return self._call(4)

    def telemetry(self):
        return self._call(1)

    def set_duty(self,percent,*,lease_seconds=5):
        return self._call(10,(integer(percent,"percent",50,99),integer(lease_seconds,"lease_seconds",1,15)),lease=True)

    def set_profile(self,name,*,lease_seconds=5):
        names = {"quiet":0,"balanced":1,"cool":2}
        if type(name) is not str or name not in names:
            raise ValueError("Profil attendu : quiet, balanced ou cool")
        return self._call(11,(names[name],integer(lease_seconds,"lease_seconds",1,15)),lease=True)

    def set_thresholds(self,primary_min_encoded,secondary_min_encoded,*,lease_seconds=5):
        return self._call(12,(integer(primary_min_encoded,"primary_min_encoded",1,99),
                              integer(secondary_min_encoded,"secondary_min_encoded",1,99),
                              integer(lease_seconds,"lease_seconds",1,15)),lease=True)

    def renew(self,*,lease_seconds=5):
        return self._call(13,(integer(lease_seconds,"lease_seconds",1,15),),lease=True)

    def restore(self):
        result = self._call(14)
        self._lease = bytes(16)
        return result

    def close(self):
        with self._lock:
            if self._owns_lease:
                self.restore()

    def __enter__(self):
        return self

    def __exit__(self,kind,*_):
        if kind is None:
            self.close()
        else:
            try:
                self.close()
            except NucError:
                pass


if __name__=="__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Client de diagnostic du moteur C (lecture seule).")
    parser.add_argument("--watch",action="store_true")
    args = parser.parse_args()
    with NucClient() as client:
        try:
            while True:
                print(json.dumps(client.telemetry(),ensure_ascii=False,indent=2),flush=True)
                if not args.watch:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass
