"""Deterministic, report-only freshness scanning."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import unicodedata
from typing import Any, Iterable, Mapping

from .config import parse_config
from .contracts import canonical_json, validate_schema

SCHEMA = "freshness-run-v1"
TTL_DAYS = {"high": 7, "medium": 30, "low": 90, "static": 365}
MAX_CANDIDATES = 20
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

class FreshnessError(RuntimeError):
    pass
class SecurityError(FreshnessError):
    pass
class ChangedTreeError(SecurityError):
    pass
def _publish_exclusive(path: Path, payload: bytes) -> None:
    """Publish one output without following or replacing an existing path."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            existing = os.lstat(path)
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise SecurityError(f"output destination is not publishable: {path.name}")
            read_flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
            read_fd = os.open(path, read_flags)
            opened = os.fstat(read_fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (existing.st_dev, existing.st_ino):
                os.close(read_fd)
                raise SecurityError(f"output destination changed during publish: {path.name}")
            with os.fdopen(read_fd, "rb") as stream:
                if stream.read(len(payload) + 1) != payload:
                    raise SecurityError(f"output destination collision: {path.name}")
            return
        except SecurityError:
            raise
        except OSError as read_exc:
            raise SecurityError(f"output destination is not publishable: {path.name}") from read_exc
    except OSError as exc:
        raise SecurityError(f"output destination is not publishable: {path.name}") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _prepare_output_dir(path: Path) -> Path:
    """Create an output directory while rejecting symlinked path components."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except FileExistsError:
                info = current.lstat()
            else:
                continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SecurityError("work output must use real directories")
    return absolute


def path_id(path: str | os.PathLike[str]) -> str:
    """Return the portable NFC-normalized relative identifier."""
    value = str(path).replace("\\", "/")
    value = unicodedata.normalize("NFC", value).strip("/")
    return value


def _frontmatter(text: str) -> tuple[dict[str, Any], bool]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, False
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("unterminated frontmatter")
    result: dict[str, Any] = {}
    allowed = {"last_verified", "volatility", "verification_sources"}
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw or raw[:1].isspace():
            raise ValueError("malformed frontmatter")
        key, value = raw.split(":", 1)
        key, value = key.strip(), value.strip()
        if key not in allowed or key in result:
            raise ValueError("unsupported or duplicate frontmatter field")
        if key == "verification_sources":
            if value in ("", "[]"): result[key] = []
            elif value.startswith("[") and value.endswith("]"):
                vals = [x.strip().strip("'\"") for x in value[1:-1].split(",") if x.strip()]
                if any(not x or "\n" in x for x in vals): raise ValueError("invalid verification_sources")
                result[key] = vals
            else: raise ValueError("verification_sources must be an inline list")
        elif key == "volatility":
            value = value.strip("'\"")
            if value not in TTL_DAYS: raise ValueError("invalid volatility")
            result[key] = value
        else:
            value = value.strip("'\"")
            try: date.fromisoformat(value)
            except ValueError as exc: raise ValueError("invalid last_verified") from exc
            result[key] = value
    return result, True


def parse_frontmatter(raw: bytes) -> dict[str, Any]:
    try: text = raw.decode("utf-8")
    except UnicodeDecodeError as exc: raise ValueError("markdown is not UTF-8") from exc
    return _frontmatter(text)[0]


def _read_regular(path: Path, rel: str, budget: list[int]) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SecurityError(f"file cannot be opened safely: {rel}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise SecurityError(f"non-regular file refused: {rel}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise SecurityError(f"file byte limit exceeded: {rel}")
        after = os.fstat(fd)
        identity = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns):
            raise SecurityError(f"file changed while scanning: {rel}")
    finally:
        os.close(fd)
    current = path.lstat()
    if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
        raise SecurityError(f"file identity changed while scanning: {rel}")
    budget[0] += len(b"".join(chunks))
    if budget[0] > MAX_TOTAL_BYTES:
        raise SecurityError("vault byte limit exceeded")
    return b"".join(chunks), before


def _entry(rel: str, p: Path, budget: list[int], contents: dict[str, bytes] | None = None) -> dict[str, Any]:
    st = p.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise SecurityError(f"link refused: {rel}")
    if getattr(st, "st_reparse_tag", 0):
        raise SecurityError(f"reparse point refused: {rel}")
    if not stat.S_ISREG(st.st_mode) and not stat.S_ISDIR(st.st_mode):
        raise SecurityError(f"special file refused: {rel}")
    item = {
        "path": rel, "id": path_id(rel),
        "type": "directory" if stat.S_ISDIR(st.st_mode) else "file",
        "mode": stat.S_IMODE(st.st_mode),
        "reparse_tag": getattr(st, "st_reparse_tag", 0),
    }
    if item["type"] == "file":
        raw, opened = _read_regular(p, rel, budget)
        if contents is not None:
            contents[rel] = raw
        item.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                    hardlink_identity=f"{opened.st_dev}:{opened.st_ino}",
                    hardlink_count=opened.st_nlink)
    return item


def _manifest_snapshot(vault: os.PathLike[str] | str) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    root = Path(vault).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise SecurityError("vault must be a real directory")
    result: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    budget = [0]
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs.sort(); files.sort()
        for name in list(dirs) + list(files):
            p = Path(base) / name
            rel = p.relative_to(root).as_posix()
            result.append(_entry(rel, p, budget, contents))
    result.sort(key=lambda x: x["id"])
    seen: dict[str, str] = {}
    for item in result:
        collision_id = item["id"].casefold()
        if collision_id in seen and seen[collision_id] != item["path"]:
            raise SecurityError(f"case/unicode path collision: {seen[collision_id]} / {item['path']}")
        seen[collision_id] = item["path"]
    return result, contents


def manifest(vault: os.PathLike[str] | str) -> list[dict[str, Any]]:
    return _manifest_snapshot(vault)[0]


def _due(meta: Mapping[str, Any], today: date) -> tuple[bool, int]:
    volatility = str(meta.get("volatility", "medium"))
    ttl = TTL_DAYS.get(volatility, TTL_DAYS["medium"])
    raw = meta.get("last_verified")
    if not raw: return True, ttl
    verified = date.fromisoformat(str(raw))
    return today >= verified + timedelta(days=ttl), ttl


def scan(vault: os.PathLike[str] | str, *, as_of: date | None = None, output_dir: os.PathLike[str] | str | None = None) -> dict[str, Any]:
    root = Path(vault).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise SecurityError("vault must be a real directory")
    root = Path(os.path.abspath(root))
    before, snapshot = _manifest_snapshot(root)
    today = as_of or date.today()
    candidates = []
    for item in before:
        if item["type"] != "file" or not item["path"].lower().endswith(".md"): continue
        raw = snapshot[item["path"]]
        meta, has = _frontmatter(raw.decode("utf-8"))
        if has:
            due, ttl = _due(meta, today)
            if due: candidates.append({"path": item["path"], "id": item["id"], "volatility": meta.get("volatility", "medium"), "ttl_days": ttl, "last_verified": meta.get("last_verified")})
    candidates.sort(key=lambda x: (x["last_verified"] is not None, x["last_verified"] or "", -x["ttl_days"], x["id"]))
    candidates = candidates[:MAX_CANDIDATES]
    after = manifest(root)
    if canonical_json(before) != canonical_json(after): raise ChangedTreeError("vault changed during scan")
    report: dict[str, Any] = {"schema": SCHEMA, "status": "succeeded", "vault_manifest": before, "candidates": candidates, "evidence": [], "candidate_cap": MAX_CANDIDATES}
    if output_dir is not None:
        requested_out = Path(output_dir).expanduser()
        resolved_out = requested_out.resolve()
        try:
            resolved_out.relative_to(root)
        except ValueError:
            pass
        else:
            raise SecurityError("work output must be outside the vault")
        out = _prepare_output_dir(requested_out)
        packet = {"schema": SCHEMA, "candidates": candidates, "evidence": []}
        packet_bytes = (canonical_json(packet) + "\n").encode("utf-8")
        packet_hash = hashlib.sha256(packet_bytes).hexdigest()
        report["work_packet_sha256"] = packet_hash
        _publish_exclusive(out / f"freshness-work-{packet_hash}.json", packet_bytes)
        report_digest = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
        report["report_sha256"] = report_digest
        report_bytes = (canonical_json(report) + "\n").encode("utf-8")
        _publish_exclusive(out / f"freshness-report-{report_digest}.json", report_bytes)
    return report


def validate_evidence(value: Any) -> dict[str, Any]:
    allowed = {"schema", "url", "reachable", "supports", "note"}
    obj = validate_schema(value, "freshness-evidence-v1", allowed)
    if not isinstance(obj.get("url"), str) or not isinstance(obj.get("reachable"), bool): raise ValueError("invalid evidence")
    if not isinstance(obj.get("supports"), bool): raise ValueError("evidence requires supports")
    return obj

run_scan = scan
build_manifest = manifest
