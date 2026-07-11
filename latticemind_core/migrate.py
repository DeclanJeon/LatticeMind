"""Journaled, fail-closed migration from legacy installation state to config-v1."""
from __future__ import annotations

import hashlib
import json
import secrets
import os
import tempfile
import shutil
from pathlib import Path
from typing import Any

from .config import CONFIG_SCHEMA, config_bytes, load_config, migrate_legacy_unix, migrate_windows_json
from .release import verify_manifest

RECEIPT = "migration-v1.json"
JOURNAL = "migration-v1.journal.json"
_TRACKED = ("reports", "integrations", "jobs", "state", "runtime")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def vault_manifest(root: os.PathLike[str] | str) -> dict[str, str]:
    """Return a complete path/type/content manifest (including empty files)."""
    root = Path(root)
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[rel] = "link:" + os.readlink(path)
        elif path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            result[rel] = "file:" + h.hexdigest()
        elif path.is_dir():
            result[rel] = "dir"
    return result
def _backup_owned(path: Path, destination: Path) -> None:
    if destination.exists():
        if path.is_file() and destination.is_file():
            same = path.read_bytes() == destination.read_bytes()
        else:
            same = vault_manifest(path) == vault_manifest(destination)
        if not same:
            raise ValueError(f"backup collision: {destination}")
        return
    _copy_owned(path, destination)
def _copy_owned(path: Path, destination: Path) -> None:
    if path.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(path, destination, symlinks=True)
    elif path.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def detect_unowned_collisions(candidates: Any, owned: Any = ()) -> list[str]:
    owned_set = {str(Path(p).resolve()) for p in owned}
    collisions = sorted(str(Path(p)) for p in candidates
                        if Path(p).exists() and str(Path(p).resolve()) not in owned_set)
    if collisions:
        raise ValueError("unowned path collision: " + ", ".join(collisions))
    return []


def _legacy_path(root: Path, platform: str) -> Path | None:
    names = ("config.json", "config") if platform == "windows" else ("config", "config.json")
    return next((root / name for name in names if (root / name).is_file()), None)


def _verified_receipt(destination: Path, receipt: Path) -> bool:
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        payload = destination.read_bytes()
        config = load_config(destination)
        migration = config.get("migration", {})
        return (data.get("schema") == "migration-v1" and
                data.get("config_sha256") == _sha(payload) and
                data.get("source_sha256") == migration.get("source_bytes_sha256") and
                data.get("vault_manifest_before") == data.get("vault_manifest_after") and
                migration.get("schema") == "migration-v1" and
                data.get("phase") == "committed")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
def recover_migration(config_root: os.PathLike[str] | str) -> bool:
    root = Path(config_root)
    destination, receipt = root / "config-v1.json", root / RECEIPT
    if _verified_receipt(destination, receipt):
        return False
    if destination.exists():
        destination.unlink()
        return True
    return False
def restore_migration(config_root: os.PathLike[str] | str) -> list[str]:
    root = Path(config_root)
    names = []
    for name in _TRACKED:
        backup = root / "backups" / "migration-v1" / name
        if backup.exists():
            _copy_owned(backup, root / name)
            names.append(name)
    return names


def migrate_install(config_root: os.PathLike[str] | str,
                    vault_root: os.PathLike[str] | str | None = None, *,
                    platform: str = "unix", source: os.PathLike[str] | str | None = None,
                    fail_after: str | None = None,
                    install_version: str | None = None,
                    trust: dict[str, Any] | None = None,
                    manifest: dict[str, Any] | None = None,
                    signature: bytes | None = None,
                    compatible_version: str | None = None) -> dict[str, Any]:
    root = Path(config_root)
    vault = Path(vault_root).expanduser() if vault_root is not None else None
    destination, receipt, journal = root / "config-v1.json", root / RECEIPT, root / JOURNAL
    if destination.is_file() and _verified_receipt(destination, receipt):
        return load_config(destination)
    if platform not in {"unix", "windows"}:
        raise ValueError("platform must be unix or windows")
    legacy = Path(source) if source is not None else _legacy_path(root, platform)
    raw = legacy.read_bytes() if legacy else b""
    backup = root / "backups" / "migration-v1"
    if legacy is not None:
        _backup_owned(legacy, backup / legacy.name)
    if legacy:
        result = migrate_windows_json(raw) if platform == "windows" else migrate_legacy_unix(raw)
    elif vault is not None:
        result = {"schema": CONFIG_SCHEMA, "vault_path": str(vault), "profile": "observe", "enabled_jobs": []}
    else:
        raise ValueError("legacy config or vault path is required")
    if vault is not None and result["vault_path"] != str(vault):
        raise ValueError("legacy and requested vault paths differ")
    before = vault_manifest(vault) if vault else {}
    result["enabled_jobs"] = []
    result.setdefault("install_id", secrets.token_hex(16))
    result["schema_versions"] = {"bootstrap": 1, "config": 1, "state": 1}
    provenance: dict[str, Any] = {"schema": "migration-v1", "source_bytes_sha256": _sha(raw),
                                   "vault_manifest": before}
    for name in _TRACKED:
        path = root / name
        provenance[name] = {"path": str(path), "exists": path.exists(),
                            "manifest": vault_manifest(path) if path.exists() else {}}
        if path.exists():
            _backup_owned(path, root / "backups" / "migration-v1" / name)
    result["migration"] = provenance
    if install_version:
        result["install_version"] = install_version
    verified = None
    if manifest is not None or signature is not None:
        if manifest is None or signature is None:
            raise ValueError("signed manifest and signature are required together")
        verified = verify_manifest(manifest, signature)
        expected = compatible_version or install_version
        if expected is not None and verified.get("version") != expected:
            raise ValueError("manifest compatibility mismatch")
        digest = _sha(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
        if trust is not None and trust.get("manifest_sha256") != digest:
            raise ValueError("manifest trust digest mismatch")
        result["trust"] = {"state": "verified", "key_id": str(verified["key_id"]),
                           "manifest_sha256": digest, "version": str(verified["version"])}
    elif trust is not None:
        raise ValueError("verified trust requires a signed manifest")
    result["manifest_path"] = str(root / "manifest-v1.json" if platform == "windows" else
                                  Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) /
                                  "latticemind" / "manifest-v1.json")
    payload = config_bytes(result)
    backup = root / "backups" / "migration-v1"
    journal_data = {"schema": "migration-v1-journal", "phase": "prepared", "config_sha256": _sha(payload),
                    "source_sha256": _sha(raw), "vault_manifest": before}
    _atomic_bytes(journal, (json.dumps(journal_data, sort_keys=True) + "\n").encode())
    try:
        journal_data["phase"] = "backed-up"
        _atomic_bytes(journal, (json.dumps(journal_data, sort_keys=True) + "\n").encode())
        if fail_after == "backup":
            raise RuntimeError("migration interrupted after backup")
        _atomic_bytes(destination, payload)
        if fail_after == "config":
            raise RuntimeError("migration interrupted after config")
        after = vault_manifest(vault) if vault else {}
        if after != before:
            raise RuntimeError("vault changed during migration")
        journal_data["phase"] = "committed"
        _atomic_bytes(journal, (json.dumps(journal_data, sort_keys=True) + "\n").encode())
        receipt_data = {"schema": "migration-v1", "config_sha256": _sha(payload),
                        "source_sha256": _sha(raw), "backup": str(backup),
                        "phase": "committed",
                        "vault_manifest_before": before, "vault_manifest_after": after,
                        "provenance": provenance}
        _atomic_bytes(receipt, (json.dumps(receipt_data, sort_keys=True, separators=(",", ":")) + "\n").encode())
        return result
    except Exception:
        # Restore exact owned state snapshots before exposing failure.
        restore_migration(root)
        if destination.exists() and not _verified_receipt(destination, receipt):
            destination.unlink()
        raise


def migrate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return migrate_install(*args, **kwargs)


def migrate_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return migrate_install(*args, **kwargs)
