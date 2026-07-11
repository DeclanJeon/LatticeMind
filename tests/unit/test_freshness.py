import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker

from latticemind_core.freshness import (
    ChangedTreeError, MAX_CANDIDATES, SecurityError, manifest, scan,
    validate_evidence,
)


class FreshnessAdversarialTest(unittest.TestCase):
    def note(self, root, name, body):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def test_deterministic_order_and_cap_and_ttl(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(MAX_CANDIDATES + 3):
                self.note(root, f"n-{i:02d}.md", b"---\nvolatility: high\n---\nclaim\n")
            self.note(root, "not-markdown.txt", b"---\nvolatility: high\n---\n")
            first = scan(root, as_of=date(2025, 1, 1))
            second = scan(root, as_of=date(2025, 1, 1))
            self.assertEqual(first, second)
            self.assertEqual(len(first["candidates"]), MAX_CANDIDATES)
            self.assertEqual(first["candidates"][0]["ttl_days"], 7)
            self.assertEqual([x["id"] for x in first["candidates"]], sorted(x["id"] for x in first["candidates"]))

    def test_malformed_utf8_and_frontmatter_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.note(root, "bad.md", b"\xff\xfe")
            with self.assertRaises(UnicodeDecodeError):
                scan(root)
            (root / "bad.md").write_text("---\nunknown: x\n---\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                scan(root)

    def test_case_unicode_collision_symlink_and_special_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.note(root, "A.md", b"x")
            self.note(root, "a.md", b"y")
            with self.assertRaises(SecurityError):
                manifest(root)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.note(root, "e\u0301.md", b"x")
            self.note(root, "\u00e9.md", b"y")
            with self.assertRaises(SecurityError):
                manifest(root)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.note(root, "real.md", b"x")
            (root / "link.md").symlink_to(root / "real.md")
            with self.assertRaises(SecurityError):
                manifest(root)
        if os.name != "nt":
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                os.mkfifo(root / "pipe")
                with self.assertRaises(SecurityError):
                    manifest(root)

    def test_output_must_be_outside_vault_and_tree_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.note(root, "a.md", b"---\nvolatility: low\n---\n")
            with self.assertRaises(SecurityError):
                scan(root, output_dir=root / "work")
            original = manifest(root)
            with mock.patch("latticemind_core.freshness.manifest", return_value=original[:-1]):
                with self.assertRaises(ChangedTreeError):
                    scan(root)
    def test_precreated_output_symlinks_never_mutate_vault(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "vault"
            root.mkdir()
            target = root / "protected.md"
            target.write_bytes(b"protected")
            probe = base / "probe"
            scan(root, as_of=date(2025, 1, 1), output_dir=probe)
            packet_name = next(probe.glob("freshness-work-*.json")).name
            report_name = next(probe.glob("freshness-report-*.json")).name

            packet_attack = base / "packet-attack"
            packet_attack.mkdir()
            (packet_attack / packet_name).symlink_to(target)
            with self.assertRaises(SecurityError):
                scan(root, as_of=date(2025, 1, 1), output_dir=packet_attack)
            self.assertEqual(target.read_bytes(), b"protected")

            report_attack = base / "report-attack"
            report_attack.mkdir()
            (report_attack / report_name).symlink_to(target)
            with self.assertRaises(SecurityError):
                scan(root, as_of=date(2025, 1, 1), output_dir=report_attack)
            self.assertEqual(target.read_bytes(), b"protected")

    @unittest.skipIf(os.name == "nt", "POSIX metadata and hardlink semantics")
    def test_manifest_detects_mode_and_hardlink_identity_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            note = root / "note.md"
            note.write_text("---\nvolatility: low\n---\nclaim\n")
            original = manifest(root)
            note.chmod(0o600)
            self.assertNotEqual(original, manifest(root))

            linked = root / "linked.md"
            os.link(note, linked)
            hardlinked = manifest(root)
            linked.unlink()
            linked.write_bytes(note.read_bytes())
            self.assertNotEqual(hardlinked, manifest(root))
    def test_emitted_run_documents_and_nested_schema_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.note(root, "claim.md", b"---\nvolatility: high\n---\nclaim\n")
            report = scan(root, as_of=date(2025, 1, 1))
            schema = json.loads((Path(__file__).parents[2] / "schemas" / "freshness-run-v1.json").read_text())
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            self.assertEqual(list(validator.iter_errors(report)), [])
            for field, value in (("mode", "0644"), ("reparse_tag", -1), ("sha256", "bad")):
                malformed = dict(report)
                malformed["vault_manifest"] = [dict(report["vault_manifest"][0], **{field: value})]
                self.assertTrue(list(validator.iter_errors(malformed)))
            malformed = dict(report, evidence=[{"schema": "freshness-evidence-v1", "url": 7,
                                               "reachable": False, "supports": False, "note": "bad"}])
            self.assertTrue(list(validator.iter_errors(malformed)))
    def test_evidence_schema_is_strict_and_typed(self):
        valid = {"schema": "freshness-evidence-v1", "url": "https://example.test", "reachable": True, "supports": False, "note": "blocked"}
        self.assertEqual(validate_evidence(valid), valid)
        for bad in ({**valid, "supports": "yes"}, {**valid, "url": 3}, {**valid, "extra": 1}):
            with self.assertRaises(ValueError):
                validate_evidence(bad)


if __name__ == "__main__":
    unittest.main()
