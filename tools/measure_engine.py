# SPDX-License-Identifier: GPL-3.0-only
"""Read-only, 30-second local measurement at four sensor requests per second."""
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
from nuc_client import NucClient, NucError


def main():
    output = ROOT/"test-results"/"performance.json"
    output.parent.mkdir(exist_ok=True)
    process = subprocess.Popen([str(ROOT/"fan-control"/"build"/"nuc-engine.exe"),"serve"],
                               creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL)
    client = NucClient(timeout=5)
    try:
        deadline = time.monotonic()+5
        while True:
            try:
                initial = client.telemetry()
                break
            except NucError:
                if time.monotonic()>deadline or process.poll() is not None:
                    raise
                time.sleep(0.05)
        times = []
        memory = []
        start = time.monotonic()
        for index in range(120):
            scheduled = start+index*0.25
            time.sleep(max(0,scheduled-time.monotonic()))
            before = time.perf_counter()
            status = client.telemetry()
            times.append((time.perf_counter()-before)*1000)
            memory.append(status["working_set_bytes"])
            assert status["manual_channels"]==[] and status["ec_hex"]==initial["ec_hex"]
        elapsed = time.monotonic()-start
        result = dict(duration_seconds=elapsed,requests=len(times),poll_interval_seconds=0.25,
                      engine_cpu_seconds=(status["cpu_time_100ns"]-initial["cpu_time_100ns"])/10_000_000,
                      engine_percent_of_one_logical_cpu=(status["cpu_time_100ns"]-initial["cpu_time_100ns"])/10_000_000/elapsed*100,
                      response_median_ms=statistics.median(times),response_p95_ms=sorted(times)[int(len(times)*.95)-1],
                      response_max_ms=max(times),working_set_max_bytes=max(memory),private_bytes=status["private_bytes"],
                      exe_bytes=(ROOT/"fan-control"/"build"/"nuc-engine.exe").stat().st_size,restored_exactly=status["ec_hex"]==initial["ec_hex"])
        output.write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result,indent=2))
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__=="__main__":
    main()
