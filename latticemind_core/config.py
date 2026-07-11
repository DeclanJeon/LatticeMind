"""Strict config-v1 loading and non-executing legacy migration."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePath
import re
import shlex
from typing import Any

from .contracts import PermissionProfile, canonical_json

CONFIG_SCHEMA = "config-v1"
_ALLOWED = {"schema", "vault_path", "profile", "enabled_jobs", "backend", "upstream", "install_id", "install_version", "release_id", "migration", "manifest_path", "trust", "schema_versions"}
_LEGACY_KEYS = {
    "VAULT", "VAULT_PATH", "LATTICEMIND_VAULT",
    "PROFILE", "LATTICEMIND_PROFILE", "BACKEND",
    "BACKUP_DIR", "INSTALL_GJC", "INSTALL_CODEX", "AGENT_LIST", "VERSION",
}
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(?:%q-)?(.*)$")


def _path(value: Any, name: str = "vault_path") -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty path")
    p = Path(value)
    normalized_parts = value.replace("\\", "/").split("/")
    if ".." in normalized_parts:
        raise ValueError("path traversal is not allowed")
    return str(p)


def parse_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != CONFIG_SCHEMA:
        raise ValueError("unsupported config schema")
    unknown = set(value) - _ALLOWED
    if unknown:
        raise ValueError(f"unknown config fields: {', '.join(sorted(unknown))}")
    result = dict(value)
    result["vault_path"] = _path(result.get("vault_path"))
    try:
        result["profile"] = PermissionProfile(result.get("profile", "observe")).value
    except ValueError as exc:
        raise ValueError("invalid permission profile") from exc
    jobs = result.get("enabled_jobs", [])
    if not isinstance(jobs, list) or any(not isinstance(j, str) or not j for j in jobs):
        raise ValueError("enabled_jobs must be a list of strings")
    if "backend" in result and (not isinstance(result["backend"], str) or not result["backend"]):
        raise ValueError("backend must be a string")
    if "install_id" in result and (
        not isinstance(result["install_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", result["install_id"])
    ):
        raise ValueError("install_id must be an opaque 8-128 character identifier")
    if "upstream" in result:
        if not isinstance(result["upstream"], dict) or set(result["upstream"]) - {"url", "requested_ref", "resolved_commit"}:
            raise ValueError("invalid upstream")
    if "manifest_path" in result:
        result["manifest_path"] = _path(result["manifest_path"], "manifest_path")
    if "migration" in result:
        migration = result["migration"]
        if (not isinstance(migration, dict) or migration.get("schema") != "migration-v1"
                or not isinstance(migration.get("source_bytes_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", migration["source_bytes_sha256"])):
            raise ValueError("invalid migration metadata")
        if any(not isinstance(migration.get(name), dict)
               for name in ("reports", "integrations", "jobs", "state", "runtime")):
            raise ValueError("incomplete migration metadata")
    if "trust" in result:
        trust = result["trust"]
        if (
            not isinstance(trust, dict)
            or set(trust) - {"state", "key_id", "manifest_sha256", "version"}
            or trust.get("state") not in {"verified", "unavailable", "revoked"}
        ):
            raise ValueError("invalid release trust")
        for key in ("key_id", "manifest_sha256", "version"):
            if key in trust and (not isinstance(trust[key], str) or not trust[key]):
                raise ValueError("invalid release trust")
    if "schema_versions" in result:
        versions = result["schema_versions"]
        if (
            not isinstance(versions, dict)
            or set(versions) != {"bootstrap", "config", "state"}
            or any(type(versions[key]) is not int or versions[key] < 1 for key in versions)
        ):
            raise ValueError("invalid schema versions")
    return result


def load_config(path: os.PathLike[str] | str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid config JSON") from exc
    return parse_config(value)
def _decode_shell_word(value: str) -> str:
    if any(token in value for token in ("$", "`", ";", "\n", "\r")):
        raise ValueError("unsafe legacy value")
    try:
        words = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("invalid legacy shell quoting") from exc
    if len(words) != 1:
        raise ValueError("legacy value must contain exactly one shell word")
    return words[0]


def migrate_legacy_unix(raw: bytes | str) -> dict[str, Any]:
    """Parse only the known KEY=%q-value assignment grammar; never execute it."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    if not isinstance(text, str):
        raise ValueError("legacy config must be text")
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGN.fullmatch(stripped)
        if not match or match.group(1) not in _LEGACY_KEYS:
            raise ValueError("unsupported legacy config syntax")
        val = _decode_shell_word(match.group(2))
        values[match.group(1)] = val
    vault = values.get("VAULT") or values.get("VAULT_PATH") or values.get("LATTICEMIND_VAULT")
    if not vault:
        raise ValueError("legacy config has no vault path")
    profile = values.get("PROFILE") or values.get("LATTICEMIND_PROFILE") or "observe"
    config = {
        "schema": CONFIG_SCHEMA,
        "vault_path": _path(vault),
        "profile": PermissionProfile(profile).value,
        "enabled_jobs": [],
        "install_id": "legacy-" + hashlib.sha256(vault.encode("utf-8")).hexdigest()[:24],
    }
    if "BACKEND" in values:
        config["backend"] = values["BACKEND"]
    return parse_config(config)


def migrate_windows_json(raw: bytes | str) -> dict[str, Any]:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid Windows config JSON") from exc
    if not isinstance(value, dict) or set(value) - {"vault_path", "vault", "profile", "backend", "enabled_jobs"}:
        raise ValueError("unknown Windows config fields")
    vault = value.get("vault_path", value.get("vault"))
    result = {
        "schema": CONFIG_SCHEMA,
        "vault_path": _path(vault),
        "profile": value.get("profile", "observe"),
        "enabled_jobs": value.get("enabled_jobs", []),
        "install_id": "legacy-" + hashlib.sha256(str(vault).encode("utf-8")).hexdigest()[:24],
    }
    if "backend" in value: result["backend"] = value["backend"]
    return parse_config(result)


def config_bytes(config: dict[str, Any]) -> bytes:
    return (canonical_json(parse_config(config)) + "\n").encode("utf-8")
