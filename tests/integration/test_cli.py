import contextlib
import io
import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from latticemind_core.cli import main


class CliCompatibilityTest(unittest.TestCase):
    def test_status_json_and_exit_behavior(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["status", "--json"])
        payload = json.loads(out.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["schema"], "status-v1")
        self.assertEqual(payload["state"], "degraded")
        self.assertEqual(payload["exit_code"], 2)

    def test_scan_json_and_blocked_exit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "vault"
            root.mkdir()
            (root / "a.md").write_text("---\nvolatility: high\n---\nclaim\n")
            state = base / "state"
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"LATTICEMIND_STATE_ROOT": str(state)}, clear=False), contextlib.redirect_stdout(out):
                code = main(["freshness", "scan", "--vault", str(root)])
            payload = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["schema"], "freshness-run-v1")
            self.assertEqual(payload["status"], "succeeded")
            self.assertTrue((state / "reports").is_dir())
            self.assertTrue(any((state / "reports").glob("freshness-work-*.json")))
            self.assertTrue(any((state / "reports").glob("freshness-report-*.json")))
            repeat = io.StringIO()
            with mock.patch.dict(os.environ, {"LATTICEMIND_STATE_ROOT": str(state)}, clear=False), contextlib.redirect_stdout(repeat):
                self.assertEqual(main(["freshness", "scan", "--vault", str(root)]), 0)
            self.assertEqual(json.loads(repeat.getvalue()), payload)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["freshness", "scan", "--vault", "/does/not/exist"])
        self.assertNotEqual(code, 0)
        self.assertTrue(err.getvalue().strip())

    def test_managed_write_challenge_apply_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault = root / "vault"
            vault.mkdir()
            note = vault / "note.md"
            note.write_text("---\nvolatility: medium\n---\nBody\n", encoding="utf-8")
            proposal = root / "proposal.json"
            proposal.write_text('{"last_verified":"2026-07-11"}', encoding="utf-8")
            config = root / "config-v1.json"
            config.write_text(json.dumps({
                "schema": "config-v1",
                "vault_path": str(vault),
                "profile": "managed-write",
                "enabled_jobs": [],
                "install_id": "install-12345678",
            }), encoding="utf-8")
            env = {
                "LATTICEMIND_CONFIG": str(config),
                "LATTICEMIND_STATE_ROOT": str(root / "state"),
            }
            challenge_out = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch("sys.stdin.isatty", return_value=True), \
                 mock.patch("latticemind_core.approval.tty_identity", return_value="tty"), \
                 mock.patch.object(challenge_out, "isatty", return_value=True), \
                 contextlib.redirect_stdout(challenge_out):
                code = main([
                    "freshness", "challenge", "run-1",
                    "--proposal", str(proposal), "--target", str(note),
                    "--install-id", "install-12345678",
                ])
            self.assertEqual(code, 0)
            challenge = json.loads(challenge_out.getvalue())
            apply_out = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch("sys.stdin.isatty", return_value=True), \
                 mock.patch("latticemind_core.approval.tty_identity", return_value="tty"), \
                 mock.patch.object(apply_out, "isatty", return_value=True), \
                 contextlib.redirect_stdout(apply_out):
                code = main([
                    "freshness", "apply", "run-1", "--proposal", str(proposal),
                    "--target", str(note), "--approval-id", challenge["approval_id"],
                    "--approve", challenge["approval_code"], "--yes",
                ])
            self.assertEqual(code, 0)
            self.assertIn("last_verified: 2026-07-11", note.read_text(encoding="utf-8"))
    def test_report_only_wrappers_use_shared_cli_and_canonical_config(self):
        unix = Path(__file__).parents[2] / "bin" / "latticemind-maintain"
        windows = Path(__file__).parents[2] / "windows" / "latticemind-maintain.ps1"
        u = unix.read_text(encoding="utf-8")
        w = windows.read_text(encoding="utf-8")
        for wrapper in (u, w):
            self.assertIn("latticemind_core", wrapper)
            self.assertIn("freshness", wrapper)
        self.assertIn("run_persisted_slot", u)
        self.assertIn("run_persisted_slot", w)
        for wrapper in (u, w):
            for forbidden in (
                "--approval-mode write", "--sandbox workspace-write",
                "--permission-mode acceptEdits", "gjc", "omp", "codex",
                "claude", "opencode",
            ):
                self.assertNotIn(forbidden, wrapper)
        self.assertIn("config-v1.json", u)
        self.assertIn("LATTICEMIND_CONFIG", u)
        self.assertIn("config-v1.json", w)
        self.assertIn("LATTICEMIND_CONFIG", w)
        self.assertNotIn("config.json", w)
        self.assertNotIn("ConvertFrom-Json", w)
        self.assertNotIn("--vault", u)
        self.assertNotIn("--vault", w)
    def test_transaction_manifest_contract_is_append_only_and_owned(self):
        root = Path(__file__).parents[2]
        unix = (root / "scripts" / "install-local.sh").read_text(encoding="utf-8")
        win = (root / "install.ps1").read_text(encoding="utf-8")
        for producer in (unix, win):
            self.assertIn("manifest-v1", producer)
            self.assertIn("owned", producer)
            self.assertIn("sha256", producer)
            self.assertIn("backup", producer)
        for script in (unix, win):
            self.assertNotIn("$Roots", script)
        self.assertNotIn("record_manifest", unix)
        self.assertIn("rec.get('output'", (root / "uninstall.sh").read_text(encoding="utf-8"))
        self.assertIn("$Record.output", (root / "windows" / "uninstall.ps1").read_text(encoding="utf-8"))


    def test_install_transaction_hooks_cover_scaffold_runtime_and_scheduler_cleanup(self):
        root = Path(__file__).parents[2]
        unix = (root / "scripts" / "install-local.sh").read_text(encoding="utf-8")
        win = (root / "install.ps1").read_text(encoding="utf-8")
        uninstall = (root / "uninstall.sh").read_text(encoding="utf-8")
        win_uninstall = (root / "windows" / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("transactions-$STAMP.jsonl", unix)
        self.assertIn("record_extra_manifest", unix)
        self.assertIn("daemon-reload", uninstall)
        self.assertIn("bootout", uninstall)
        self.assertIn("Remove-Item -LiteralPath $PathValue", win_uninstall)
        self.assertIn("backup_sha256", win)
        for identifier in ("installed-shared.json", "installed-codex.json", "installed-gjc.json"):
            self.assertIn(identifier, unix)
        self.assertIn("record_named_manifest", unix)
        self.assertIn("managed-block", unix)
        self.assertIn("VersionRoot", win)
        self.assertIn("CurrentPointer", win)
        self.assertIn("created = $true", win)
        self.assertIn("job-definition-v1", win_uninstall)
    def test_installers_pin_bootstrap_profiles_permissions_and_shared_parents(self):
        root = Path(__file__).parents[2]
        unix_path = root / "scripts" / "install-local.sh"
        unix = unix_path.read_text(encoding="utf-8")
        unix_entry = (root / "install.sh").read_text(encoding="utf-8")
        win = (root / "install.ps1").read_text(encoding="utf-8")
        self.assertIn('PROFILE="observe"', unix)
        self.assertIn('--profile', unix)
        self.assertIn('PROFILE" == observe', unix)
        self.assertIn("[ValidateSet('observe', 'safe-write', 'managed-write', 'full')]", win)
        self.assertIn('Profile', win)
        for pin in (
            "67c919617ee354825374516574219a0b1774aabdd50a9069c32060a5225a94dd",
            "79c7d6c76d238683ef52a3c2035f0fab06f60ede27503df4b44fefdd4bd481ce",
            "0d005eab9b2f4df946e90ed0db6e44ad1320309023a05d228a79ce8ba40f0f11",
            "9d7c4b56155e3f94a61293858aea80fa312975bd92ca2d413f95c7f1f0f5d536",
        ):
            self.assertIn(pin, win)
        self.assertIn(hashlib.sha256(unix_path.read_bytes()).hexdigest(), unix_entry)
        for relative in (
            "bootstrap/latticemind-verify.ps1",
            "latticemind_core/release.py",
            "latticemind_core/trust_root.py",
            "latticemind_core/__init__.py",
        ):
            self.assertIn(hashlib.sha256((root / relative).read_bytes()).hexdigest(), win)
        self.assertIn("sys.path.insert(0", win)
        self.assertIn("umask 077", unix)
        self.assertIn("profile = $Profile", win)
if __name__ == "__main__":
    unittest.main()
