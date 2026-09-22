"""Checkpointed, low-token continuation campaign for Local Quant Lab.

The local process runs tests and writes compact summaries.  It never edits
Pine sources, downloads data, places orders, or promotes a configuration.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from enqueue_dual_component_ablation import make_job
from lab_safety import atomic_write_json, update_queue
from quant_lab import LAB_ROOT, PROJECT_ROOT, run_queued_job


CAMPAIGN_ID = "REMAINING-CAUSAL-CAMPAIGN-01"
STATE = LAB_ROOT / "campaign_status.json"
SUMMARY = LAB_ROOT / "reports" / "campaigns" / CAMPAIGN_ID / "stage_summary.csv"
REGISTRY = LAB_ROOT / "reports" / "campaigns" / CAMPAIGN_ID / "candidate_registry.json"
SYMBOLS = ["WCTUSDT", "DUSKUSDT", "TAOUSDT"]


def _locked(job: dict, stage: str, variant: str) -> dict:
    job["categorical"] = {"exit_family": ["NONE"], "er": ["v1"], "winrate_state": ["OFF"]}
    job.setdefault("parameter_grid", {})["er_persist_bars"] = [1]
    job.setdefault("gate_parameter_grid", {})["external_gate_closes_position"] = [False]
    job["auto_download_missing"] = False
    job["engine_status"] = "RESEARCH_APPROXIMATION"
    job["production_eligible"] = False
    job["campaign"] = {
        "id": CAMPAIGN_ID, "stage": stage, "variant": variant,
        "winrate_state": "OFF", "er_persist_bars": 1,
        "lookahead": "BLOCKED", "pine_parity": "PENDING",
    }
    return job


def _hl_job(pivot: int) -> dict:
    job = make_job("BASE", pmax=False, core=False)
    job["id"] = f"RC01-S11-HL-G6-P{pivot}"
    job["description"] = f"HL→G6 entry-only cross-market pivot {pivot}/{pivot}"
    job["symbols"] = list(SYMBOLS)
    job["planned_runs"] = len(SYMBOLS)
    job["max_runs"] = len(SYMBOLS)
    job["gate_parameter_grid"].update({
        "strategy_master_enabled": [True], "ext1_enabled": [True],
        "ext1_source": ["MASTER_GATE"], "ext2_enabled": [False], "ext_mode": ["AND"],
        "g6_enabled": [True], "g6_source": ["HL"], "g6_mode": ["Pozitif"],
        "hl_left": [pivot], "hl_right": [pivot],
        "hl_mtf_minutes": [60], "hl_htf_minutes": [240],
        "hl_activation": ["HTF HL"], "hl_confirmation": ["Moderate"],
        "hl_close_source": ["HTF"], "hl_close_mode": ["Any Next Signal"],
    })
    return _locked(job, "S11_HL_CROSS_MARKET", f"HL_G6_P{pivot}")


COMPONENTS = {
    "CORE": {"core_enabled": True, "master_tf_minutes": 240},
    "MOST": {"most_enabled": True, "g1_tf_minutes": 240},
    "MOST2": {"g5_enabled": True, "g5_tf_minutes": 60},
    "TILLSON": {"tillson_enabled": True, "g2_tf_minutes": 60},
    "BOLLINGER": {"g3_enabled": True, "master_tf_minutes": 240},
    "MOMENTUM": {"g4_enabled": True, "master_tf_minutes": 240},
    "PMAX": {"pmax_enabled": True, "pmax_tf_minutes": 240},
}


def _component_job(name: str, changes: dict) -> dict:
    job = make_job("BASE", pmax=False, core=False)
    job["id"] = f"RC01-S12-{name}"
    job["description"] = f"BTC single Master component entry-only screen: {name}"
    job["gate_parameter_grid"].update({
        "strategy_master_enabled": [True], "ext1_enabled": [True],
        "ext1_source": ["MASTER_GATE"], "ext2_enabled": [False], "ext_mode": ["AND"],
        "g6_enabled": [False], **{key: [value] for key, value in changes.items()},
    })
    return _locked(job, "S12_MASTER_COMPONENT_SCREEN", name)


def jobs() -> list[dict]:
    return [_hl_job(5), _hl_job(8), *[_component_job(name, changes) for name, changes in COMPONENTS.items()]]


def validate_plan(planned: list[dict]) -> None:
    ids = [job["id"] for job in planned]
    if len(ids) != len(set(ids)):
        raise ValueError("Campaign job ids are not unique")
    for job in planned:
        if job["categorical"]["winrate_state"] != ["OFF"]:
            raise ValueError(f"{job['id']}: Winrate must stay OFF")
        if job["parameter_grid"]["er_persist_bars"] != [1]:
            raise ValueError(f"{job['id']}: ER persistence must stay 1")
        if job["gate_parameter_grid"]["external_gate_closes_position"] != [False]:
            raise ValueError(f"{job['id']}: campaign gates are entry-only")
        if job.get("auto_download_missing"):
            raise ValueError(f"{job['id']}: campaign cannot download data")


def enqueue(planned: list[dict]) -> None:
    ids = {job["id"] for job in planned}
    def mutate(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        existing = {row.get("id"): row for row in stored}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        for job in planned:
            prior = existing.get(job["id"])
            stored.append(prior if prior and prior.get("status") == "COMPLETE" else job)
    update_queue(LAB_ROOT / "jobs.json", mutate)


def compact_summary(planned: list[dict]) -> pd.DataFrame:
    queue = json.loads((LAB_ROOT / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    by_id = {row["id"]: row for row in queue}
    rows: list[dict] = []
    for planned_job in planned:
        job = by_id[planned_job["id"]]
        if job.get("status") != "COMPLETE":
            rows.append({"job_id": job["id"], "stage": planned_job["campaign"]["stage"],
                         "variant": planned_job["campaign"]["variant"], "status": job.get("status"),
                         "error": job.get("error")})
            continue
        result = pd.read_csv(Path(job["output_dir"]) / "job_results.csv")
        for item in result.to_dict("records"):
            rows.append({
                "job_id": job["id"], "stage": planned_job["campaign"]["stage"],
                "variant": planned_job["campaign"]["variant"], "symbol": item.get("symbol"),
                "status": item.get("status"), "coverage": item.get("coverage"),
                "trades": item.get("closed_trades"), "win_rate_pct": item.get("win_rate_pct"),
                "net_pnl_usdt": item.get("closed_net_profit_usdt"), "profit_factor": item.get("profit_factor"),
                "max_drawdown_pct": item.get("max_drawdown_pct_initial"),
                "gate_open_share": item.get("gate_open_share"),
                "pine_parity": item.get("gate_parity_status", "PINE_PARITY_PENDING"),
            })
    return pd.DataFrame(rows)


def candidate_registry(summary: pd.DataFrame) -> dict:
    complete = summary.loc[summary["status"].eq("COMPLETE")].copy()
    complete["profit_factor"] = pd.to_numeric(complete["profit_factor"], errors="coerce")
    complete["net_pnl_usdt"] = pd.to_numeric(complete["net_pnl_usdt"], errors="coerce")
    complete["trades"] = pd.to_numeric(complete["trades"], errors="coerce")
    decisions = []
    for (stage, variant), group in complete.groupby(["stage", "variant"]):
        pass_rows = group.loc[(group["profit_factor"] > 1) & (group["net_pnl_usdt"] > 0) & (group["trades"] >= 30)]
        decisions.append({
            "stage": stage, "variant": variant, "markets": int(len(group)),
            "passing_markets": int(len(pass_rows)),
            "verdict": "RESEARCH_MORE" if len(pass_rows) >= max(1, (len(group) + 1) // 2) else "REJECT",
            "reason": "Mechanical screen only; robustness, costs, OOS and Pine parity remain required.",
        })
    return {
        "campaign_id": CAMPAIGN_ID, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_eligible": False, "pine_parity": "PENDING", "decisions": decisions,
    }


def run() -> None:
    planned = jobs()
    validate_plan(planned)
    enqueue(planned)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    current_job = None
    completed = 0
    try:
        for index, job in enumerate(planned, start=1):
            current_job = job["id"]
            queue = json.loads((LAB_ROOT / "jobs.json").read_text(encoding="utf-8"))["jobs"]
            current = next(row for row in queue if row["id"] == job["id"])
            atomic_write_json(STATE, {
                "campaign_id": CAMPAIGN_ID, "state": "RUNNING", "current_job": current_job,
                "completed_jobs": completed, "total_jobs": len(planned),
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            })
            if current.get("status") != "COMPLETE":
                run_queued_job(LAB_ROOT / "config.json", LAB_ROOT / "jobs.json",
                               PROJECT_ROOT / "research" / "data" / "raw", current_job)
            completed = index
        summary = compact_summary(planned)
        SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(SUMMARY, index=False)
        registry = candidate_registry(summary)
        atomic_write_json(REGISTRY, registry)
        atomic_write_json(STATE, {
            "campaign_id": CAMPAIGN_ID, "state": "COMPLETE", "current_job": None,
            "completed_jobs": len(planned), "total_jobs": len(planned),
            "summary": str(SUMMARY), "registry": str(REGISTRY),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        atomic_write_json(STATE, {
            "campaign_id": CAMPAIGN_ID, "state": "FAILED", "current_job": current_job,
            "completed_jobs": completed, "total_jobs": len(planned), "error": str(exc),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"plan", "run"}:
        raise SystemExit("usage: remaining_campaign.py {plan|run}")
    planned_jobs = jobs()
    validate_plan(planned_jobs)
    if sys.argv[1] == "plan":
        print(json.dumps({"campaign_id": CAMPAIGN_ID, "jobs": [job["id"] for job in planned_jobs]}, indent=2))
    else:
        run()
