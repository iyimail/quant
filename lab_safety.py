"""Shared queue transactions, model identity and input validation (no Pine execution)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, timeout: float = 5):
    """OS lock: automatically released after crash; persistent lock file is harmless."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Motor veya kuyruk başka bir işlem tarafından kullanılıyor")
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_queue(path: Path, mutate):
    with file_lock(path.with_suffix(".queue.lock")):
        queue = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": "local-quant-lab-job-queue-v1", "jobs": []}
        result = mutate(queue)
        atomic_write_json(path, queue)
        return result


def append_job(path: Path, job):
    def mutate(queue):
        if any(row["id"] == job["id"] for row in queue["jobs"]):
            raise ValueError(f"{job['id']} kimlikli iş zaten var")
        queue["jobs"].append(job)
    update_queue(path, mutate)


def append_jobs(path: Path, jobs):
    """Append a complete experiment plan as one queue transaction.

    A baseline and its ablations must never be half-added: otherwise an
    apparent comparison could silently lack one of the interventions.
    """
    jobs = list(jobs)

    def mutate(queue):
        existing = {row["id"] for row in queue["jobs"]}
        incoming = [job["id"] for job in jobs]
        duplicates = {job_id for job_id in incoming if incoming.count(job_id) > 1}
        collisions = existing.intersection(incoming)
        if duplicates:
            raise ValueError(f"Plan içinde yinelenen iş kimliği var: {', '.join(sorted(duplicates))}")
        if collisions:
            raise ValueError(f"Bu iş kimlikleri zaten kuyrukta var: {', '.join(sorted(collisions))}")
        queue["jobs"].extend(jobs)

    update_queue(path, mutate)


def recover_interrupted(queue_path: Path, worker_lock: Path):
    with file_lock(worker_lock, timeout=0):
        def mutate(queue):
            count = 0
            for row in queue["jobs"]:
                if row.get("status") == "RUNNING":
                    row["status"] = "INTERRUPTED"
                    row["error"] = "Motor artık çalışmıyor. Yeniden deneme yeni klasöre yazılır; eski çıktılar korunur."
                    count += 1
            return count
        return update_queue(queue_path, mutate)


INTEGER_FIELDS = {"er_length", "er_persist_bars", "winrate_window", "winrate_min_trades"}
POSITIVE_FIELDS = {"initial_capital", "cash_per_order", "stop_loss_pct", "trailing_activation_pct", "quantity_step_fallback", "tick_size_fallback"} | INTEGER_FIELDS
POSITIVE_FIELDS -= {"winrate_min_trades"}


def validate_parameters(values):
    for key, value in values.items():
        if key == "winrate_block_before_min":
            if not isinstance(value, bool):
                raise ValueError(f"{key}: true/false gerekli")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key}: sonlu bir sayı gerekli")
        if value < 0 or (key in POSITIVE_FIELDS and value == 0):
            raise ValueError(f"{key}: geçersiz negatif/sıfır değer")
        if key in INTEGER_FIELDS and int(value) != value:
            raise ValueError(f"{key}: tam sayı gerekli")
        if key == "er_length" and value < 2:
            raise ValueError("er_length: kaynak minimumu 2")
        if key == "er_persist_bars" and value > 50:
            raise ValueError("er_persist_bars: kaynak maksimumu 50")
        if key == "winrate_window" and not 2 <= value <= 2000:
            raise ValueError("winrate_window: kaynak aralığı 2..2000")
        if key == "winrate_min_trades" and value > 2000:
            raise ValueError("winrate_min_trades: kaynak maksimumu 2000")
        if key in {"er_threshold", "winrate_threshold"} and value > 1:
            raise ValueError(f"{key}: 0 ile 1 arasında olmalı")
        if key in {"stop_loss_pct", "commission_pct_per_order"} and value >= 100:
            raise ValueError(f"{key}: yüzde 100'den küçük olmalı")


def file_digest(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


NON_EXECUTION_FILES = {"gui.py", "test_categories.py", "result_seed.py", "system_audit.py", "audit_checkpoint.py"}


def _execution_files(files):
    """UI/result-organization changes must not invalidate a queued simulation."""
    return {
        path: digest for path, digest in files.items()
        if Path(path).name not in NON_EXECUTION_FILES
    }


def engine_identity(lab_root: Path):
    paths = [path for path in sorted(lab_root.glob("*.py")) if path.name not in NON_EXECUTION_FILES]
    paths += sorted((lab_root.parent / "research" / "scripts").glob("*.py"))
    paths += sorted((lab_root.parent / "sources").glob("*.pine"))
    hashes = {str(path.relative_to(lab_root.parent)): file_digest(path) for path in paths}
    return {"id": "multi-gate-master-python-v2", "sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), "files": hashes,
            "status": "RESEARCH_APPROXIMATION", "pine_execution": False}


def validate_engine(job, current):
    if job.get("pine_candidate"):
        raise ValueError("Seçilen Pine kodunun Python yürütücüsü doğrulanmadı. Bu kod yerine eski motor çalıştırılmayacak.")
    saved = job.get("engine_snapshot")
    # Older jobs include GUI hashes. Compare their executable subset so a
    # category or layout change does not invalidate an already-created test.
    if saved:
        saved_files = _execution_files(saved.get("files", {}))
        current_files = _execution_files(current.get("files", {}))
        changed = saved_files != current_files if (saved_files or current_files) else saved.get("sha256") != current.get("sha256")
        if changed:
            raise ValueError("Kuyruğa eklendikten sonra test motoru değişti. Güncel motorla yeni bir test oluşturun.")
