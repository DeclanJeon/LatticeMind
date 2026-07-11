"""Isolated non-shell backend execution."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

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
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate(proc)
                stdout, stderr = proc.communicate()

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
