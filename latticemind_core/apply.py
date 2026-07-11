"""One-file, managed-frontmatter transaction with crash recovery."""
from __future__ import annotations
import hashlib, json, os, tempfile, uuid
from pathlib import Path
from typing import Mapping
from .contracts import canonical_json
from .state import FileLock

MANAGED_KEYS = frozenset({"last_verified", "volatility", "verification_sources"})
_PHASES = frozenset({"prepared", "replacing", "replaced", "committed", "recovered"})
_HEX = frozenset("0123456789abcdef")

def _safe(path: Path) -> os.stat_result:
    st = path.lstat()
    if not path.is_file() or path.is_symlink(): raise PermissionError("target must be a regular non-symlink file")
    return st

def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)

def _frontmatter(data: bytes) -> tuple[bytes, bytes, str]:
    data.decode("utf-8")
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---": raise ValueError("managed frontmatter required")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip(b"\r\n") == b"---"), -1)
    if end < 0: raise ValueError("unterminated frontmatter")
    for line in lines[1:end]:
        raw = line.rstrip(b"\r\n")
        if not raw: continue
        if b":" not in raw: raise ValueError("ambiguous frontmatter")
        key, value = raw.split(b":", 1)
        if not key.strip(): raise ValueError("empty frontmatter key")
        if not value.strip(): raise ValueError("empty frontmatter value")
    return b"".join(lines[:end + 1]), b"".join(lines[end + 1:]), ""

def _managed_value(key: str, value: object) -> str:
    if value is None: raise PermissionError("deletes are forbidden")
    if key == "verification_sources":
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.startswith(("https://", "http://")) or "\n" in item or "\r" in item for item in value): raise ValueError("verification_sources must be a list of HTTP(S) URLs")
        return canonical_json(value)
    if not isinstance(value, str) or "\n" in value or "\r" in value: raise ValueError("managed scalar must be a single-line string")
    if key == "volatility" and value not in {"high", "medium", "low", "static"}: raise ValueError("invalid volatility")
    if key == "last_verified":
        from datetime import date
        try: date.fromisoformat(value)
        except ValueError as exc: raise ValueError("invalid last_verified date") from exc
    return value

def _render(header: bytes, body: bytes, updates: Mapping[str, object], nl: str) -> bytes:
    if not updates or any(k not in MANAGED_KEYS or not isinstance(k, str) for k in updates): raise PermissionError("only managed keys may change")
    rendered = {key: _managed_value(key, value).encode("utf-8") for key, value in updates.items()}
    lines = header.splitlines(keepends=True)
    found: set[str] = set()
    output: list[bytes] = []
    for line in lines:
        raw = line.rstrip(b"\r\n")
        if b":" in raw:
            key_bytes, _ = raw.split(b":", 1)
            key = key_bytes.decode("utf-8").strip()
            if key in rendered:
                ending = line[len(raw):]
                prefix = line[:len(key_bytes) + 1]
                output.append(prefix + b" " + rendered[key] + ending)
                found.add(key)
                continue
        output.append(line)
    for key, value in rendered.items():
        if key not in found:
            delimiter = output[-1]
            ending = b"\r\n" if delimiter.endswith(b"\r\n") else (b"\n" if delimiter.endswith(b"\n") else b"")
            if not ending:
                ending = next((b"\r\n" if line.endswith(b"\r\n") else b"\n" for line in output if line.endswith((b"\r\n", b"\n"))), b"\n")
            output.insert(len(output) - 1, key.encode() + b": " + value + ending)
    return b"".join(output) + body

def _wal(path: Path, value: dict) -> None:
    """Write WAL record atomically via temp+rename+fsync."""
    payload = canonical_json(value) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

def _authorized_target(vault_root: Path, target: Path) -> Path:
    raw = Path(os.path.abspath(target))
    resolved = target.resolve()
    if raw != resolved:
        raise PermissionError("WAL target contains a symlink")
    try:
        resolved.relative_to(vault_root.resolve())
    except ValueError as exc:
        raise PermissionError("WAL target outside configured vault") from exc
    return resolved
def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _authorized_transaction_root(vault_root: Path, transaction_root: Path) -> Path:
    vault = vault_root.resolve()
    root = transaction_root.resolve()
    try:
        root.relative_to(vault)
    except ValueError:
        return root
    raise PermissionError("transaction root must not be inside vault")



def apply_file(
    path: os.PathLike[str] | str,
    updates: Mapping[str, object],
    *,
    expected_preimage: str,
    transaction_root: os.PathLike[str] | str,
    vault_root: os.PathLike[str] | str,
) -> dict:
    target = _authorized_target(Path(vault_root), Path(path))
    root = Path(transaction_root)
    _authorized_transaction_root(Path(vault_root), root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    _fsync_dir(root)
    with FileLock(root / ".apply.lock"):
        _recover(root, Path(vault_root))
        st = _safe(target); original = target.read_bytes(); digest = _digest(original)
        if digest != expected_preimage: raise RuntimeError("preimage mismatch")
        header, body, nl = _frontmatter(original); replacement = _render(header, body, updates, nl)
        tx = root / uuid.uuid4().hex
        tx.mkdir()
        _fsync_dir(root)
        backup = tx / "preimage"
        with backup.open("wb") as handle:
            handle.write(original)
            os.chmod(backup, 0o600)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(tx)
        rec = {"schema":"apply-wal-v1", "target":str(target.resolve()), "target_dev":st.st_dev, "target_ino":st.st_ino, "replacement_dev":0, "replacement_ino":0, "preimage":digest, "postimage":_digest(replacement), "phase":"prepared"}
        _wal(tx / "transaction.json", rec)
        _fsync_dir(tx)
        _fsync_dir(root)
        if _safe(target).st_ino != st.st_ino or _digest(target.read_bytes()) != digest: raise RuntimeError("target raced")
        fd, name = tempfile.mkstemp(prefix=".latticemind-", dir=str(target.parent))
        try:
            os.fchmod(fd, st.st_mode & 0o7777)
            with os.fdopen(fd, "wb") as f: f.write(replacement); f.flush(); os.fsync(f.fileno())
            replacement_st = os.stat(name)
            rec.update({"replacement_dev": replacement_st.st_dev, "replacement_ino": replacement_st.st_ino, "phase": "replacing"})
            _wal(tx / "transaction.json", rec); _fsync_dir(tx); _fsync_dir(root)
            os.replace(name, target)
            _fsync_dir(target.parent)
            rec["phase"] = "replaced"; _wal(tx / "transaction.json", rec); _fsync_dir(tx); _fsync_dir(root)
            if _digest(target.read_bytes()) != rec["postimage"]: raise RuntimeError("postimage mismatch")
            rec["phase"] = "committed"; _wal(tx / "transaction.json", rec); _fsync_dir(tx); _fsync_dir(root)
            backup.unlink()
            _fsync_dir(tx)
            _fsync_dir(root)
        finally:
            try: os.unlink(name)
            except FileNotFoundError: pass
        return rec

def _validate_record(rec: object, root: Path, tx: Path, vault_root: Path) -> tuple[Path, Path]:
    required = {"schema", "target", "target_dev", "target_ino", "replacement_dev", "replacement_ino", "preimage", "postimage", "phase"}
    if not isinstance(rec, dict) or set(rec) != required or rec["schema"] != "apply-wal-v1" or rec["phase"] not in _PHASES or not _valid_digest(rec["preimage"]) or not _valid_digest(rec["postimage"]): raise ValueError("invalid WAL record")
    if not isinstance(rec["target"], str) or any(not isinstance(rec[key], int) for key in ("target_dev", "target_ino", "replacement_dev", "replacement_ino")): raise ValueError("invalid WAL target identity")
    if tx.resolve().parent != root.resolve(): raise PermissionError("transaction outside root")
    _authorized_transaction_root(vault_root, root)
    return _authorized_target(vault_root, Path(rec["target"])), tx / "preimage"

def _recover(root: Path, vault_root: Path) -> list[str]:
    restored = []
    for tx in root.iterdir() if root.exists() else ():
        journal = tx / "transaction.json"
        if not tx.is_dir() or not journal.exists() or tx.name == ".apply.lock": continue
        rec = json.loads(journal.read_text(encoding="utf-8")); target, backup = _validate_record(rec, root, tx, vault_root); st = _safe(target)
        identity = (st.st_dev, st.st_ino)
        original_identity = (rec["target_dev"], rec["target_ino"])
        replacement_identity = (rec["replacement_dev"], rec["replacement_ino"])
        current = _digest(target.read_bytes())
        if rec["phase"] == "prepared" and identity != original_identity:
            raise PermissionError("target identity changed")
        if rec["phase"] == "replacing" and (identity, current) not in {(original_identity, rec["preimage"]), (replacement_identity, rec["postimage"])}:
            raise PermissionError("target identity or content changed")
        if rec["phase"] == "replaced" and (identity, current) != (replacement_identity, rec["postimage"]):
            raise PermissionError("target identity or content changed")
        if rec["phase"] == "committed" and (identity, current) != (replacement_identity, rec["postimage"]):
            raise PermissionError("target identity or content changed")
        if current not in {rec["preimage"], rec["postimage"]}: raise RuntimeError("unrecognized target content")
        if rec["phase"] in {"committed", "recovered"} or current == rec["preimage"]:
            if rec["phase"] in {"committed", "recovered"} and backup.exists():
                backup.unlink()
                _fsync_dir(tx)
            continue
        if not backup.is_file() or backup.is_symlink() or _digest(backup.read_bytes()) != rec["preimage"]: raise RuntimeError("invalid preimage backup")
        os.replace(backup, target)
        with target.open("rb") as handle: os.fsync(handle.fileno())
        _fsync_dir(target.parent)
        rec["phase"] = "recovered"; _wal(journal, rec); _fsync_dir(tx); _fsync_dir(root)
        restored.append(str(target))
    return restored

def recover(
    transaction_root: os.PathLike[str] | str,
    *,
    vault_root: os.PathLike[str] | str,
) -> list[str]:
    root = Path(transaction_root)
    _authorized_transaction_root(Path(vault_root), root)
    root.mkdir(parents=True, exist_ok=True)
    _fsync_dir(root)
    with FileLock(root / ".apply.lock"):
        return _recover(root, Path(vault_root))
