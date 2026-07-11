"""Isolated non-shell backend execution."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import selectors
import subprocess
import tempfile
import time

from .backends import BackendAdapter


@dataclass(frozen=True)
class BackendResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    evidence: dict[str, object] | None = None


class BackendRunner:
    def __init__(self, *, output_limit: int = 1_048_576, timeout: float = 900.0):
        if output_limit <= 0 or timeout <= 0:
            raise ValueError("limits must be positive")
        self.output_limit = output_limit
        self.timeout = timeout

    def run(
        self,
        adapter: BackendAdapter,
        packet: os.PathLike[str] | str,
        *,
        workdir: os.PathLike[str] | str | None = None,
        env: dict[str, str] | None = None,
        version: str | None = None,
    ) -> BackendResult:
        packet_path = Path(packet).resolve()
        if not packet_path.is_file():
            raise FileNotFoundError(packet_path)
        # Caller-controlled workdir is intentionally ignored. The backend receives
        # only a fresh outside-vault workspace that is removed after validation.
        cwd = Path(tempfile.mkdtemp(prefix="latticemind-backend-")).resolve()
        try:
            output = cwd / "evidence.json"
            command = adapter.command(str(packet_path), str(output), version=version)
            self._validate_command(command)
            allowed = set(adapter.env_allowlist)
            clean_env = {key: value for key, value in (env or {}).items() if key in allowed}
            clean_env.setdefault("LC_ALL", "C")
            clean_env.setdefault("LANG", "C")
            kwargs: dict[str, object] = {
                "cwd": str(cwd),
                "env": clean_env,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "shell": False,
            }
            if os.name != "nt":
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(command, **kwargs)
            timed_out = False
            captured_stdout = b""
            captured_stderr = b""
            try:
                # Read in bounded chunks to enforce output limit during capture
                sel = selectors.DefaultSelector()
                if proc.stdout:
                    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
                if proc.stderr:
                    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")
                deadline = time.monotonic() + self.timeout
                while sel.get_map():
                    remaining = max(0.01, deadline - time.monotonic()) if deadline else None
                    ready = sel.select(timeout=min(remaining, 1.0) if remaining else 1.0)
                    for key, _ in ready:
                        chunk = key.fileobj.read1(65536) if hasattr(key.fileobj, 'read1') else key.fileobj.read(65536)
                        if not chunk:
                            sel.unregister(key.fileobj)
                            continue
                        if key.data == "stdout":
                            captured_stdout += chunk
                            if len(captured_stdout) > self.output_limit:
                                captured_stdout = captured_stdout[:self.output_limit]
                                sel.unregister(key.fileobj)
                                key.fileobj.close()
                        else:
                            captured_stderr += chunk
                            if len(captured_stderr) > self.output_limit:
                                captured_stderr = captured_stderr[:self.output_limit]
                                sel.unregister(key.fileobj)
                                key.fileobj.close()
                    if deadline and time.monotonic() >= deadline:
                        timed_out = True
                        _terminate(proc)
                        sel.close()
                        break
                sel.close()
                # Close any remaining pipes and wait for process completion
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
                if proc.stderr and not proc.stderr.closed:
                    proc.stderr.close()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate(proc)
                    proc.wait()
                stdout, stderr = captured_stdout, captured_stderr
            except (subprocess.TimeoutExpired, OSError):
                timed_out = True
                _terminate(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except Exception:
                    stdout, stderr = captured_stdout or b"", captured_stderr or b""
            evidence = None
            if proc.returncode == 0 and not timed_out:
                evidence = self._read_evidence(output, adapter.output_schema)
            return BackendResult(
                proc.returncode,
                stdout[: self.output_limit],
                stderr[: self.output_limit],
                timed_out,
                evidence,
            )
        finally:
            try:
                shutil.rmtree(cwd)
            except OSError as cleanup:
                active = __import__("sys").exc_info()[1]
                if active is not None:
                    active.add_note(f"backend workspace cleanup failed: {cleanup}")
                else:
                    raise RuntimeError("backend workspace cleanup failed") from cleanup

    @staticmethod
    def _validate_command(command: list[str]) -> None:
        safe_values = {
            "--approval-mode": {"plan", "read-only"},
            "--sandbox": {"read-only"},
            "--permission-mode": {"plan"},
        }
        for flag, values in safe_values.items():
            if flag in command:
                index = command.index(flag)
                if index + 1 >= len(command) or command[index + 1] not in values:
                    raise PermissionError("backend observe contract is not read-only")

    def _read_evidence(self, output: Path, schema: str) -> dict[str, object]:
        if not output.is_file() or output.is_symlink():
            raise ValueError("backend did not produce a regular evidence.json")
        if output.stat().st_size > self.output_limit:
            raise ValueError("evidence.json exceeds output limit")
        try:
            value = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("malformed evidence.json") from exc
        if not isinstance(value, dict) or value.get("schema") != schema:
            raise ValueError("evidence.json schema mismatch")
        return value


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
            )
            if proc.poll() is None:
                proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def run_backend(adapter: BackendAdapter, packet: os.PathLike[str] | str, **kwargs: object) -> BackendResult:
    runner_args = {key: kwargs.pop(key) for key in ("output_limit", "timeout") if key in kwargs}
    return BackendRunner(**runner_args).run(adapter, packet, **kwargs)
