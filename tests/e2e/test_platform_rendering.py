import configparser
import hashlib
import plistlib
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from latticemind_core.jobs import JOB_DEFINITIONS, render_launchd, render_systemd, render_task_scheduler
from latticemind_core.migrate import migrate_install, vault_manifest


class PlatformRenderingTest(unittest.TestCase):
    def test_all_platform_renderers_are_owned_and_safe(self):
        for job in JOB_DEFINITIONS:
            systemd = configparser.ConfigParser()
            systemd.read_string(render_systemd(job))
            self.assertEqual(systemd["Service"]["Type"], "oneshot")
            self.assertEqual(systemd["Service"]["TimeoutStartSec"], str(job.timeout_seconds))
            self.assertEqual(systemd["Service"]["TimeoutStopSec"], str(job.kill_grace_seconds))
            launchd = plistlib.loads(render_launchd(job).encode())
            self.assertEqual(launchd["Label"], f"com.latticemind.{job.job_id}")
            self.assertEqual(launchd["TimeOut"], job.timeout_seconds)
            task = ET.fromstring(render_task_scheduler(job))
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            self.assertEqual(task.findtext("t:RegistrationInfo/t:URI", namespaces=ns), f"\\LatticeMind\\{job.job_id}")
            self.assertEqual(task.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns), f"PT{job.timeout_seconds}S")
            self.assertEqual(task.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns), "IgnoreNew")
    def test_windows_cli_contract_uses_signed_embedded_runtime(self):
        wrapper = Path(__file__).resolve().parents[2] / "windows" / "latticemind.ps1"
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn("python-x64", text)
        self.assertIn("python-arm64", text)
        self.assertIn("PROCESSOR_ARCHITEW6432", text)
        self.assertIn("sys.path.insert(0,sys.argv.pop(1))", text)
        self.assertIn("runpy.run_module", text)
        self.assertIn("& $PythonExe -c $Bootstrap $PayloadRoot @ArgumentList", text)
        self.assertIn("Unsupported Windows architecture", text)
        self.assertNotIn("Get-Command python", text)
        self.assertNotIn("$env:PYTHONPATH", text)
        builder = (Path(__file__).resolve().parents[2] / "scripts" / "build_release.py").read_text(encoding="utf-8")
        self.assertIn('"upstream/windows/latticemind.ps1" not in names', builder)

    def test_vault_manifest_is_byte_and_path_stable(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault with spaces"
            (vault / "nested").mkdir(parents=True)
            (vault / "note.md").write_bytes(b"exact\x00bytes\n")
            state = base / "state"
            state.mkdir()
            (state / "owned").write_bytes(b"before")
            before = vault_manifest(vault)
            with self.assertRaises(RuntimeError):
                migrate_install(state, vault, fail_after="config")
            self.assertEqual(before, vault_manifest(vault))
            self.assertEqual((state / "owned").read_bytes(), b"before")
            migrate_install(state, vault)
            self.assertEqual(before, vault_manifest(vault))
            self.assertEqual(before["note.md"], "file:" + hashlib.sha256(b"exact\x00bytes\n").hexdigest())


if __name__ == "__main__":
    unittest.main()
