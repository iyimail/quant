"""Queue VWAP direct/G6 routing with entry permission but no exit authority."""
from __future__ import annotations

import copy

from enqueue_vwap_topology_pair import topology_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


def main() -> None:
    jobs: list[dict] = []
    for kind in ("DIRECT", "VIA-G6"):
        job = copy.deepcopy(topology_job(kind))
        job["id"] = f"AUTO-BTC-S6-VWAP-{kind}-ENTRY-ONLY"
        job["description"] = f"BTC VWAP {kind}: yalnız giriş izni, external exit yetkisi kapalı"
        job["gate_parameter_grid"]["external_gate_closes_position"] = [False]
        job["experiment"].update({
            "id": "AUTO-BTC-S6-VWAP-ENTRY-ONLY",
            "stage": "S6_ENTRY_EXIT_AUTHORITY_SEPARATION",
            "variant": kind,
            "risk": "Yerel VWAP modeli; Pine parity ve gerçek OOS beklemede.",
        })
        jobs.append(job)

    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        ids = {job["id"] for job in jobs}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        stored.extend(jobs)

    update_queue(LAB_ROOT / "jobs.json", add)
    print("\n".join(job["id"] for job in jobs))


if __name__ == "__main__":
    main()
