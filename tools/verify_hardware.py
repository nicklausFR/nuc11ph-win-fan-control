# SPDX-License-Identifier: GPL-3.0-only
"""Read-only by default. --write explicitly tests short C-controlled commands."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
from nuc_client import NucClient, NucError

EXE = ROOT/"fan-control"/"build"/"nuc-engine.exe"
OUTPUT = ROOT/"test-results"/"hardware.json"


def direct_status():
    result = subprocess.run([str(EXE),"status"],capture_output=True,check=True,timeout=5)
    return json.loads(result.stdout)


def wait_for(predicate,timeout=8):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError("Timeout waiting for verified recovery")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write",action="store_true")
    parser.add_argument("--crash",action="store_true")
    args = parser.parse_args()
    if args.crash and not args.write:
        parser.error("--crash requires --write")
    OUTPUT.parent.mkdir(exist_ok=True)
    original = direct_status()
    result = dict(initial=original,checks=[])
    command = [str(EXE),"serve"]+(["--allow-write"] if args.write else [])
    stderr = (OUTPUT.parent/"hardware-engine.log").open("wb")
    process = subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=stderr)
    client = NucClient(timeout=12)
    def ready():
        try:
            return client.capabilities()
        except NucError:
            if process.poll() is not None:
                raise RuntimeError("C engine exited before readiness")
            return None
    def restored():
        status = client.telemetry()
        return status if status["ec_hex"]==original["ec_hex"] and status["control"]["mode"]=="automatic" else None
    try:
        caps = wait_for(ready)
        result["capabilities"] = caps
        assert not caps["simulated"] and caps["hardware_verified"]
        result["checks"].append(dict(name="native_pipe_telemetry",passed=True,telemetry=client.telemetry()))
        try:
            client._call(100,(0,55))
        except NucError as exc:
            assert exc.code=="invalid_request"
            result["checks"].append(dict(name="test_fault_commands_absent",passed=True))
        else:
            raise AssertionError("Test-only command exposed in production")
        if args.write:
            client.set_profile("cool",lease_seconds=2)
            observed = client.telemetry()
            assert observed["thresholds"]["primary_min_duty_percent"]==40
            recovered = wait_for(restored)
            result["checks"].append(dict(name="cool_expiry",passed=True,recovery=recovered["control"]))
            try:
                client.set_thresholds(original["thresholds"]["primary_min_encoded"]+1,
                                      original["thresholds"]["secondary_min_encoded"]+1,lease_seconds=3)
            except NucError as exc:
                if exc.code not in ("temperature_limit_or_unavailable","fan_unavailable"):
                    raise
                result["checks"].append(dict(name="encoded_thresholds",safely_refused=exc.code))
            else:
                observed = client.telemetry()
                assert observed["thresholds"]["primary_min_encoded"]==original["thresholds"]["primary_min_encoded"]+1
                client.restore()
                result["checks"].append(dict(name="encoded_thresholds",passed=True))
            wait_for(restored)
            try:
                command_result = client.set_duty(60,lease_seconds=4)
            except NucError as exc:
                if exc.code not in ("temperature_limit_or_unavailable","fan_unavailable"):
                    raise
                result["checks"].append(dict(name="manual_60_percent",safely_refused=exc.code))
            else:
                observed = client.telemetry()
                assert observed["manual_channels"]==[dict(channel=1,duty_percent=60)] or observed["control"]["mode"]=="automatic"
                time.sleep(1)
                after = client.telemetry()
                client.restore()
                result["checks"].append(dict(name="manual_60_percent",passed=True,command=command_result,
                                            observed=observed["manual_channels"],after=after["control"]))
            wait_for(restored)
        else:
            try:
                client.set_profile("cool")
            except NucError as exc:
                assert exc.code=="read_only"
                result["checks"].append(dict(name="read_only_enforced",passed=True))
            else:
                raise AssertionError("Read-only command accepted")
        if args.crash:
            marker = OUTPUT.parent/"c-client-ready.json"
            marker.unlink(missing_ok=True)
            code = ("import sys,time,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                    "from nuc_client import NucClient;c=NucClient();r=c.set_profile('cool',lease_seconds=10);"
                    "Path(sys.argv[2]).write_text(json.dumps(r));time.sleep(30)")
            child = subprocess.Popen([sys.executable,"-I","-c",code,str(ROOT/"python"),str(marker)],
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                wait_for(marker.exists)
                child.kill()
                child.wait(timeout=3)
                recovered = wait_for(restored)
                assert recovered["control"]["last_restoration"]["reason"]=="client_exited"
                result["checks"].append(dict(name="python_client_killed_C_restores",passed=True))
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
            client.set_profile("cool",lease_seconds=10)
            process.kill()
            process.wait(timeout=3)
            recovered = wait_for(lambda: (value if (value:=direct_status())["ec_hex"]==original["ec_hex"] else None))
            result["checks"].append(dict(name="C_engine_killed_guardian_restores",passed=True))
        result["passed"] = True
    except BaseException as exc:
        result["passed"] = False
        result["error"] = repr(exc)
        result["win32_error"] = getattr(exc,"win32",None)
        raise
    finally:
        try:
            if process.poll() is None:
                client.restore()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            stderr.close()
            result["final"] = direct_status()
            result["restored_exactly"] = result["final"]["ec_hex"]==original["ec_hex"]
            OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=="__main__":
    main()
