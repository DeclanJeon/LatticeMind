"""Explicit, fail-closed agent adapter declarations."""
from __future__ import annotations
from dataclasses import dataclass, field
import os
import re
import shutil
import subprocess
from typing import Mapping, Sequence

_SAFE_FLAGS = {
    "--mode": {"observe"},
    "--sandbox": {"read-only"},
    "--permission-mode": {"plan"},
    "--approval-mode": {"plan", "read-only"},
}
_VERSION_RE = re.compile(r"\b\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?\b")

@dataclass(frozen=True)
class BackendAdapter:
    name: str
    executable: str
    min_version: str
    max_version: str | None
    capabilities: frozenset[str]
    observe_argv: tuple[str, ...]
    env_allowlist: tuple[str, ...]
    output_schema: str = "evidence-response-v1"
    network_research: bool = False
    enabled: bool = False
    sandbox_verified: bool = False
    verified_versions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def command(self, packet: str, output: str, *, version: str | None = None) -> list[str]:
        if not self.enabled or not self.sandbox_verified:
            raise PermissionError("backend observe execution is disabled pending a verified sandbox contract")
        if "observe" not in self.capabilities or "write" in self.capabilities:
            raise PermissionError("backend has no safe observe capability")
        if not packet or not output:
            raise ValueError("packet and output are required")
        if version is None or version not in self.verified_versions:
            raise PermissionError("backend version has no verified observe contract")
        argv = tuple(self.verified_versions[version])
        if self.name not in _OBSERVE_ARGV or argv != _OBSERVE_ARGV[self.name]:
            raise PermissionError("backend version is not an exact certified observe contract")
        if not _safe_observe_argv(argv):
            raise PermissionError("backend observe contract contains unsafe argv")
        return [self.executable, *argv, "--input", packet, "--output", output]

def _safe_observe_argv(argv: Sequence[str]) -> bool:
    """Reject write/approval flags, options with missing values, and shell syntax."""
    if any(not isinstance(x, str) or not x or x in {"--approve", "--yes", "--write"} or
           any(ch in x for ch in (";", "|", "&", "\x00")) for x in argv):
        return False
    for i, token in enumerate(argv):
        if token in _SAFE_FLAGS:
            if i + 1 >= len(argv) or argv[i + 1] not in _SAFE_FLAGS[token]:
                return False
    return True

_NAMES = ("gjc", "omp", "codex", "claude", "opencode", "pi", "gemini", "hermes")
_OBSERVE_ARGV = {
    "gjc": ("run", "--mode", "observe", "--format", "json"),
    "omp": ("run", "--mode", "observe", "--format", "json"),
    "codex": ("exec", "--sandbox", "read-only", "--json"),
    "claude": ("-p", "--permission-mode", "plan"),
    "opencode": ("run", "--format", "json", "--read-only"),
    "pi": ("-p", "--read-only", "--json"),
    "gemini": ("run", "--output-format", "json", "--approval-mode", "plan"),
    "hermes": ("run", "--json", "--read-only"),
}

def _adapter(name: str) -> BackendAdapter:
    return BackendAdapter(name, name, "unverified", None,
        frozenset({"observe", "evidence"}), _OBSERVE_ARGV[name],
        ("LANG", "LC_ALL", "PATH"), network_research=name in {"gemini", "hermes"},
        verified_versions={})

ADAPTERS: Mapping[str, BackendAdapter] = {n: _adapter(n) for n in _NAMES}

def get_adapter(name: str) -> BackendAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise ValueError("unsupported backend") from exc

def probe_version(adapter: BackendAdapter, *, timeout: float = 2.0) -> dict[str, object]:
    """Observe-only executable/version discovery; never makes an adapter executable."""
    path = shutil.which(adapter.executable)
    result: dict[str, object] = {"backend": adapter.name, "executable": adapter.executable,
                                 "installed": bool(path), "version": None, "verified": False,
                                 "blocked_reason": "backend_not_installed" if not path else
                                 "blocked/backend_version_unverified"}
    if not path:
        return result
    try:
        completed = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
            shell=False, env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "LANG": "C"})
        text = (completed.stdout + completed.stderr).decode("utf-8", "replace")
        match = _VERSION_RE.search(text)
        if match:
            result["version"] = match.group(0)
            result["verified"] = match.group(0) in adapter.verified_versions
            if result["verified"]:
                result["blocked_reason"] = None
    except (OSError, subprocess.TimeoutExpired):
        result["blocked_reason"] = "backend_version_probe_failed"
    return result

def select_adapter(configured: Sequence[str], installed: Mapping[str, str] | None = None) -> BackendAdapter:
    versions = installed or {}
    for name in configured:
        adapter = ADAPTERS.get(name)
        version = versions.get(name)
        if adapter is None or not isinstance(version, str) or not adapter.enabled or not adapter.sandbox_verified:
            continue
        if version not in adapter.verified_versions or "observe" not in adapter.capabilities or "write" in adapter.capabilities:
            continue
        return adapter
    raise RuntimeError("blocked/backend_no_observe_capability")

def detect_capabilities(adapter: BackendAdapter, version: str) -> dict[str, object]:
    if not isinstance(version, str) or version not in adapter.verified_versions:
        raise RuntimeError("blocked/backend_version_unverified")
    argv = adapter.verified_versions[version]
    if not _safe_observe_argv(argv):
        raise RuntimeError("blocked/backend_unsafe_argv")
    return {"backend": adapter.name, "version": version, "capabilities": sorted(adapter.capabilities),
            "observe": "observe" in adapter.capabilities and "write" not in adapter.capabilities,
            "network_research": adapter.network_research, "output_schema": adapter.output_schema,
            "observe_argv": list(argv)}