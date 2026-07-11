from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts.build_release import build

ROOT = Path(__file__).resolve().parents[2]


def _upstream_fixture(root: Path) -> tuple[Path, str]:
    upstream = root / "upstream"
    (upstream / "scripts").mkdir(parents=True)
    (upstream / "dist").mkdir()
    (upstream / "dist" / "agent.txt").write_text("agent\n")
    (upstream / "scripts" / "bootstrap_vault.py").write_text(
        "from pathlib import Path\n"
        "def bootstrap(destination, owner, preset, *args):\n"
        "    Path(destination).mkdir(parents=True, exist_ok=True)\n"
        "    (Path(destination) / 'README.md').write_text(preset + '\\n')\n"
    )
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    subprocess.run(["git", "-C", str(upstream), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(upstream), "config", "user.name", "Release Test"], check=True)
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    return upstream, commit


def _runtime_fixtures(root: Path) -> Path:
    runtimes = root / "runtimes"
    runtimes.mkdir()
    for rid in ("windows-x64", "windows-arm64"):
        with zipfile.ZipFile(runtimes / f"{rid}.zip", "w") as archive:
            archive.writestr("python.exe", rid.encode())
    return runtimes


def _args(output: Path, runtimes: Path, upstream: Path, upstream_commit: str) -> argparse.Namespace:
    return argparse.Namespace(
        root=str(ROOT),
        output=str(output),
        runtimes=str(runtimes),
        upstream_dir=str(upstream),
        version="v0.2.99",
        tag=None,
        commit="a" * 40,
        upstream_url="https://example.invalid/upstream",
        upstream_commit=upstream_commit,
        previous_version="v0.2.98",
    )


class ReleaseBuildTest(unittest.TestCase):
    def test_release_is_deterministic_and_installable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            upstream, upstream_commit = _upstream_fixture(root)
            runtimes = _runtime_fixtures(root)
            env = {
                "LATTICEMIND_WINDOWS_X64_URL": "https://www.python.org/x64",
                "LATTICEMIND_WINDOWS_ARM64_URL": "https://www.python.org/arm64",
                "LATTICEMIND_SIGNING_KEY_B64": base64.b64encode(bytes(range(32))).decode(),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                first = root / "first"
                second = root / "second"
                build(_args(first, runtimes, upstream, upstream_commit))
                build(_args(second, runtimes, upstream, upstream_commit))
            self.assertEqual(
                hashlib.sha256((first / "latticemind-dist.zip").read_bytes()).digest(),
                hashlib.sha256((second / "latticemind-dist.zip").read_bytes()).digest(),
            )
            with zipfile.ZipFile(first / "latticemind-dist.zip") as archive:
                names = set(archive.namelist())
            for required in (
                "upstream/VERSION",
                "upstream/dist/agent.txt",
                "upstream/scaffolds/default/README.md",
                "upstream/windows/python-x64/python.exe",
                "upstream/windows/python-arm64/python.exe",
            ):
                self.assertIn(required, names)
            self.assertTrue((first / "release-manifest-v1.sig").exists())
            self.assertTrue((first / "SHA256SUMS").exists())
            self.assertEqual(
                [line.split("  ", 1)[1] for line in (first / "SHA256SUMS").read_text().splitlines()],
                ["latticemind-dist.zip", "release-manifest-v1.json", "release-manifest-v1.sig"],
            )

    def test_runtime_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            upstream, upstream_commit = _upstream_fixture(root)
            runtimes = _runtime_fixtures(root)
            with zipfile.ZipFile(runtimes / "windows-x64.zip", "a") as archive:
                archive.writestr("PYTHON.EXE", b"alias")
            env = {
                "LATTICEMIND_WINDOWS_X64_URL": "https://www.python.org/x64",
                "LATTICEMIND_WINDOWS_ARM64_URL": "https://www.python.org/arm64",
                "LATTICEMIND_SIGNING_KEY_B64": base64.b64encode(bytes(range(32))).decode(),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(ValueError, "Windows-equivalent|collision"):
                    build(_args(root / "out", runtimes, upstream, upstream_commit))
    def test_missing_signing_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            upstream, upstream_commit = _upstream_fixture(root)
            runtimes = _runtime_fixtures(root)
            env = {
                "LATTICEMIND_WINDOWS_X64_URL": "https://www.python.org/x64",
                "LATTICEMIND_WINDOWS_ARM64_URL": "https://www.python.org/arm64",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("LATTICEMIND_SIGNING_KEY_B64", None)
                with self.assertRaisesRegex(ValueError, "required"):
                    build(_args(root / "out", runtimes, upstream, upstream_commit))

    def test_mismatched_upstream_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            upstream, _ = _upstream_fixture(root)
            runtimes = _runtime_fixtures(root)
            with self.assertRaisesRegex(ValueError, "HEAD"):
                build(_args(root / "out", runtimes, upstream, "b" * 40))


if __name__ == "__main__":
    unittest.main()
