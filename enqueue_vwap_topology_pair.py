"""Queue direct-VWAP versus VWAP-through-G6 topology comparison."""
from __future__ import annotations

import copy

from enqueue_dual_component_ablation import make_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


def topology_job(kind: str) -> dict:
    direct = kind == "DIRECT"
    job = copy.deepcopy(make_job("BASE", pmax=False, core=False))
    job["id"] = f"AUTO-BTC-S5-VWAP-{kind}"
    job["description"] = f"BTC VWAP topology: {kind}; Winrate OFF, ER persistence=1"
    job["gate_parameter_grid"].update({
        # In source/master.pine this toggle is the permission master for the
        # whole external-gate entry chain.  Direct EXT still requires it ON.
        "strategy_master_enabled": [True],
        "vwap_enabled": [True],
        "vwap_use_daily": [True],
        "vwap_use_weekly": [True],
        "vwap_mode": ["D+W Confirm"],
        "vwap_persist_bars": [3],
        "g6_enabled": [not direct],
        "g6_source": ["VWAP"],
        "g6_mode": ["Pozitif"],
        "ext1_source": ["VWAP" if direct else "MASTER_GATE"],
    })
    job["experiment"].update({
        "id": "AUTO-BTC-S5-VWAP-TOPOLOGY",
        "stage": "S5_ROUTING_COMPARISON",
        "variant": kind,
        "risk": "Yerel VWAP/route yaklaşımı; TradingView bar-parity beklemede.",
    })
    return job


def main() -> None:
    jobs = [topology_job("DIRECT"), topology_job("VIA-G6")]
    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        ids = {job["id"] for job in jobs}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        stored.extend(jobs)
    update_queue(LAB_ROOT / "jobs.json", add)
    print("\n".join(job["id"] for job in jobs))


if __name__ == "__main__":
    main()
