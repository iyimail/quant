"""Queue one bounded, reproducible local baseline without touching Pine.

This is deliberately a single-run smoke test for the job engine.  It freezes
Winrate OFF and ER persistence at one bar, and uses a causal PMAX Master Gate
path on the 30-minute BTC archive.  It is research evidence only.
"""
from __future__ import annotations

import copy
import argparse
from datetime import datetime, timezone
from pathlib import Path

from lab_safety import update_queue
from quant_lab import LAB_ROOT, load_config


BASE_JOB_ID = "AUTO-BTC-S0-ER1-WROFF-PMAX"


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue one bounded causal local research job")
    parser.add_argument("--pmax", choices=("on", "off"), default="on")
    args = parser.parse_args()
    pmax_enabled = args.pmax == "on"
    job_id = BASE_JOB_ID if pmax_enabled else "AUTO-BTC-S1-ABLATE-PMAX"
    queue_path = LAB_ROOT / "jobs.json"
    config = copy.deepcopy(load_config(LAB_ROOT / "config.json"))
    config["interval"] = "30m"
    config.setdefault("strategy", {})["er_persist_bars"] = 1
    job = {
        "id": job_id,
        "description": (
            "Otomatik S0 smoke test: BTC 30m, Winrate OFF, ER persistence=1, PMAX→Master→EXT1"
            if pmax_enabled else
            "Otomatik S1 ablation: BTC 30m, Winrate OFF, ER persistence=1, PMAX kapalı; diğerleri S0 ile aynı"
        ),
        "status": "READY",
        "symbols": ["BTCUSDT"],
        "start": "2026-01-01",
        "end": "2026-09-01",
        "categorical": {"exit_family": ["NONE"], "er": ["v1"], "winrate_state": ["OFF"]},
        "parameter_grid": {
            "initial_capital": [1000.0], "cash_per_order": [300.0],
            "commission_pct_per_order": [0.08], "stop_loss_pct": [2.3],
            "trailing_activation_pct": [2.2], "trailing_distance_pct": [0.0],
            "er_length": [9], "er_threshold": [0.17], "er_persist_bars": [1],
        },
        "gate_parameter_grid": {
            "routing_mode": ["EXPLICIT"], "strategy_master_enabled": [True],
            "pmax_enabled": [pmax_enabled], "pmax_tf_minutes": [240],
            "g6_enabled": [False], "ext1_enabled": [True],
            "ext1_source": ["MASTER_GATE"], "ext2_enabled": [False],
            "ext2_source": ["close"], "ext_mode": ["AND"],
        },
        "max_runs": 1,
        "planned_runs": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine_status": "RESEARCH_APPROXIMATION",
        "production_eligible": False,
        "config_snapshot": config,
        "auto_download_missing": False,
        "experiment": {
            "id": "AUTO-BTC-S0" if pmax_enabled else "AUTO-BTC-S1",
            "stage": "S0_SMOKE_TEST" if pmax_enabled else "S1_ABLATION",
            "purpose": "ENGINE_AND_CAUSAL_GATE_PATH_VALIDATION" if pmax_enabled else "ONE_FACTOR_COMPONENT_ABLATION",
            "winrate_state": "OFF", "er_persist_bars": 1,
            "risk": "Local research approximation; no TradingView parity or production conclusion.",
        },
    }

    def add(queue: dict) -> None:
        jobs = queue.setdefault("jobs", [])
        if any(row.get("id") == job_id and row.get("status") in {"READY", "RUNNING"} for row in jobs):
            return
        jobs[:] = [row for row in jobs if row.get("id") != job_id]
        jobs.append(job)

    update_queue(queue_path, add)
    print(job_id)


if __name__ == "__main__":
    main()
