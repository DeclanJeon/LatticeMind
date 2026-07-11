import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from latticemind_core.migrate import migrate_install, vault_manifest


class MigrationTest(unittest.TestCase):
    def test_copy_on_write_is_idempotent_and_preserves_zero_byte_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "state"
            vault = Path(td) / "vault"
            root.mkdir(); vault.mkdir()
            (vault / "note.md").write_bytes(b"raw\x00bytes")
            (root / "config").write_bytes(b"")
            before = vault_manifest(vault)
            with self.assertRaises(ValueError):
                migrate_install(root, vault, source=root / "config")
            # Empty legacy config is invalid, but its bytes are not silently discarded.
            self.assertFalse((root / "config-v1.json").exists())
            self.assertEqual((root / "backups" / "migration-v1" / "config").read_bytes(), b"")
            self.assertEqual(before, vault_manifest(vault))

    def test_receipt_required_and_whole_vault_manifest_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "state"; vault = Path(td) / "vault"
            root.mkdir(); vault.mkdir()
            (root / "config").write_text(f"VAULT=%q-{vault}\n")
            result = migrate_install(root, vault)
            receipt = json.loads((root / "migration-v1.json").read_text())
            self.assertEqual(receipt["vault_manifest_before"], receipt["vault_manifest_after"])
            self.assertEqual(result["enabled_jobs"], [])
            payload = (root / "config-v1.json").read_bytes()
            self.assertEqual(receipt["config_sha256"], hashlib.sha256(payload).hexdigest())

    def test_partial_failure_removes_uncommitted_config_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "state"; vault = Path(td) / "vault"
            root.mkdir(); vault.mkdir()
            (root / "config").write_text(f"VAULT=%q-{vault}\n")
            with self.assertRaises(RuntimeError):
                migrate_install(root, vault, fail_after="config")
            self.assertFalse((root / "config-v1.json").exists())
            self.assertFalse((root / "migration-v1.json").exists())
            migrate_install(root, vault)
            self.assertTrue((root / "migration-v1.json").exists())


if __name__ == "__main__":
    unittest.main()
