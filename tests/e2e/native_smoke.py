"""Real native scheduler smoke test; prerequisites are intentionally not mocked."""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(command, env):
    return subprocess.run(command, env=env, check=True, text=True, capture_output=True)


def write_uninstall_manifest(home: Path) -> None:
    import hashlib
    import json

    export = json.loads((home / "jobs.json").read_text(encoding="utf-8"))
    records = []
    for job in export["jobs"]:
        path = Path(job["path"])
        records.append({
            "output": str(path.resolve()),
            "type": "scheduler",
            "owner": job["owner"],
            "job_id": job["job_id"],
            "identity": job,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "marker": "owner=" + job["owner"] + " schema=job-definition-v1",
        })
    state = home / "data/latticemind"
    state.mkdir(parents=True, exist_ok=True)
    (state / "manifest-v1.json").write_text(
        json.dumps({"schema": "manifest-v1", "owned": records}, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main():
    if os.environ.get("LATTICEMIND_NATIVE_E2E") != "1":
        raise SystemExit("set LATTICEMIND_NATIVE_E2E=1 to run native smoke")
    system = platform.system()
    with tempfile.TemporaryDirectory(prefix="latticemind native smoke ") as td:
        home = Path(td)
        env = os.environ.copy()
        env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / "config"),
                   XDG_DATA_HOME=str(home / "data"), LATTICEMIND_JOB_EXPORT=str(home / "jobs.json"),
                   XDG_RUNTIME_DIR=str(home / "runtime"))
        (home / "runtime").mkdir()
        if system in {"Linux", "Darwin"}:
            # Install the real launcher and core so native services execute, not fixture files.
            local = home / ".local"
            (local / "bin").mkdir(parents=True)
            shutil.copy2(ROOT / "bin/latticemind-maintain", local / "bin/latticemind-maintain")
            shutil.copytree(ROOT / "latticemind_core", local / "latticemind_core")
            (local / "bin/latticemind-maintain").chmod(0o755)
        config = home / "config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "latticemind" ).mkdir(exist_ok=True)
        (config / "latticemind/config-v1.json").write_text(
            '{"schema":"config-v1","vault_path":"%s","profile":"observe",'
            '"enabled_jobs":[],"install_id":"native-smoke","install_version":"ci",'
            '"schema_versions":{"bootstrap":1,"config":1,"state":1}}\n' % (home / "vault")
        )
        (home / "vault").mkdir()
        if system == "Linux":
            run(["systemctl", "--user", "show-environment"], env)
            run(["bash", str(ROOT / "scripts/install-systemd.sh")], env)
            unit = home / "config/systemd/user/latticemind-freshness.timer"
            if "owner=latticemind-job-v1" not in unit.read_text():
                raise RuntimeError("systemd unit ownership marker missing")
            write_uninstall_manifest(home)
            run(["systemctl", "--user", "start", "latticemind-freshness.service"], env)
            run(["systemctl", "--user", "status", "latticemind-freshness.service", "--no-pager"], env)
            run(["systemctl", "--user", "status", "latticemind-freshness.timer", "--no-pager"], env)
            reports = home / "data/latticemind/reports"
            if not any(reports.glob("freshness-report-*.json")):
                raise RuntimeError("native systemd freshness report missing")
            run(["bash", str(ROOT / "uninstall.sh")], env)
            if (home / "config/systemd/user/latticemind-freshness.timer").exists():
                raise RuntimeError("native systemd timer remained registered")
        elif system == "Darwin":
            run(["launchctl", "print", f"gui/{os.getuid()}"], env)
            run(["bash", str(ROOT / "scripts/install-launchd.sh")], env)
            run(["launchctl", "print", f"gui/{os.getuid()}/com.latticemind.freshness"], env)
            run(["launchctl", "kickstart", f"gui/{os.getuid()}/com.latticemind.freshness"], env)
            run(["launchctl", "print", f"gui/{os.getuid()}/com.latticemind.freshness"], env)
            reports = home / "data/latticemind/reports"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not any(reports.glob("freshness-report-*.json")):
                time.sleep(0.25)
            if not any(reports.glob("freshness-report-*.json")):
                raise RuntimeError("native launchd freshness report missing")
            write_uninstall_manifest(home)
            run(["bash", str(ROOT / "uninstall.sh")], env)
            remaining = subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/com.latticemind.freshness"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if remaining.returncode == 0:
                raise RuntimeError("native launchd job remained registered")
        elif system == "Windows":
            raise RuntimeError("invoke Windows smoke through pwsh native lane")
        else:
            raise RuntimeError(f"unsupported platform {system}")
        evidence_root = os.environ.get("LATTICEMIND_EVIDENCE_DIR")
        if evidence_root and system in {"Linux", "Darwin"}:
            report = next(reports.glob("freshness-report-*.json"))
            destination = Path(evidence_root)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "native-evidence.json").write_text(
                json.dumps({
                    "schema": "native-evidence-v1",
                    "platform": system.lower(),
                    "scheduler": "systemd" if system == "Linux" else "launchd",
                    "freshness_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                    "cleanup": "passed",
                }, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
    print(f"native scheduler smoke passed on {system}")


if __name__ == "__main__":
    main()
