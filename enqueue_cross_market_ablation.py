"""Queue a small three-market replication of the S2 component ablations."""
from __future__ import annotations

import copy

from enqueue_dual_component_ablation import make_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


SYMBOLS = ["WCTUSDT", "DUSKUSDT", "TAOUSDT"]
VARIANTS = {
    "BASE": (True, True),
    "NO-PMAX": (False, True),
    "NO-CORE": (True, False),
}


def main() -> None:
    jobs: list[dict] = []
    for variant, (pmax, core) in VARIANTS.items():
        job = copy.deepcopy(make_job(variant, pmax=pmax, core=core))
        job["id"] = f"AUTO-XMKT-S4-{variant}"
        job["symbols"] = list(SYMBOLS)
        job["start"] = "2026-01-01"
        job["end"] = "2026-09-01"
        job["description"] = f"Üç aktif coin çapraz kontrolü: {variant}; Winrate OFF, ER persistence=1"
        job["planned_runs"] = len(SYMBOLS)
        job["max_runs"] = len(SYMBOLS)
        job["experiment"].update({
            "id": "AUTO-XMKT-S4",
            "stage": "S4_CROSS_MARKET_REPLICATION",
            "variant": variant,
            "symbols": list(SYMBOLS),
            "risk": "Üç piyasalı küçük örneklem; Pine parity ve bağımsız OOS beklemede.",
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
