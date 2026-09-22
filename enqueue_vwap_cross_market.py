"""Queue a three-market replication of VWAP entry-only routing."""
from __future__ import annotations

import copy

from enqueue_vwap_topology_pair import topology_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


SYMBOLS = ["WCTUSDT", "DUSKUSDT", "TAOUSDT"]


def main() -> None:
    jobs: list[dict] = []
    for kind in ("DIRECT", "VIA-G6"):
        job = copy.deepcopy(topology_job(kind))
        job["id"] = f"AUTO-XMKT-S7-VWAP-{kind}-ENTRY-ONLY"
        job["symbols"] = list(SYMBOLS)
        job["planned_runs"] = len(SYMBOLS)
        job["max_runs"] = len(SYMBOLS)
        job["description"] = f"Üç aktif coin: VWAP {kind}, yalnız giriş izni"
        job["gate_parameter_grid"]["external_gate_closes_position"] = [False]
        job["experiment"].update({
            "id": "AUTO-XMKT-S7-VWAP-ENTRY-ONLY",
            "stage": "S7_CROSS_MARKET_ROUTING_REPLICATION",
            "variant": kind,
            "symbols": list(SYMBOLS),
            "risk": "Üç piyasalı küçük örneklem; Pine parity ve gerçek OOS beklemede.",
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
