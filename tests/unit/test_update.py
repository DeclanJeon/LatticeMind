import hashlib, hmac, json, tarfile, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock
from latticemind_core import trust_root
from latticemind_core.release import ArchiveError, TrustError, canonical_manifest, validate_archive, verify_manifest
from latticemind_core.update import UpdateError, apply_update, rollback, snapshot_install
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    Ed25519PrivateKey=None

class UpdateSecurityTests(unittest.TestCase):
    def setUp(self):
        if Ed25519PrivateKey is None: self.skipTest("cryptography unavailable")
        self.key=Ed25519PrivateKey.generate(); self.old=(trust_root.PUBLIC_KEY,trust_root.KEY_ID)
        from cryptography.hazmat.primitives import serialization
        trust_root.PUBLIC_KEY=self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw); trust_root.KEY_ID="test-key"
    def tearDown(self): trust_root.PUBLIC_KEY,trust_root.KEY_ID=self.old
    def manifest(self,asset):
        raw=Path(asset).read_bytes(); sha=hashlib.sha256(raw).hexdigest()
        return {"schema":"release-manifest-v1","key_id":"test-key","repository":trust_root.REPOSITORY,"channel":"stable","version":"v1.2.3","tag":"v1.2.3","full_sha":"1"*40,"upstream":{"url":"https://example.invalid/upstream","full_commit":"2"*40},"bounds":{"bootstrap":{"min":1,"max":1},"config":{"min":1,"max":1},"state":{"min":1,"max":1}},"bootstrap":{},"payload":{"root":"upstream","members":["dist","scaffolds","windows","VERSION"]},"assets":[{"name":Path(asset).name,"size":len(raw),"sha256":sha,"rid":"any"}],"runtimes":{"windows-x64":{"url":"https://example.invalid/x64","size":1,"sha256":"3"*64,"rid":"windows-x64"},"windows-arm64":{"url":"https://example.invalid/arm64","size":1,"sha256":"4"*64,"rid":"windows-arm64"}},"scheduler_assets":{"scripts/install-systemd.sh":{"sha256":"5"*64}},"previous_compatible_version":"v1.2.2","activation":{"min_bootstrap":1,"epoch":1},"revocation":{"revoked_epoch":0}}
    def signed(self,m): return {"key_id":"test-key","epoch":1,"signature":self.key.sign(canonical_manifest(m))}
    def release_asset(self, root):
        upstream = root / "upstream"
        for relative in ("dist", "scaffolds", "windows", "bin", "latticemind_core"):
            (upstream / relative).mkdir(parents=True, exist_ok=True)
        (upstream / "VERSION").write_text("v1.2.3\n")
        (upstream / "bin/latticemind").write_text("new")
        (upstream / "bin/latticemind-maintain").write_text("maintain")
        (upstream / "bin/latticemind-status").write_text("status")
        (upstream / "uninstall.sh").write_text("uninstall")
        (upstream / "latticemind_core/__init__.py").write_text("")
        asset = root / "a.tar"
        with tarfile.open(asset, "w") as archive:
            archive.add(upstream, arcname="upstream")
        return asset
    def test_valid_signature_and_identity_mismatch_matrix(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"a.tar"; p.write_bytes(b"asset"); m=self.manifest(p); verify_manifest(m,self.signed(m),asset=p)
            for key in ("repository","channel","tag","full_sha","upstream","bounds","assets","runtimes","scheduler_assets"):
                bad=dict(m); bad[key]="bad"; 
                with self.assertRaises(TrustError): verify_manifest(bad,self.signed(bad),asset=p)
            bad=dict(m); bad["key_id"]="other"
            with self.assertRaises(TrustError): verify_manifest(bad,self.signed(m),asset=p)
            with self.assertRaises(TrustError):
                verify_manifest(m, {"key_id": "test-key", "epoch": 0, "signature": self.key.sign(canonical_manifest(m))}, asset=p)
            future = dict(m)
            future["activation"] = {"min_bootstrap": 1, "epoch": 2}
            with self.assertRaises(TrustError):
                verify_manifest(future, self.signed(future), asset=p)
    def test_archive_collision_traversal_link_and_quota(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); z=root/"x.zip"
            with zipfile.ZipFile(z,"w") as f: f.writestr("A",b"1"); f.writestr("a",b"2")
            with self.assertRaises(ArchiveError): validate_archive(z,root/"out")
            t=root/"t.tar"; outside=root/"x"; outside.write_bytes(b"x")
            with tarfile.open(t,"w") as f: f.add(outside,arcname="../escape")
            with self.assertRaises(ArchiveError): validate_archive(t,root/"out2")
    def test_snapshot_receipt_tamper_arbitrary_and_vault_invariance(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); cfg=root/"config"; cfg.write_text("old"); jobs=root/"jobs"; jobs.mkdir(); (jobs/"j").write_text("old")
            vault=root/"vault"; vault.mkdir(); secret=vault/"secret"; secret.write_text("keep")
            snap=snapshot_install(root/"snaps",components={"config":cfg,"jobs":jobs},pointer_target="/old",vault=vault,version="v1")
            self.assertEqual(secret.read_text(),"keep"); (snap/"receipt.json").write_text("tampered")
            with self.assertRaises(UpdateError): rollback(install_root=root,snapshot=snap)
    def test_snapshot_rejects_nested_symlink_without_residue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            component = root / "component"
            component.mkdir()
            secret = root / "secret"
            secret.write_text("sensitive")
            (component / "linked").symlink_to(secret)
            snapshots = root / "snapshots"
            with self.assertRaises(UpdateError):
                snapshot_install(snapshots, components={"component": component})
            self.assertEqual(list(snapshots.glob("snapshot-*")), [])
    def test_immutable_switch_and_post_switch_restore_external_components(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); asset=self.release_asset(root)
            m=self.manifest(asset); cfg=root/"config"; cfg.write_text("old"); install=root/"install"; install.mkdir(); old=install/"versions"; old.mkdir(); (old/"v0").mkdir(); (old/"v0"/"latticemind").write_text("old"); (install/"current").symlink_to(old/"v0",target_is_directory=True)
            def fail(): cfg.write_text("mutated"); raise RuntimeError("injected")
            with self.assertRaises(UpdateError): apply_update(m,self.signed(m),asset,install_root=install,snapshot_root=root/"snaps",component_paths={"config":cfg},fail_after_switch=fail,install_id="test-install",current_schema_versions={"bootstrap":1,"config":1,"state":1})
            self.assertEqual(cfg.read_text(),"old"); self.assertEqual((install/"current"/"latticemind").read_text(),"old")
    def test_update_persists_authenticated_rollback_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            asset = self.release_asset(root)
            manifest = self.manifest(asset)
            config = root / "config"
            config.write_text("old")
            install = root / "install"
            old = install / "versions" / "v0"
            old.mkdir(parents=True)
            (old / "latticemind").write_text("old")
            (install / "current").symlink_to(old, target_is_directory=True)

            result = apply_update(
                manifest,
                self.signed(manifest),
                asset,
                install_root=install,
                snapshot_root=root / "snapshots",
                component_paths={"config": config},
                install_id="test-install",
                current_schema_versions={"bootstrap": 1, "config": 1, "state": 1},
            )

            state = json.loads((install / "state-v1.json").read_text())
            self.assertEqual(state["install_id"], "test-install")
            self.assertTrue(state["state_hmac"])
            self.assertEqual((install / "current" / "latticemind").read_text(), "new")
            state_path = install / "state-v1.json"
            original_state = state_path.read_bytes()
            replacement_key = root / "replacement.key"
            replacement_key.write_bytes(b"x" * 32)
            forged_state = dict(state)
            forged_state["state_key_path"] = str(replacement_key)
            forged_state.pop("state_hmac")
            forged_state["state_hmac"] = hmac.new(
                replacement_key.read_bytes(),
                json.dumps(forged_state, sort_keys=True, separators=(",", ":")).encode(),
                hashlib.sha256,
            ).hexdigest()
            state_path.write_text(json.dumps(forged_state))
            with self.assertRaises(UpdateError):
                rollback(
                    install_root=install,
                    snapshot=result["state"]["snapshot_path"],
                    manifest=manifest,
                    signature=self.signed(manifest),
                    compatible_version="v1.2.2",
                    component_allowlist={"config": config},
                    install_id="test-install",
                )
            state_path.write_bytes(original_state)

            with self.assertRaises(UpdateError):
                rollback(
                    install_root=install,
                    snapshot=result["state"]["snapshot_path"],
                    manifest=manifest,
                    signature=self.signed(manifest),
                    compatible_version="v1.2.2",
                    component_allowlist={"config": config},
                    install_id="other-install",
                )
            rollback(
                install_root=install,
                snapshot=result["state"]["snapshot_path"],
                manifest=manifest,
                signature=self.signed(manifest),
                compatible_version="v1.2.2",
                component_allowlist={"config": config},
                install_id="test-install",
            )
            self.assertEqual((install / "current" / "latticemind").read_text(), "old")
if __name__=="__main__": unittest.main()
