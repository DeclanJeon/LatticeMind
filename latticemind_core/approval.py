"""TTY-bound, one-time approval challenges."""
from __future__ import annotations
import base64, hashlib, json, os, re, secrets, sys, time
import binascii
from pathlib import Path
from typing import Callable
from .state import FileLock, write_json_atomic

_APPROVAL_ID = re.compile(r"[0-9a-f]{32}\Z")

def actor_identity() -> str:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        name = ctypes.create_unicode_buffer(257)
        size = wintypes.DWORD(len(name))
        if not ctypes.windll.secur32.GetUserNameExW(2, name, ctypes.byref(size)):
            raise PermissionError("unable to determine Windows domain identity")
        return name.value
    return str(os.getuid())

def tty_identity() -> str:
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream.isatty(): return os.ttyname(stream.fileno())
        except (OSError, AttributeError): pass
    return ""
def _identities(provider: Callable[[], tuple[str, str]] | None = None) -> tuple[str, str]:
    if provider is not None:
        value = provider()
        if (not isinstance(value, tuple) or len(value) != 2 or
                not all(isinstance(item, str) and item for item in value)):
            raise PermissionError("trusted identity provider returned invalid identity")
        return value
    return actor_identity(), tty_identity()

def _approval_path(root: os.PathLike[str] | str, approval_id: str) -> Path:
    if not isinstance(approval_id, str) or _APPROVAL_ID.fullmatch(approval_id) is None:
        raise ValueError("invalid approval id")
    return Path(root) / (approval_id + ".json")

def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value)
    try: os.chmod(path, 0o600)
    except OSError: pass

def issue_challenge(root: os.PathLike[str] | str, *, run_id: str, proposal_digest: str, path_digest: str,
                    preimage_digest: str, install_id: str, actor: str | None = None, terminal: str | None = None,
                    now: float | None = None, approval_id: str | None = None,
                    identity_provider: Callable[[], tuple[str, str]] | None = None) -> tuple[dict, str]:
    if not all(isinstance(x, str) and x for x in (run_id, proposal_digest, path_digest, preimage_digest, install_id)):
        raise ValueError("approval binding fields are required")
    if approval_id is not None and _APPROVAL_ID.fullmatch(approval_id) is None:
        raise ValueError("invalid approval id")
    nonce = secrets.token_bytes(32)
    code = base64.b32encode(nonce).decode("ascii").rstrip("=")
    if actor is not None or terminal is not None:
        raise PermissionError("asserted identities are not accepted")
    actor_value, terminal_value = _identities(identity_provider)
    if not terminal_value: raise PermissionError("interactive TTY required")
    issued = time.time() if now is None else float(now)
    record = {"schema": "approval-v1", "approval_id": approval_id or secrets.token_hex(16), "run_id": run_id,
              "proposal_digest": proposal_digest, "path_digest": path_digest, "preimage_digest": preimage_digest,
              "actor": actor_value, "terminal": terminal_value, "install_id": install_id,
              "nonce_sha256": hashlib.sha256(nonce).hexdigest(), "issued_at": issued, "expires_at": issued + 600,
              "state": "issued"}
    root_path = Path(root)
    with FileLock(root_path / ".approval.lock", timeout=5):
        path = _approval_path(root_path, record["approval_id"])
        if path.exists(): raise FileExistsError("approval id already exists")
        _save(path, record)
    return record, code

def load_approval(root: os.PathLike[str] | str, approval_id: str) -> dict:
    return json.loads(_approval_path(root, approval_id).read_text(encoding="utf-8"))

def verify_code(record: dict, code: str) -> bool:
    try: nonce = base64.b32decode(code.upper() + "=" * ((8-len(code)%8)%8), casefold=True)
    except (binascii.Error, TypeError, ValueError): return False
    return len(nonce) == 32 and secrets.compare_digest(hashlib.sha256(nonce).hexdigest(), record.get("nonce_sha256", ""))

def reserve(root: os.PathLike[str] | str, approval_id: str, code: str, *, run_id: str, proposal_digest: str,
            path_digest: str, preimage_digest: str, install_id: str, actor: str | None = None,
            terminal: str | None = None, now: float | None = None,
            identity_provider: Callable[[], tuple[str, str]] | None = None) -> dict:
    root_path = Path(root)
    with FileLock(root_path / ".approval.lock", timeout=5):
        path = _approval_path(root_path, approval_id)
        rec = load_approval(root_path, approval_id)
        current = time.time() if now is None else float(now)
        if rec.get("state") != "issued" or current >= float(rec.get("expires_at", 0)) or not verify_code(rec, code):
            raise PermissionError("approval rejected")
        actor_value, terminal_value = _identities(identity_provider)
        if actor is not None or terminal is not None:
            raise PermissionError("asserted identities are not accepted")
        expected = (run_id, proposal_digest, path_digest, preimage_digest, install_id,
                    actor_value, terminal_value)
        actual = (rec.get("run_id"), rec.get("proposal_digest"), rec.get("path_digest"),
                  rec.get("preimage_digest"), rec.get("install_id"), rec.get("actor"), rec.get("terminal"))
        if actual != expected: raise PermissionError("approval binding mismatch")
        rec["state"] = "reserved"; rec["reserved_at"] = current
        _save(path, rec)
        return rec

def consume(root: os.PathLike[str] | str, approval_id: str, *, install_id: str,
            actor: str | None = None, terminal: str | None = None,
            now: float | None = None,
            identity_provider: Callable[[], tuple[str, str]] | None = None) -> dict:
    root_path = Path(root)
    with FileLock(root_path / ".approval.lock", timeout=5):
        path = _approval_path(root_path, approval_id)
        rec = load_approval(root_path, approval_id)
        if rec.get("state") != "reserved":
            raise PermissionError("approval is not reserved")
        current = time.time() if now is None else float(now)
        if current >= float(rec.get("expires_at", 0)):
            raise PermissionError("approval has expired")
        actor_value, terminal_value = _identities(identity_provider)
        if actor is not None or terminal is not None:
            raise PermissionError("asserted identities are not accepted")
        if (rec.get("install_id"), rec.get("actor"), rec.get("terminal")) != (install_id, actor_value, terminal_value):
            raise PermissionError("approval binding mismatch")
        rec["state"] = "consumed"; rec["consumed_at"] = current
        _save(path, rec)
        return rec
