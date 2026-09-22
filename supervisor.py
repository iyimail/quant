"""Credit-free local queue supervisor for Local Quant Lab."""

from __future__ import annotations

import argparse
import json
import time
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

from quant_lab import LAB_ROOT, PROJECT_ROOT
from lab_safety import file_lock, atomic_write_json


def ready_job(queue_path: Path) -> str | None:
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    for job in queue.get("jobs", []):
        if job.get("status") == "READY":
            return str(job["id"])
    return None


def write_status(path: Path, state: str, job_id: str | None = None, error: str | None = None) -> None:
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "job_id": job_id,
        "error": error,
    }
    atomic_write_json(path, payload)


def supervise() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Bir READY işi çalıştırıp kapan")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    queue_path = LAB_ROOT / "jobs.json"
    status_path = LAB_ROOT / "supervisor_status.json"
    config_path = LAB_ROOT / "config.json"
    data_root = PROJECT_ROOT / "research" / "data" / "raw"
    stop_path = LAB_ROOT / "supervisor_stop.json"
    atomic_write_json(stop_path, {"stop": False})
    while True:
        if json.loads(stop_path.read_text(encoding="utf-8")).get("stop"):
            write_status(status_path, "STOPPED")
            return
        job_id = ready_job(queue_path)
        if job_id is None:
            write_status(status_path, "WAITING_FOR_READY_JOB")
            if args.once:
                return
            for _ in range(max(10, args.poll_seconds)):
                if json.loads(stop_path.read_text(encoding="utf-8")).get("stop"):
                    break
                time.sleep(1)
            continue
        write_status(status_path, "RUNNING", job_id)
        try:
            # A fresh interpreter per job prevents a long-lived supervisor from
            # executing old imported engine code while hashing newer disk files.
            subprocess.run([sys.executable, "-X", "utf8", "-u", str(LAB_ROOT / "quant_lab.py"), "job", "--config", str(config_path), "--queue", str(queue_path), "--data-root", str(data_root), "--job-id", job_id],
                           check=True, env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            report = next(row.get("report") for row in queue["jobs"] if row["id"] == job_id)
            write_status(status_path, "COMPLETED", job_id)
            print(f"Tamamlandı: {job_id} -> {report}", flush=True)
        except Exception as exc:
            write_status(status_path, "FAILED", job_id, str(exc))
            print(f"Başarısız: {job_id} -> {exc}", flush=True)
            time.sleep(2)
        if args.once:
            return


def main():
    with file_lock(LAB_ROOT / "supervisor.lock", timeout=0):
        supervise()


if __name__ == "__main__":
    main()
