import json
import tempfile
import unittest
from pathlib import Path

from latticemind_core import (
    Ledger, PermissionProfile, canonical_json, migrate_legacy_unix,
    validate_transition, write_json_atomic,
)


class CoreContractsTest(unittest.TestCase):
    def test_canonical_json_and_enum(self):
        self.assertEqual(canonical_json({"b": 1, "a": "é"}), '{"a":"é","b":1}')
        self.assertEqual(PermissionProfile.OBSERVE.value, "observe")

    def test_safe_legacy_migration_rejects_commands(self):
        self.assertEqual(migrate_legacy_unix("VAULT=%q-/tmp/vault\n")["vault_path"], "/tmp/vault")
        with self.assertRaises(ValueError):
            migrate_legacy_unix("VAULT=/tmp/x; touch /tmp/pwn\n")

    def test_atomic_write_and_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "status.json"
            write_json_atomic(target, {"z": 1})
            self.assertEqual(json.loads(target.read_text()), {"z": 1})
            ledger = Ledger(root / "ledger.jsonl", redactor=lambda x: {k: v for k, v in x.items() if k != "secret"})
            ledger.append({"event": "queued", "secret": "hidden"})
            self.assertTrue(ledger.verify())

    def test_transitions(self):
        validate_transition(None, "queued")
        validate_transition("queued", "running")
        with self.assertRaises(ValueError):
            validate_transition("succeeded", "running")


if __name__ == "__main__":
    unittest.main()
