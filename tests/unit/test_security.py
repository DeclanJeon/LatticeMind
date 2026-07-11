import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from latticemind_core.approval import consume, issue_challenge, reserve
from latticemind_core.apply import apply_file, recover
from latticemind_core.backend_runner import BackendRunner
from latticemind_core.backends import ADAPTERS, BackendAdapter, get_adapter, select_adapter


class SecurityAdversarialTest(unittest.TestCase):
    def test_all_eight_backends_are_observe_only_and_selection_fail_closed(self):
        self.assertEqual(set(ADAPTERS), {"gjc", "omp", "codex", "claude", "opencode", "pi", "gemini", "hermes"})
        for name, adapter in ADAPTERS.items():
            self.assertIn("observe", adapter.capabilities)
            self.assertNotIn("write", adapter.capabilities)
            self.assertNotIn("acceptEdits", adapter.observe_argv)
            self.assertNotIn("workspace-write", adapter.observe_argv)
            self.assertNotIn("--approve", adapter.observe_argv)
            with self.assertRaises(RuntimeError):
                select_adapter([name], {name: "1"})
        with self.assertRaises(RuntimeError):
            select_adapter(["unknown"], {"unknown": "1"})
        with self.assertRaises(PermissionError):
            BackendAdapter("bad", "bad", "0", None, frozenset({"write"}), (), ()).command("p", "o")

    def test_backend_runner_isolated_timeout_and_output_cap(self):
        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "slow-backend"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import sys, time\n"
                "print('x' * 10000)\n"
                "sys.stdout.flush()\n"
                "time.sleep(2)\n"
            )
            executable.chmod(0o700)
            argv = ADAPTERS["gjc"].observe_argv
            adapter = BackendAdapter(
                "gjc", str(executable), "0", None, frozenset({"observe", "evidence"}),
                argv, ("PATH", "LANG", "LC_ALL"), enabled=True, sandbox_verified=True, verified_versions={"0": argv},
            )
            packet = Path(td) / "packet"
            packet.write_text("{}")
            result = BackendRunner(output_limit=32, timeout=.05).run(adapter, packet, workdir=Path(td), version="0")
            self.assertTrue(result.timed_out)
            self.assertLessEqual(len(result.stdout), 32)

    def test_backend_evidence_is_required_and_schema_validated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            executable = root / "evidence-backend"
            packet = root / "packet.json"
            packet.write_text("{}")
            runner = BackendRunner(output_limit=128, timeout=2)
            argv = ADAPTERS["gjc"].observe_argv
            for mode in ("valid", "missing", "malformed", "wrong", "large"):
                executable.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, pathlib, sys\n"
                    f"mode = {mode!r}\n"
                    "output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
                    "if mode == 'valid': output.write_text(json.dumps({'schema':'evidence-response-v1','items':[]}))\n"
                    "elif mode == 'malformed': output.write_text('{')\n"
                    "elif mode == 'wrong': output.write_text(json.dumps({'schema':'wrong'}))\n"
                    "elif mode == 'large': output.write_text('x' * 1024)\n"
                )
                executable.chmod(0o700)
                adapter = BackendAdapter(
                    "gjc",
                    str(executable),
                    "1",
                    None,
                    frozenset({"observe", "evidence"}),
                    argv,
                    ("PATH", "LANG", "LC_ALL"),
                    enabled=True,
                    sandbox_verified=True,
                    verified_versions={"1": argv},
                )
                if mode == "valid":
                    result = runner.run(adapter, packet, version="1")
                    self.assertEqual(result.evidence["schema"], "evidence-response-v1")
                else:
                    with self.assertRaises(ValueError):
                        runner.run(adapter, packet, version="1")
    def _approval(self, root, now=100):
        return issue_challenge(
            root, run_id="run", proposal_digest="proposal", path_digest="path",
            preimage_digest="pre", install_id="install", now=now,
            identity_provider=lambda: ("actor", "tty"),
        )

    def test_approval_replay_expiry_actor_and_digest_mismatch(self):
        identity = lambda: ("actor", "tty")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rec, code = self._approval(root)
            reserve(root, rec["approval_id"], code, run_id="run", proposal_digest="proposal", path_digest="path", preimage_digest="pre", install_id="install", identity_provider=identity, now=101)
            with self.assertRaises(PermissionError):
                reserve(root, rec["approval_id"], code, run_id="run", proposal_digest="proposal", path_digest="path", preimage_digest="pre", install_id="install", identity_provider=identity, now=101)
            consume(root, rec["approval_id"], install_id="install", identity_provider=identity, now=102)
            with self.assertRaises(PermissionError):
                consume(root, rec["approval_id"], install_id="install", identity_provider=identity, now=103)
            rec2, code2 = self._approval(root, now=200)
            with self.assertRaises(PermissionError):
                reserve(root, rec2["approval_id"], code2, run_id="run", proposal_digest="wrong", path_digest="path", preimage_digest="pre", install_id="install", identity_provider=identity, now=201)
            rec3, code3 = self._approval(root, now=300)
            with self.assertRaises(PermissionError):
                reserve(root, rec3["approval_id"], code3, run_id="run", proposal_digest="proposal", path_digest="path", preimage_digest="pre", install_id="install", identity_provider=lambda: ("other", "tty"), now=301)
            rec4, code4 = self._approval(root, now=400)
            with self.assertRaises(PermissionError):
                reserve(root, rec4["approval_id"], code4, run_id="run", proposal_digest="proposal", path_digest="path", preimage_digest="pre", install_id="install", identity_provider=identity, now=1000)

    def test_apply_allowlist_preserves_body_newline_and_rejects_preimage_race_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); vault = base / "vault"; vault.mkdir()
            target = vault / "note.md"; tx = base / "tx"
            body = b"---\r\nlast_verified: 2020-01-01\r\nsource: old\r\n---\r\nBody\r\n"
            target.write_bytes(body)
            digest = hashlib.sha256(body).hexdigest()
            rec = apply_file(target, {"last_verified": "2025-01-01"}, expected_preimage=digest, transaction_root=tx, vault_root=vault)
            updated = target.read_bytes()
            self.assertIn(b"Body\r\n", updated)
            self.assertIn(b"last_verified: 2025-01-01\r\n", updated)
            with self.assertRaises(PermissionError):
                apply_file(target, {"title": "bad"}, expected_preimage=hashlib.sha256(updated).hexdigest(), transaction_root=tx, vault_root=vault)
            with self.assertRaises(RuntimeError):
                apply_file(target, {"volatility": "high"}, expected_preimage=digest, transaction_root=tx, vault_root=vault)
            link = vault / "link.md"; link.symlink_to(target)
            with self.assertRaises(PermissionError):
                apply_file(link, {"volatility": "high"}, expected_preimage=hashlib.sha256(updated).hexdigest(), transaction_root=tx, vault_root=vault)

    def test_wal_rejects_transaction_roots_inside_or_aliased_to_vault(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault"
            vault.mkdir()
            target = vault / "note.md"
            target.write_text("---\na: b\n---\nold\n")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            for root in (vault, vault / "state"):
                with self.assertRaises(PermissionError):
                    apply_file(target, {"volatility": "high"}, expected_preimage=digest,
                               transaction_root=root, vault_root=vault)
                if root != vault:
                    self.assertFalse(root.exists())
            alias = base / "vault-alias"
            alias.symlink_to(vault, target_is_directory=True)
            with self.assertRaises(PermissionError):
                recover(alias / "state", vault_root=vault)
            self.assertFalse((alias / "state").exists())

    def test_wal_durability_errors_fail_closed_before_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault = root / "vault"
            vault.mkdir()
            target = vault / "note.md"
            target.write_text("---\na: b\n---\nold\n")
            before = target.read_bytes()
            digest = hashlib.sha256(before).hexdigest()
            tx = root / "transactions"
            with mock.patch("latticemind_core.apply.os.fsync", side_effect=OSError("unsupported")):
                with self.assertRaises(OSError):
                    apply_file(target, {"volatility": "high"}, expected_preimage=digest,
                               transaction_root=tx, vault_root=vault)
            self.assertEqual(target.read_bytes(), before)
    def test_wal_recovery_restores_preimage(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); vault = base / "vault"; vault.mkdir()
            target = vault / "x.md"; tx = base / "tx"; target.write_text("---\na: b\n---\nold\n")
            pre = target.read_bytes(); digest = hashlib.sha256(pre).hexdigest()
            apply_file(target, {"volatility": "high"}, expected_preimage=digest, transaction_root=tx, vault_root=vault)
            journal = next(p for p in tx.iterdir() if p.is_dir()) / "transaction.json"
            data = json.loads(journal.read_text()); data["phase"] = "replaced"; journal.write_text(json.dumps(data))
            (journal.parent / "preimage").write_bytes(pre)
            self.assertEqual(recover(tx, vault_root=vault), [str(target)])
            self.assertEqual(target.read_bytes(), pre)
    def test_wal_recovery_rolls_back_crash_after_replace_before_phase_update(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); vault = base / "vault"; vault.mkdir()
            target = vault / "x.md"; tx = base / "tx"; target.write_text("---\na: b\n---\nold\n")
            pre = target.read_bytes(); digest = hashlib.sha256(pre).hexdigest()

            original_wal = __import__("latticemind_core.apply", fromlist=["_wal"])._wal
            def crash_after_replaced(journal, record):
                original_wal(journal, record)
                if record["phase"] == "replaced":
                    raise OSError("crash after replacement")

            with mock.patch("latticemind_core.apply._wal", side_effect=crash_after_replaced):
                with self.assertRaises(OSError):
                    apply_file(target, {"volatility": "high"}, expected_preimage=digest,
                               transaction_root=tx, vault_root=vault)
            self.assertEqual(recover(tx, vault_root=vault), [str(target)])
            self.assertEqual(target.read_bytes(), pre)


if __name__ == "__main__":
    unittest.main()
