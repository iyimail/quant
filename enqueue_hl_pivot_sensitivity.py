"""Queue one-at-a-time HL pivot sensitivity checks on the BTC G6 route."""
from __future__ import annotations

import argparse
import copy

from enqueue_bias_hl_entry_only import base_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pivot", type=int, choices=(3, 8), required=True)
    args = parser.parse_args()
    pivot = args.pivot
    job = copy.deepcopy(base_job(f"HL-VIA-G6-PIVOT-{pivot}"))
    job["id"] = f"AUTO-BTC-S10-HL-G6-PIVOT{pivot}"
    job["description"] = f"BTC HL→G6 entry-only pivot sensitivity: left/right={pivot}"
    job["gate_parameter_grid"].update({
        "ext1_source": ["MASTER_GATE"], "g6_enabled": [True], "g6_source": ["HL"],
        "hl_left": [pivot], "hl_right": [pivot],
        "hl_mtf_minutes": [60], "hl_htf_minutes": [240],
        "hl_activation": ["HTF HL"], "hl_confirmation": ["Moderate"],
        "hl_close_source": ["HTF"], "hl_close_mode": ["Any Next Signal"],
    })
    job["experiment"].update({
        "id": "AUTO-BTC-S10-HL-PIVOT-SENSITIVITY",
        "stage": "S10_ONE_FACTOR_SENSITIVITY",
        "variant": f"PIVOT-{pivot}",
        "risk": "One-factor local sensitivity; Pine parity and OOS pending.",
    })

    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        stored[:] = [row for row in stored if row.get("id") != job["id"]]
        stored.append(job)

    update_queue(LAB_ROOT / "jobs.json", add)
    print(job["id"])


if __name__ == "__main__":
    main()
