"""Queue a valid two-component Master Gate ablation set.

Both PMAX and Core are computed on 4h from a 30m decision clock.  Each
ablation leaves the other component enabled, so it remains a marginal test
rather than a structural all-closed control.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from lab_safety import update_queue
from quant_lab import LAB_ROOT, load_config


PREFIX = "AUTO-BTC-S2-PMAX-CORE"


def make_job(suffix: str, *, pmax: bool, core: bool) -> dict:
    config = copy.deepcopy(load_config(LAB_ROOT / "config.json"))
    config["interval"] = "30m"
    config.setdefault("strategy", {})["er_persist_bars"] = 1
    label = {"BASE": "iki bileşenli baseline", "NO-PMAX": "PMAX ablation", "NO-CORE": "CORE ablation"}[suffix]
    return {
        "id": f"{PREFIX}-{suffix}",
        "description": f"BTC 30m {label}: Winrate OFF, ER persistence=1; PMAX+CORE Master Gate yolu",
        "status": "READY", "symbols": ["BTCUSDT"], "start": "2026-01-01", "end": "2026-09-01",
        "categorical": {"exit_family": ["NONE"], "er": ["v1"], "winrate_state": ["OFF"]},
        "parameter_grid": {
            "initial_capital": [1000.0], "cash_per_order": [300.0], "commission_pct_per_order": [0.08],
            "stop_loss_pct": [2.3], "trailing_activation_pct": [2.2], "trailing_distance_pct": [0.0],
            "er_length": [9], "er_threshold": [0.17], "er_persist_bars": [1],
        },
        "gate_parameter_grid": {
            "routing_mode": ["EXPLICIT"], "strategy_master_enabled": [True],
            "pmax_enabled": [pmax], "pmax_tf_minutes": [240],
            "core_enabled": [core], "master_tf_minutes": [240],
            "g6_enabled": [False], "ext1_enabled": [True], "ext1_source": ["MASTER_GATE"],
            "ext2_enabled": [False], "ext2_source": ["close"], "ext_mode": ["AND"],
        },
        "max_runs": 1, "planned_runs": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine_status": "RESEARCH_APPROXIMATION", "production_eligible": False,
        "config_snapshot": config, "auto_download_missing": False,
        "experiment": {
            "id": PREFIX, "stage": "S2_TWO_COMPONENT_ABLATION", "variant": suffix,
            "purpose": "ONE_FACTOR_COMPONENT_ABLATION", "winrate_state": "OFF", "er_persist_bars": 1,
            "risk": "Local research approximation; Pine parity/OOS pending.",
        },
    }


def main() -> None:
    jobs = [make_job("BASE", pmax=True, core=True), make_job("NO-PMAX", pmax=False, core=True), make_job("NO-CORE", pmax=True, core=False)]
    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        ids = {job["id"] for job in jobs}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        stored.extend(jobs)
    update_queue(LAB_ROOT / "jobs.json", add)
    print("\n".join(job["id"] for job in jobs))


if __name__ == "__main__":
    main()
