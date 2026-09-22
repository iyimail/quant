"""Cross-market interaction campaign for PMAX, MOST2 and HL→G6 8/8."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from enqueue_dual_component_ablation import make_job
from lab_safety import atomic_write_json, update_queue
from quant_lab import LAB_ROOT, PROJECT_ROOT, run_queued_job


CAMPAIGN_ID = "SURVIVOR-INTERACTIONS-02"
STATE = LAB_ROOT / "campaign_status_02.json"
OUT = LAB_ROOT / "reports" / "campaigns" / CAMPAIGN_ID
SYMBOLS = ["WCTUSDT", "DUSKUSDT", "TAOUSDT"]
VARIANTS = {
    "PMAX": {"pmax_enabled": True, "pmax_tf_minutes": 240},
    "MOST2": {"g5_enabled": True, "g5_tf_minutes": 60},
    "PMAX_MOST2": {"pmax_enabled": True, "pmax_tf_minutes": 240, "g5_enabled": True, "g5_tf_minutes": 60},
    "PMAX_HL8": {"pmax_enabled": True, "pmax_tf_minutes": 240, "g6_enabled": True, "g6_source": "HL"},
    "MOST2_HL8": {"g5_enabled": True, "g5_tf_minutes": 60, "g6_enabled": True, "g6_source": "HL"},
    "PMAX_MOST2_HL8": {"pmax_enabled": True, "pmax_tf_minutes": 240, "g5_enabled": True, "g5_tf_minutes": 60,
                         "g6_enabled": True, "g6_source": "HL"},
}


def make_variant(name: str, changes: dict) -> dict:
    job = make_job("BASE", pmax=False, core=False)
    job["id"] = f"SC02-{name}"
    job["description"] = f"Cross-market survivor interaction: {name}"
    job["symbols"] = list(SYMBOLS)
    job["planned_runs"] = len(SYMBOLS)
    job["max_runs"] = len(SYMBOLS)
    job["categorical"] = {"exit_family": ["NONE"], "er": ["v1"], "winrate_state": ["OFF"]}
    job["parameter_grid"]["er_persist_bars"] = [1]
    job["gate_parameter_grid"].update({
        "strategy_master_enabled": [True], "external_gate_closes_position": [False],
        "ext1_enabled": [True], "ext1_source": ["MASTER_GATE"],
        "ext2_enabled": [False], "ext_mode": ["AND"],
        "hl_left": [8], "hl_right": [8], "hl_mtf_minutes": [60], "hl_htf_minutes": [240],
        "hl_activation": ["HTF HL"], "hl_confirmation": ["Moderate"],
        **{key: [value] for key, value in changes.items()},
    })
    job["auto_download_missing"] = False
    job["engine_status"] = "RESEARCH_APPROXIMATION"
    job["production_eligible"] = False
    job["campaign"] = {
        "id": CAMPAIGN_ID, "stage": "S13_SURVIVOR_INTERACTIONS", "variant": name,
        "winrate_state": "OFF", "er_persist_bars": 1, "exit_authority": "ENTRY_ONLY",
        "pine_parity": "PENDING",
    }
    return job


def jobs() -> list[dict]:
    return [make_variant(name, changes) for name, changes in VARIANTS.items()]


def validate(planned: list[dict]) -> None:
    if len(planned) != 6 or len({job["id"] for job in planned}) != 6:
        raise ValueError("Expected six unique survivor jobs")
    for job in planned:
        if job["categorical"]["winrate_state"] != ["OFF"] or job["parameter_grid"]["er_persist_bars"] != [1]:
            raise ValueError("Campaign lock violation")
        if job["gate_parameter_grid"]["external_gate_closes_position"] != [False] or job.get("auto_download_missing"):
            raise ValueError("Entry-only/no-download lock violation")


def enqueue(planned: list[dict]) -> None:
    ids = {job["id"] for job in planned}
    def mutate(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        existing = {row.get("id"): row for row in stored}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        stored.extend(existing[job["id"]] if existing.get(job["id"], {}).get("status") == "COMPLETE" else job for job in planned)
    update_queue(LAB_ROOT / "jobs.json", mutate)


def summarize(planned: list[dict]) -> tuple[pd.DataFrame, dict]:
    queue = json.loads((LAB_ROOT / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    by_id = {row["id"]: row for row in queue}
    rows = []
    for spec in planned:
        job = by_id[spec["id"]]
        result = pd.read_csv(Path(job["output_dir"]) / "job_results.csv")
        for item in result.to_dict("records"):
            rows.append({
                "job_id": job["id"], "variant": spec["campaign"]["variant"], "symbol": item.get("symbol"),
                "status": item.get("status"), "coverage": item.get("coverage"),
                "trades": item.get("closed_trades"), "net_pnl_usdt": item.get("closed_net_profit_usdt"),
                "profit_factor": item.get("profit_factor"), "max_drawdown_pct": item.get("max_drawdown_pct_initial"),
                "gate_open_share": item.get("gate_open_share"), "pine_parity": item.get("gate_parity_status"),
            })
    frame = pd.DataFrame(rows)
    numeric = frame.copy()
    for key in ("trades", "net_pnl_usdt", "profit_factor", "max_drawdown_pct"):
        numeric[key] = pd.to_numeric(numeric[key], errors="coerce")
    decisions = []
    for variant, group in numeric.groupby("variant"):
        passed = group.loc[(group["trades"] >= 30) & (group["net_pnl_usdt"] > 0) & (group["profit_factor"] > 1)]
        decisions.append({
            "variant": variant, "markets": int(len(group)), "passing_markets": int(len(passed)),
            "median_profit_factor": float(group["profit_factor"].median()),
            "worst_drawdown_pct": float(group["max_drawdown_pct"].max()),
            "verdict": "COST_STRESS" if len(passed) >= 2 else "REJECT",
        })
    registry = {"campaign_id": CAMPAIGN_ID, "production_eligible": False, "pine_parity": "PENDING", "decisions": decisions}
    return frame, registry


def run() -> None:
    planned = jobs()
    validate(planned)
    enqueue(planned)
    completed = 0
    current = None
    try:
        for spec in planned:
            current = spec["id"]
            queue = json.loads((LAB_ROOT / "jobs.json").read_text(encoding="utf-8"))["jobs"]
            row = next(item for item in queue if item["id"] == current)
            atomic_write_json(STATE, {"campaign_id": CAMPAIGN_ID, "state": "RUNNING", "current_job": current,
                                      "completed_jobs": completed, "total_jobs": len(planned),
                                      "updated_at_utc": datetime.now(timezone.utc).isoformat()})
            if row.get("status") != "COMPLETE":
                run_queued_job(LAB_ROOT / "config.json", LAB_ROOT / "jobs.json", PROJECT_ROOT / "research" / "data" / "raw", current)
            completed += 1
        frame, registry = summarize(planned)
        OUT.mkdir(parents=True, exist_ok=True)
        frame.to_csv(OUT / "stage_summary.csv", index=False)
        atomic_write_json(OUT / "candidate_registry.json", registry)
        atomic_write_json(STATE, {"campaign_id": CAMPAIGN_ID, "state": "COMPLETE", "current_job": None,
                                  "completed_jobs": completed, "total_jobs": len(planned),
                                  "summary": str(OUT / "stage_summary.csv"), "registry": str(OUT / "candidate_registry.json"),
                                  "updated_at_utc": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        atomic_write_json(STATE, {"campaign_id": CAMPAIGN_ID, "state": "FAILED", "current_job": current,
                                  "completed_jobs": completed, "total_jobs": len(planned), "error": str(exc),
                                  "updated_at_utc": datetime.now(timezone.utc).isoformat()})
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"plan", "run"}:
        raise SystemExit("usage: survivor_campaign.py {plan|run}")
    planned_jobs = jobs()
    validate(planned_jobs)
    if sys.argv[1] == "plan":
        print(json.dumps([job["id"] for job in planned_jobs], indent=2))
    else:
        run()
