"""User-only state, atomic persistence, locking, and tamper-evident ledger."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping

from .contracts import RunStatus, canonical_json

import errno

def write_json_atomic(path: os.PathLike[str] | str, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical_json(value) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
        try:
            dfd = os.open(target.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        except OSError as exc:
            raise OSError("unable to fsync state directory") from exc
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


class FileLock:
    def __init__(self, path: os.PathLike[str] | str, timeout: float = 0.0):
        self.path, self.timeout, self._handle = Path(path), timeout, None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise
                if time.monotonic() >= deadline:
                    self._handle.close(); self._handle = None
                    raise TimeoutError("state lock is held")
                time.sleep(0.01)

    def release(self) -> None:
        if self._handle is None: return
        if os.name == "nt":
            import msvcrt
            self._handle.seek(0); msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close(); self._handle = None

    def __enter__(self): self.acquire(); return self
    def __exit__(self, *_): self.release()


_ALLOWED_TRANSITIONS = {
    None: {RunStatus.QUEUED}, RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.BLOCKED, RunStatus.FAILED},
    RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.TIMED_OUT},
    RunStatus.SUCCEEDED: {RunStatus.APPLIED}, RunStatus.APPLIED: set(), RunStatus.BLOCKED: set(), RunStatus.FAILED: set(), RunStatus.TIMED_OUT: set(),
}


def validate_transition(previous: str | RunStatus | None, current: str | RunStatus) -> None:
    before = None if previous is None else RunStatus(previous)
    after = RunStatus(current)
    if after not in _ALLOWED_TRANSITIONS[before]:
        raise ValueError(f"invalid run transition: {previous!s} -> {after.value}")
_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "nonce", "approval_code", "private_key")


def redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str) and os.path.isabs(value):
        return "<absolute-path>"
    return value


class Ledger:
    def __init__(self, path: os.PathLike[str] | str, redactor: Callable[[Any], Any] | None = None):
        self.path, self.redactor = Path(path), redactor or redact

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        clean = self.redactor(dict(record))
        if not isinstance(clean, dict): raise ValueError("redactor must return an object")
        lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        if "status" in clean:
            try:
                prior_status = None
                for prior_line in reversed(lines):
                    prior = json.loads(prior_line)
                    if prior.get("run_id") == clean.get("run_id") and "status" in prior:
                        prior_status = prior["status"]
                        break
                validate_transition(prior_status, clean["status"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid ledger run transition") from exc
        previous = ""
        sequence = 0
        if lines:
            try:
                last = json.loads(lines[-1]); previous = last["hash"]; sequence = last["sequence"] + 1
            except (ValueError, KeyError, TypeError) as exc: raise ValueError("corrupt ledger") from exc
        clean["sequence"], clean["previous_hash"] = sequence, previous
        clean["hash"] = hashlib.sha256(canonical_json(clean).encode("utf-8")).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write((canonical_json(clean) + "\n").encode("utf-8")); handle.flush(); os.fsync(handle.fileno())
        return clean

    def verify(self) -> bool:
        previous = ""; sequence = 0
        if not self.path.exists(): return True
        for line in self.path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("sequence") != sequence or item.get("previous_hash") != previous: return False
            digest = item.get("hash"); unsigned = dict(item); unsigned.pop("hash", None)
            if digest != hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest(): return False
            previous, sequence = digest, sequence + 1
        return True


class StateStore:
    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        try: os.chmod(self.root, 0o700)
        except OSError as exc: raise PermissionError("unable to secure private state directory") from exc
        self.lock = FileLock(self.root / ".lock")
        self.ledger = Ledger(self.root / "ledger-v1.jsonl")

    def write_status(self, status: Mapping[str, Any]) -> None:
        write_json_atomic(self.root / "status-v1.json", dict(status))

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self.lock: return self.ledger.append(record)
