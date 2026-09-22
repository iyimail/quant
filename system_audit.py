"""Bounded, one-factor system audit plans for Local Quant Lab."""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


CONNECTIONS = (
    ("G6_VWAP", {"routing_mode": "EXPLICIT", "g6_enabled": True, "g6_source": "VWAP", "ext1_enabled": False, "ext2_enabled": False}),
    ("G6_BIAS", {"routing_mode": "EXPLICIT", "g6_enabled": True, "g6_source": "BIAS", "ext1_enabled": False, "ext2_enabled": False}),
    ("G6_HL", {"routing_mode": "EXPLICIT", "g6_enabled": True, "g6_source": "HL", "ext1_enabled": False, "ext2_enabled": False}),
    ("EXT1_VWAP", {"routing_mode": "EXPLICIT", "ext1_enabled": True, "ext1_source": "VWAP", "ext2_enabled": False}),
    ("EXT1_BIAS", {"routing_mode": "EXPLICIT", "ext1_enabled": True, "ext1_source": "BIAS", "ext2_enabled": False}),
    ("EXT1_HL", {"routing_mode": "EXPLICIT", "ext1_enabled": True, "ext1_source": "HL", "ext2_enabled": False}),
    ("EXT2_VWAP", {"routing_mode": "EXPLICIT", "ext1_enabled": False, "ext2_enabled": True, "ext2_source": "VWAP"}),
    ("EXT2_BIAS", {"routing_mode": "EXPLICIT", "ext1_enabled": False, "ext2_enabled": True, "ext2_source": "BIAS"}),
    ("EXT2_HL", {"routing_mode": "EXPLICIT", "ext1_enabled": False, "ext2_enabled": True, "ext2_source": "HL"}),
)

# Master Gate's independently switchable local components.  They are tested
# one at a time against the same fixed baseline so a failed implementation is
# distinguishable from a multi-gate interaction.  Bias, HL and VWAP are
# exercised through the explicit external connection matrix above.
LOCAL_GATE_TOGGLES = (
    ("CORE_VECTOR", "core_enabled"),
    ("G1_MOST", "most_enabled"),
    ("G5_MOST2", "g5_enabled"),
    ("G2_TILLSON", "tillson_enabled"),
    ("G3_BOLLINGER", "g3_enabled"),
    ("G4_MOMENTUM", "g4_enabled"),
    ("G7_PRICE_LATCH", "g7_enabled"),
    ("MACRO_FILTER", "macro_enabled"),
    ("G8_PMAX", "pmax_enabled"),
)


def sampled(values: list[Any]) -> list[Any]:
    """Keep a range/list bounded to first, median, last values."""
    values = list(dict.fromkeys(values))
    if len(values) <= 3:
        return values
    return list(dict.fromkeys((values[0], values[len(values) // 2], values[-1])))


def _baseline(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    strategy = {key: sampled(values if isinstance(values, list) else [values])[0]
                for key, values in job.get("parameter_grid", {}).items()}
    gate = {key: sampled(values if isinstance(values, list) else [values])[0]
            for key, values in job.get("gate_parameter_grid", {}).items()}
    # Choose the first valid structural HTF rather than blindly retaining an
    # invalid first range point such as MTF=60 / HTF=60.
    chart = int(str(job.get("config_snapshot", {}).get("interval", "30m")).removesuffix("m"))
    mtf = int(gate.get("hl_mtf_minutes", 60))
    htf_values = job.get("gate_parameter_grid", {}).get("hl_htf_minutes", [240])
    htf_values = htf_values if isinstance(htf_values, list) else [htf_values]
    valid = [value for value in htf_values if chart < mtf < int(value)]
    if valid:
        gate["hl_htf_minutes"] = valid[0]
    return strategy, gate


def _job(base: dict[str, Any], audit_id: str, label: str, strategy: dict[str, Any], gate: dict[str, Any], *, axis=None) -> dict[str, Any]:
    candidate = copy.deepcopy(base)
    # Never inherit lifecycle/output state from a completed test used as a
    # configuration seed.  Reusing its output directory would make a new
    # audit look COMPLETE before it has executed.
    for key in ("output_dir", "report", "excel_report", "excel_report_error", "completed_runs", "skipped_runs",
                "error", "attempt", "attempts", "started_at", "started_at_utc", "finished_at",
                "completed_at_utc", "run_manifest", "worker_id"):
        candidate.pop(key, None)
    candidate["id"] = f"{audit_id}-{label}"[:80]
    candidate["status"] = "READY"
    candidate["description"] = f"Sistem denetimi: {label}; bir aralık en fazla üç örnek değer içerir"
    candidate["parameter_grid"] = {key: [value] if not isinstance(value, list) else value for key, value in strategy.items()}
    candidate["gate_parameter_grid"] = {key: [value] if not isinstance(value, list) else value for key, value in gate.items()}
    # This is a wiring diagnostic, not a portfolio test.  Pinning the first
    # selected symbol makes the advertised three-run bound literal even when
    # the UI currently has several coins selected.  Cross-market validation
    # remains a separate, later phase.
    candidate["symbols"] = list(candidate.get("symbols", []))[:1]
    candidate["categorical"] = {key: [sampled(values if isinstance(values, list) else [values])[0]]
                                for key, values in candidate.get("categorical", {}).items()}
    candidate["planned_runs"] = (len(candidate["symbols"])
                                 * math.prod(len(values) for values in candidate["categorical"].values())
                                 * math.prod(len(values) for values in candidate["parameter_grid"].values())
                                 * math.prod(len(values) for values in candidate["gate_parameter_grid"].values()))
    candidate["max_runs"] = 3
    candidate["system_audit"] = {"id": audit_id, "label": label, "axis": axis, "max_samples_per_range": 3,
                                "scope": "ONE_SYMBOL_ONE_CATEGORICAL_BASELINE"}
    return candidate


def build_system_audit_plan(base_job: dict[str, Any], audit_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strategy, gate = _baseline(base_job)
    jobs = [_job(base_job, audit_id, "BASELINE", strategy, gate)]
    skipped: list[dict[str, Any]] = []
    chart = int(str(base_job.get("config_snapshot", {}).get("interval", "30m")).removesuffix("m"))
    for scope, original, baseline in (("strategy", base_job.get("parameter_grid", {}), strategy), ("gate", base_job.get("gate_parameter_grid", {}), gate)):
        for key, raw in original.items():
            values = sampled(raw if isinstance(raw, list) else [raw])
            if len(values) < 2:
                continue
            valid_values = values
            if key == "hl_htf_minutes":
                mtf = int(baseline.get("hl_mtf_minutes", 60))
                valid_values = [value for value in values if chart < mtf < int(value)]
            if key == "hl_mtf_minutes":
                htf = int(baseline.get("hl_htf_minutes", 240))
                valid_values = [value for value in values if chart < int(value) < htf]
            if not valid_values:
                skipped.append({"field": key, "reason": "No valid Chart < MTF < HTF sample"})
                continue
            s, g = dict(strategy), dict(gate)
            (s if scope == "strategy" else g)[key] = valid_values
            jobs.append(_job(base_job, audit_id, f"{scope.upper()}-{key.upper()}", s, g, axis=(scope, key)))
    for label, override in CONNECTIONS:
        g = {**gate, **override}
        jobs.append(_job(base_job, audit_id, f"CONN-{label}", strategy, g, axis=("connection", label)))
    for label, key in LOCAL_GATE_TOGGLES:
        if key not in base_job.get("gate_parameter_grid", {}):
            skipped.append({"field": key, "reason": "Gate input is absent from this configuration"})
            continue
        # A local Master Gate component has no consumer merely by being
        # enabled.  Route Master Gate into EXT1 explicitly; otherwise this
        # case would execute the strategy while never resolving MASTER_GATE
        # and falsely look identical to the baseline.
        g = {**gate, "ext1_enabled": True, "ext1_source": "MASTER_GATE",
             "ext2_enabled": False, key: True}
        jobs.append(_job(base_job, audit_id, f"LOCAL-{label}", strategy, g, axis=("local_gate", key)))
    manifest = {
        "audit_id": audit_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "SYSTEM_WIRING_AND_PARAMETER_AUDIT", "job_ids": [job["id"] for job in jobs],
        "connection_cases": [name for name, _ in CONNECTIONS],
        "local_gate_cases": [name for name, key in LOCAL_GATE_TOGGLES if key in base_job.get("gate_parameter_grid", {})],
        "skipped_invalid_samples": skipped,
        "max_samples_per_range": 3,
        "verdict_definition": "PASS requires every planned job COMPLETE with at least one COMPLETE result and no skipped run.",
    }
    return jobs, manifest


def audit_summary(queue_path: Path, audit_id: str) -> dict[str, Any]:
    queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {"jobs": []}
    jobs = [job for job in queue.get("jobs", []) if job.get("system_audit", {}).get("id") == audit_id]
    records = []
    for job in jobs:
        path = Path(job.get("output_dir") or "") / "job_results.csv"
        rows = pd.read_csv(path) if path.exists() else pd.DataFrame()
        complete = rows.loc[rows.get("status", pd.Series(dtype=str)).eq("COMPLETE")] if len(rows) else rows
        axis = job["system_audit"].get("axis") or []
        records.append({
            "job_id": job["id"], "label": job["system_audit"]["label"], "axis": str(job["system_audit"].get("axis")),
            "job_status": job.get("status"), "planned_runs": job.get("planned_runs"),
            "complete_runs": int(len(complete)), "skipped_runs": int(job.get("skipped_runs", 0)),
            "net_pnl_usdt": float(complete.get("total_pnl_usdt", pd.Series(dtype=float)).sum()) if len(complete) else None,
            "profit_factor": float(complete.get("profit_factor", pd.Series(dtype=float)).replace([float("inf")], pd.NA).dropna().mean()) if len(complete) and complete.get("profit_factor", pd.Series(dtype=float)).notna().any() else None,
            "max_drawdown_usdt": float(complete.get("max_drawdown_usdt", pd.Series(dtype=float)).max()) if len(complete) else None,
            "sampled_values": (job["parameter_grid"].get(axis[1]) if len(axis) > 1 and axis[0] == "strategy" else
                               job["gate_parameter_grid"].get(axis[1]) if len(axis) > 1 and axis[0] == "gate" else None),
        })
    statuses = {record["job_status"] for record in records}
    if not records:
        verdict, reason = "WAITING", "Audit jobs not found"
    elif ("FAILED" in statuses
          or any(record["complete_runs"] == 0 and record["job_status"] == "COMPLETE" for record in records)
          or any(record["complete_runs"] > 3 for record in records)):
        verdict, reason = "FAIL", "A planned audit job failed, reused incompatible output, or produced no completed result"
    elif any(status in {"READY", "RUNNING", "INTERRUPTED"} for status in statuses):
        verdict, reason = "WAITING", "Audit jobs are still running or queued"
    elif any(record["skipped_runs"] for record in records):
        verdict, reason = "CAUTION", "A completed audit job skipped one or more runs"
    else:
        verdict, reason = "PASS", "Every planned wiring and sampled parameter job completed"
    recommendations = []
    for record in records:
        if record["sampled_values"] and record["job_status"] == "COMPLETE":
            recommendations.append({"axis": record["axis"], "sampled_values": record["sampled_values"],
                                    "profit_factor": record["profit_factor"], "net_pnl_usdt": record["net_pnl_usdt"],
                                    "max_drawdown_usdt": record["max_drawdown_usdt"], "evidence": "ONE_FACTOR_RESEARCH_ONLY"})
    return {"audit_id": audit_id, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "audit_agent": {"name": "Local System Audit Agent", "verdict": verdict, "reason": reason},
            "jobs": records, "recommendations": recommendations}
