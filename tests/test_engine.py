# SPDX-License-Identifier: GPL-3.0-only
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import unittest
import ctypes as C

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
from nuc_client import NucClient, NucError, REQUEST, MAGIC, VERSION
C_BOOL,C_PTR,C_U32 = C.c_int32,C.c_void_p,C.c_uint32


def wait_for(predicate,timeout=5):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out")


class EngineTestCase(unittest.TestCase):
    write = True

    def setUp(self):
        self.log = (ROOT/"test-results").mkdir(exist_ok=True)
        self.stderr = (ROOT/"test-results"/"engine-test.log").open("wb")
        args = [str(ROOT/"fan-control"/"build"/"nuc-engine-test.exe"),"serve"]
        if self.write:
            args.append("--allow-write")
        env = dict(os.environ)
        env.pop("NUC_ENGINE_TEST_MAP",None)
        self.engine = subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=self.stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW,env=env)
        self.addCleanup(self.cleanup_process)
        self.client = NucClient(timeout=5)
        def ready():
            try:
                return self.client.capabilities()
            except NucError:
                if self.engine.poll() is not None:
                    raise AssertionError("Engine exited: "+(ROOT/"test-results"/"engine-test.log").read_text())
                return False
        wait_for(ready)
        self.original = self.client.telemetry()["ec_hex"]

    def cleanup_process(self):
        if self.engine.poll() is None:
            self.engine.terminate()
            self.engine.wait(timeout=5)
        self.stderr.close()

    def tearDown(self):
        if self.engine.poll() is None:
            try:
                self.client.restore()
            finally:
                self.engine.terminate()
                self.engine.wait(timeout=5)
        self.stderr.close()

    def automatic(self):
        value = self.client.telemetry()
        return value if value["ec_hex"]==self.original and value["control"]["mode"]=="automatic" else None


class EngineTests(EngineTestCase):
    def test_capabilities_and_telemetry(self):
        caps = self.client.capabilities()
        self.assertTrue(caps["simulated"])
        self.assertEqual(caps["engine"],"C")
        self.assertFalse(caps["independent_fan_control"])
        self.assertEqual(len(self.client.telemetry()["fans"]),2)

    def test_profiles_catalog_matches_firmware_profiles(self):
        profiles = self.client.profiles()
        self.assertEqual([p["id"] for p in profiles],self.client.capabilities()["profiles"])
        self.assertEqual([p["label"] for p in profiles],["Silencieux","Équilibré","Refroidissement"])
        for profile in profiles:
            with self.subTest(profile=profile["id"]):
                self.client.set_profile(profile["id"])
                observed = self.client.telemetry()["thresholds"]
                self.assertEqual(observed["primary_min_duty_percent"],profile["minimum_duty_percent"]["cpu"])
                self.assertEqual(observed["secondary_min_duty_percent"],profile["minimum_duty_percent"]["nvidia_gpu"])
                for key in ("primary_min_encoded","secondary_min_encoded","units","celsius_conversion_verified"):
                    self.assertEqual(observed[key],profile["thresholds"][key])
                self.client.restore()
        self.assertTrue(self.automatic())

    def test_safeguards_catalog_has_unique_rules_and_explicit_scope(self):
        policy = self.client.safeguards()
        self.assertTrue(policy["write_enabled"])
        self.assertFalse(policy["configurable_via_api"])
        self.assertEqual(policy["semantics"],"configured_policy_not_live_health")
        rules = {rule["id"]:rule for rule in policy["rules"]}
        self.assertEqual(len(rules),len(policy["rules"]))
        self.assertEqual(len(rules),22)
        self.assertEqual(rules["cpu_temperature"]["applies_to"],["duty","thresholds"])
        self.assertEqual(rules["nvidia_gpu_temperature"]["applies_to"],["duty","thresholds"])
        self.assertNotIn("profile",rules["cpu_temperature"]["applies_to"])
        for rule in rules.values():
            self.assertTrue(rule["label"])
            if "limit_ref" in rule:
                self.assertIn(rule["limit_ref"],policy["limits"])
        caps = self.client.capabilities()
        self.assertEqual(policy["limits"]["cpu_temperature_celsius"]["restore_at_or_above"],caps["manual_cpu_limit_celsius"])
        self.assertEqual(policy["limits"]["duty_percent"],dict(minimum=caps["duty_percent_range"][0],maximum=caps["duty_percent_range"][1]))
        self.assertTrue(self.automatic())

    def test_declared_thermal_limits_match_enforcement(self):
        limits = self.client.safeguards()["limits"]
        cpu_limit = limits["cpu_temperature_celsius"]["restore_at_or_above"]
        gpu_limit = limits["nvidia_gpu_temperature_celsius"]["restore_at_or_above"]
        self.client._call(100,(0,cpu_limit-1))
        self.client._call(100,(1,gpu_limit-1))
        self.client.set_duty(limits["duty_percent"]["minimum"])
        self.client.restore()
        self.client._call(100,(0,cpu_limit))
        with self.assertRaises(NucError) as error:
            self.client.set_duty(60)
        self.assertEqual(error.exception.code,"temperature_limit_or_unavailable")
        self.client._call(100,(0,55))
        self.client._call(100,(1,gpu_limit))
        with self.assertRaises(NucError) as error:
            self.client.set_duty(60)
        self.assertEqual(error.exception.code,"temperature_limit_or_unavailable")
        self.client.set_profile("cool")  # OEM profiles keep firmware thermal control.
        self.client.restore()
        self.assertTrue(self.automatic())

    def test_discovery_does_not_adopt_or_modify_active_lease(self):
        session = self.client.set_duty(60)
        with NucClient() as observer:
            self.assertEqual(len(observer.profiles()),3)
            self.assertFalse(observer.safeguards()["configurable_via_api"])
        observed = self.client.telemetry()
        self.assertEqual(observed["control"]["active_lease"],session["control"]["active_lease"])
        self.assertEqual(observed["manual_channels"][0]["duty_percent"],60)

    def test_discovery_rejects_arguments_and_lease_identifiers(self):
        for command in (3,4):
            for values,lease in (((1,0,0,0),bytes(16)),((0,0,0,0),b"x"*16)):
                with self.subTest(command=command),self.assertRaises(NucError) as error:
                    self.client._raw(REQUEST.pack(MAGIC,VERSION,48,command,*values,lease))
                self.assertEqual(error.exception.code,"invalid_request")
        self.assertTrue(self.automatic())

    def test_manual_readback_and_expiry(self):
        self.client.set_duty(60,lease_seconds=1)
        self.assertEqual(self.client.telemetry()["manual_channels"],[dict(channel=1,duty_percent=60)])
        result = wait_for(self.automatic)
        self.assertEqual(result["control"]["last_restoration"]["reason"],"lease_expired")

    def test_renew_preserves_active_command(self):
        self.client.set_duty(60,lease_seconds=1)
        time.sleep(0.5)
        self.client.renew(lease_seconds=2)
        time.sleep(0.7)
        self.assertEqual(self.client.telemetry()["manual_channels"][0]["duty_percent"],60)
        self.client.restore()
        self.assertTrue(self.automatic())

    def test_thresholds_and_baseline_preserved_across_modes(self):
        self.client.set_profile("cool")
        self.client.set_duty(65)
        self.client.set_thresholds(51,43)
        self.assertEqual(self.client.telemetry()["thresholds"]["primary_min_encoded"],51)
        self.client.restore()
        self.assertTrue(self.automatic())

    def test_thresholds_cannot_drift(self):
        self.client.set_thresholds(55,47)
        with self.assertRaises(NucError):
            self.client.set_thresholds(56,48)
        self.assertTrue(wait_for(self.automatic))

    def test_hot_cpu_restores(self):
        self.client.set_duty(60)
        self.client._call(100,(0,85))
        result = wait_for(self.automatic)
        self.assertEqual(result["control"]["last_restoration"]["reason"],"temperature_limit_or_unavailable")

    def test_missing_gpu_refuses_manual(self):
        self.client._call(100,(1,0))
        with self.assertRaises(NucError) as error:
            self.client.set_duty(60)
        self.assertEqual(error.exception.code,"temperature_limit_or_unavailable")
        self.assertTrue(self.automatic())

    def test_missing_fan_restores(self):
        self.client.set_duty(60)
        self.client._call(100,(2,0))
        result = wait_for(self.automatic)
        self.assertEqual(result["control"]["last_restoration"]["reason"],"fan_unavailable")

    def test_power_notification_restores_and_allows_new_session(self):
        self.client.set_duty(60)
        self.client._call(100,(4,0))
        result = wait_for(self.automatic)
        self.assertIn(result["control"]["last_restoration"]["reason"],("power_change","client_release"))
        self.client.set_profile("cool")
        self.client.restore()
        self.assertTrue(self.automatic())

    def test_partial_frame_times_out_without_blocking_recovery(self):
        self.client.set_duty(60,lease_seconds=1)
        with self.client._api.connect(self.client.pipe_name,2) as handle:
            self.client._api.io(handle,b"NUC",True,2)
            time.sleep(3.2)
        self.assertTrue(wait_for(self.automatic))

    def test_simulated_engine_crash_guardian_restores(self):
        self.client.set_duty(60,lease_seconds=15)
        api = self.client._api
        open_map = api.bind(api.kernel,"OpenFileMappingW",C_PTR,[C_U32,C_BOOL,C.c_wchar_p])
        map_view = api.bind(api.kernel,"MapViewOfFile",C_PTR,[C_PTR,C_U32,C_U32,C_U32,C.c_size_t])
        unmap = api.bind(api.kernel,"UnmapViewOfFile",C_BOOL,[C_PTR])
        mapping = open_map(4,False,f"Local\\NucControl.Test.{self.engine.pid}")
        self.assertTrue(mapping)
        view = map_view(mapping,4,0,0,256)
        self.assertTrue(view)
        try:
            self.engine.kill()
            self.engine.wait(timeout=3)
            wait_for(lambda: C.string_at(view,94).hex()==self.original)
        finally:
            unmap(view)
            api.close(mapping)

    def test_other_client_without_lease_cannot_override(self):
        self.client.set_duty(60)
        other = NucClient()
        with self.assertRaises(NucError) as error:
            other.set_profile("quiet")
        self.assertEqual(error.exception.code,"lease_owner_mismatch")

    def test_read_only_observer_does_not_restore_another_client(self):
        self.client.set_duty(60)
        with NucClient() as observer:
            self.assertEqual(observer.telemetry()["manual_channels"][0]["duty_percent"],60)
        self.assertEqual(self.client.telemetry()["manual_channels"][0]["duty_percent"],60)

    def test_read_only_observer_does_not_adopt_visible_lease(self):
        self.client.set_duty(60)
        with NucClient() as observer:
            observer.telemetry()
            with self.assertRaises(NucError) as error:
                observer.set_profile("cool")
            self.assertEqual(error.exception.code,"lease_owner_mismatch")
        self.assertEqual(self.client.telemetry()["manual_channels"][0]["duty_percent"],60)

    def test_client_process_exit_restores(self):
        marker = ROOT/"test-results"/"client-ready.json"
        marker.unlink(missing_ok=True)
        code = ("import sys,time,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "from nuc_client import NucClient;c=NucClient();r=c.set_duty(60,lease_seconds=15);"
                "Path(sys.argv[2]).write_text(json.dumps(r));time.sleep(30)")
        child = subprocess.Popen([sys.executable,"-I","-c",code,str(ROOT/"python"),str(marker)],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_for(marker.exists)
            self.assertTrue(self.client.telemetry()["manual_channels"])
            child.kill()
            child.wait(timeout=3)
            result = wait_for(self.automatic)
            self.assertEqual(result["control"]["last_restoration"]["reason"],"client_exited")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)

    def test_guardian_crash_engine_restores(self):
        result = self.client.set_duty(60)
        pid = result["control"]["guardian_pid"]
        api = self.client._api
        terminate = api.bind(api.kernel,"TerminateProcess",C_BOOL,[C_PTR,C_U32])
        open_process = api.bind(api.kernel,"OpenProcess",C_PTR,[C_U32,C_BOOL,C_U32])
        handle = open_process(1,False,pid)
        self.assertTrue(handle)
        try:
            self.assertTrue(terminate(handle,99))
        finally:
            api.close(handle)
        self.assertTrue(wait_for(self.automatic))

    def test_invalid_frames_and_ranges(self):
        for command,values in ((10,(0,5,0,0)),(10,(60,16,0,0)),(10,(60,5,1,0)),(999,(0,0,0,0))):
            with self.subTest(command=command,values=values),self.assertRaises(NucError) as error:
                self.client._raw(REQUEST.pack(MAGIC,VERSION,48,command,*values,bytes(16)))
            self.assertEqual(error.exception.code,"invalid_request")
        with self.assertRaises(NucError):
            self.client._raw(REQUEST.pack(0,VERSION,48,1,0,0,0,0,bytes(16)))
        self.assertTrue(self.automatic())

    def test_sdk_strict_integers(self):
        for value in (True,60.0,"60",49,100):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.client.set_duty(value)
        self.assertTrue(self.automatic())


class ReadOnlyTests(EngineTestCase):
    write = False
    def test_read_only_refuses_control(self):
        with self.assertRaises(NucError) as error:
            self.client.set_profile("cool")
        self.assertEqual(error.exception.code,"read_only")
        self.assertTrue(self.automatic())

    def test_discovery_works_without_write_permission(self):
        self.assertEqual([p["id"] for p in self.client.profiles()],["quiet","balanced","cool"])
        policy = self.client.safeguards()
        self.assertFalse(policy["write_enabled"])
        self.assertEqual(policy["limits"]["lease_seconds"],dict(minimum=1,maximum=15))
        self.assertFalse(policy["validation"]["real_suspend_resume_tested"])
        self.assertTrue(self.automatic())


if __name__=="__main__":
    unittest.main()
