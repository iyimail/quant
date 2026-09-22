"""Commission stress campaign for cross-market survivor interactions."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from lab_safety import atomic_write_json, update_queue
from quant_lab import LAB_ROOT, PROJECT_ROOT, run_queued_job
from survivor_campaign import VARIANTS, make_variant


CAMPAIGN_ID = "SURVIVOR-COST-STRESS-03"
STATE = LAB_ROOT / "campaign_status_03.json"
OUT = LAB_ROOT / "reports" / "campaigns" / CAMPAIGN_ID
COSTS = [0.12, 0.16]


def make_stress_job(name: str, changes: dict) -> dict:
    job = make_variant(name, changes)
    job["id"] = f"SC03-{name}-COST"
    job["description"] = f"Cross-market commission stress: {name}, 0.12/0.16 percent per order"
    job["parameter_grid"]["commission_pct_per_order"] = list(COSTS)
    job["planned_runs"] = len(job["symbols"]) * len(COSTS)
    job["max_runs"] = job["planned_runs"]
    job["campaign"].update({"id": CAMPAIGN_ID, "stage": "S14_COMMISSION_STRESS"})
    return job


def jobs() -> list[dict]:
    return [make_stress_job(name, changes) for name, changes in VARIANTS.items()]


def validate(planned: list[dict]) -> None:
    if len(planned) != 6 or len({job["id"] for job in planned}) != 6:
        raise ValueError("Expected six unique cost-stress jobs")
    for job in planned:
        if job["parameter_grid"]["commission_pct_per_order"] != COSTS or job["planned_runs"] != 6:
            raise ValueError("Cost stress axis must be exactly 0.12/0.16 across three markets")
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
            overrides = json.loads(item["parameter_overrides"])
            rows.append({
                "job_id": job["id"], "variant": spec["campaign"]["variant"], "symbol": item.get("symbol"),
                "commission_pct_per_order": overrides["commission_pct_per_order"],
                "status": item.get("status"), "coverage": item.get("coverage"),
                "trades": item.get("closed_trades"), "net_pnl_usdt": item.get("closed_net_profit_usdt"),
                "profit_factor": item.get("profit_factor"), "max_drawdown_pct": item.get("max_drawdown_pct_initial"),
                "pine_parity": item.get("gate_parity_status"),
            })
    frame = pd.DataFrame(rows)
    numeric = frame.copy()
    for key in ("trades", "net_pnl_usdt", "profit_factor", "max_drawdown_pct", "commission_pct_per_order"):
        numeric[key] = pd.to_numeric(numeric[key], errors="coerce")
    decisions = []
    harsh = numeric.loc[numeric["commission_pct_per_order"].eq(max(COSTS))]
    for variant, group in harsh.groupby("variant"):
        passed = group.loc[(group["trades"] >= 30) & (group["net_pnl_usdt"] > 0) & (group["profit_factor"] > 1)]
        decisions.append({
            "variant": variant, "harsh_cost_pct_per_order": max(COSTS),
            "markets": int(len(group)), "passing_markets": int(len(passed)),
            "median_profit_factor": float(group["profit_factor"].median()),
            "worst_drawdown_pct": float(group["max_drawdown_pct"].max()),
            "verdict": "TIME_ROBUSTNESS" if len(passed) >= 2 else "REJECT",
        })
    return frame, {"campaign_id": CAMPAIGN_ID, "production_eligible": False,
                   "pine_parity": "PENDING", "decisions": decisions}


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
        raise SystemExit("usage: cost_stress_campaign.py {plan|run}")
    planned_jobs = jobs()
    validate(planned_jobs)
    if sys.argv[1] == "plan":
        print(json.dumps([{"id": job["id"], "runs": job["planned_runs"]} for job in planned_jobs], indent=2))
    else:
        run()
