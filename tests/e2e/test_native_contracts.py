"""Complementary native scheduler contract checks.

The platform smoke test owns lifecycle execution; this module checks the
portable ownership and cleanup contract without repeating scheduler setup.
"""
import os
import platform
import tempfile
import unittest
from pathlib import Path

from tests.e2e.native_smoke import write_uninstall_manifest

ROOT = Path(__file__).resolve().parents[2]


class NativeContractsTest(unittest.TestCase):
    def test_owned_scheduler_manifest_is_digest_bound(self):
        if os.environ.get("LATTICEMIND_NATIVE_E2E") != "1":
            self.skipTest("native lane is opt-in")
        with tempfile.TemporaryDirectory(prefix="latticemind native contract ") as td:
            home = Path(td)
            job = home / "freshness.service"
            job.write_text("# owner=latticemind-job-v1 schema=job-definition-v1\n", encoding="utf-8")
            (home / "jobs.json").write_text(
                '{"jobs":[{"path":"%s","owner":"latticemind-job-v1","job_id":"freshness"}]}\n'
                % job,
                encoding="utf-8",
            )
            write_uninstall_manifest(home)
            manifest = (home / "data/latticemind/manifest-v1.json").read_text(encoding="utf-8")
            self.assertIn('"sha256":', manifest)
            self.assertIn('"marker":"owner=latticemind-job-v1 schema=job-definition-v1"', manifest)

    def test_native_smoke_requires_real_platform(self):
        self.assertIn(platform.system(), {"Linux", "Darwin", "Windows"})
        self.assertIn("shutil.copytree", (ROOT / "tests/e2e/native_smoke.py").read_text(encoding="utf-8"))
        self.assertNotIn("Junction", (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
