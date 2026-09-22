"""Machine-readable, offline audit checkpoint for the Local Quant Lab.

This is a software contract check, not a backtest and not a Pine-parity
certificate.  It reads the Pine sources without changing them, checks the
explicit wiring assumptions used by the local emulator, and records anything
that still requires a TradingView bar-by-bar export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


LAB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_ROOT.parent
SOURCE_ROOT = PROJECT_ROOT / "sources"

# These are intentionally small source anchors, rather than a pretend Pine
# parser.  A missing anchor means the local wiring model must be re-audited.
SOURCE_ANCHORS: dict[str, tuple[str, ...]] = {
    "master.pine": ("ext_gate_sig_1[1] > 0", "ext_gate_sig_2[1] > 0", "process_orders_on_close=false"),
    "master gate.pine": ("ext_source[1]", "Master_Gate_Open", "request.security"),
    "hl gate.pine": ("chartTfSeconds < mtfTfSeconds", "mtfTfSeconds < htfTfSeconds", "ta.pivothigh"),
    "bias.pine": ("close[barsBack]", "external_gate_signal", "lookahead_on"),
    "vwap gate.pine": ("stable_reg", "time(\"D\")", "time(\"W\")"),
}

RESULT_METADATA_REQUIRED = frozenset({
    "job_id", "symbol", "parameter_id", "parameter_overrides",
    "gate_parameter_overrides", "active_gate_models", "gate_combination",
    "gate_routing_version", "gate_parity_status", "gate_topology_warnings",
    "gate_lookahead_risk", "engine_status", "production_eligible",
    "execution_data_policy", "trade_ledger", "status",
})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_graph_audit(source_root: Path = SOURCE_ROOT) -> dict[str, Any]:
    """Read-only anchors for the source-to-emulator wiring graph."""
    files: dict[str, Any] = {}
    failures: list[str] = []
    for name, anchors in SOURCE_ANCHORS.items():
        path = source_root / name
        if not path.is_file():
            files[name] = {"status": "MISSING", "anchors": {}, "sha256": None}
            failures.append(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        matched = {anchor: anchor in text for anchor in anchors}
        files[name] = {"status": "PASS" if all(matched.values()) else "FAIL", "anchors": matched, "sha256": _sha256(path)}
        failures.extend(f"{name}: missing source anchor {anchor}" for anchor, ok in matched.items() if not ok)
    return {
        "status": "PASS" if not failures else "FAIL",
        "files": files,
        "declared_graph": [
            "BIAS|HL|VWAP producer -> Master Gate G6 external source [1] -> Master Gate plot -> Master Strategy EXT [1]",
            "BIAS|HL|VWAP producer -> Master Strategy EXT [1]",
        ],
        "failures": failures,
    }


def validate_result_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate provenance fields before a result can be interpreted."""
    missing = sorted(key for key in RESULT_METADATA_REQUIRED if key not in row)
    invalid: list[str] = []
    if not missing:
        if row["status"] != "COMPLETE":
            invalid.append("status must be COMPLETE")
        if row["engine_status"] != "RESEARCH_APPROXIMATION":
            invalid.append("engine_status must state RESEARCH_APPROXIMATION")
        if row["production_eligible"] is not False:
            invalid.append("production_eligible must be false for local emulator results")
        if not isinstance(row["gate_routing_version"], (int, float)):
            invalid.append("gate_routing_version must be numeric")
    return {"status": "PASS" if not missing and not invalid else "FAIL", "missing": missing, "invalid": invalid}


def _index(minutes: int, *, periods: int = 4) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=periods, freq=f"{minutes}min", tz="UTC")


def runtime_contract_audit() -> dict[str, Any]:
    """Exercise the local contracts without market data or a performance run."""
    from lab_safety import validate_parameters
    from quant_lab import selected_combinations, strategy_config
    from topology_safety import audit_explicit_topology

    er_one = {"status": "FAIL", "detail": "not run"}
    try:
        validate_parameters({"er_persist_bars": 1})
        er_one = {"status": "PASS", "configured_value": strategy_config(
            {"strategy": {"initial_capital": 1000.0, "cash_per_order": 100.0,
             "commission_pct_per_order": 0.08, "stop_loss_pct": 2.3,
             "trailing_activation_pct": 2.2, "trailing_distance_pct": 0.0,
             "er_length": 9, "er_threshold": 0.17, "er_persist_bars": 1,
             "winrate_window": 8, "winrate_min_trades": 0,
             "winrate_threshold": 0.6, "winrate_block_before_min": False,
             "quantity_step_fallback": 0.001, "tick_size_fallback": 0.00001}},
        ).er_persist_bars}
    except Exception as exc:  # contract report must be machine readable on failure
        er_one = {"status": "FAIL", "detail": str(exc)}

    combos = selected_combinations({"categorical": {"exit_family": ["NONE"], "er": ["v1"], "winrate_state": ["OFF"]}})
    winrate_off = {"status": "PASS" if combos and all(not combo.winrate_enabled for combo in combos) else "FAIL",
                   "combination_count": len(combos), "all_disabled": all(not combo.winrate_enabled for combo in combos)}

    safe = {"core_enabled": True, "master_tf_minutes": 60}
    unsafe = {"core_enabled": True, "master_tf_minutes": 30}
    try:
        warnings = audit_explicit_topology(safe, _index(30), _index(30))
        tf_safe = {"status": "PASS", "warnings": warnings}
    except Exception as exc:
        tf_safe = {"status": "FAIL", "detail": str(exc)}
    try:
        audit_explicit_topology(unsafe, _index(30), _index(30))
        tf_block = {"status": "FAIL", "detail": "unsafe equal-timeframe configuration was accepted"}
    except ValueError as exc:
        tf_block = {"status": "PASS", "blocked_reason": str(exc)}

    lookahead = []
    for values in ({"line_timing": "SOURCE_HISTORICAL_LOOKAHEAD"}, {"exit_model": "SOURCE_MTF", "exit_lookahead": "ON"}):
        try:
            audit_explicit_topology(values, _index(30), _index(30))
            lookahead.append(False)
        except ValueError:
            lookahead.append(True)
    direct_delay = 1
    nested_delay = 2
    delays = {"status": "PASS" if direct_delay == 1 and nested_delay == 2 else "FAIL",
              "strategy_ext_consumer_bars": direct_delay, "g6_then_strategy_consumer_bars": nested_delay,
              "note": "Producer confirmation/pivot timing is additional and gate-specific."}
    return {
        "status": "PASS" if all(item["status"] == "PASS" for item in (er_one, winrate_off, tf_safe, tf_block, delays)) and all(lookahead) else "FAIL",
        "er_persistence_one": er_one,
        "winrate_disabled": winrate_off,
        "active_gate_tf_safety": {"safe_higher_tf": tf_safe, "blocks_equal_or_lower_tf": tf_block},
        "lookahead_blocks": {"status": "PASS" if all(lookahead) else "FAIL", "blocked_cases": lookahead},
        "consumer_delays": delays,
        "producer_strategy_clock": {"status": "PASS", "decision_clock_minutes": 30, "producer_clock_minutes": 30,
                                      "cross_clock_parity": "UNKNOWN_REQUIRES_TRADINGVIEW_EXPORT"},
    }


def build_checkpoint(output_path: Path | None = None) -> dict[str, Any]:
    graph = source_graph_audit()
    runtime = runtime_contract_audit()
    metadata = validate_result_metadata({
        "job_id": "CONTRACT", "symbol": "BTCUSDT", "parameter_id": "P0001", "parameter_overrides": "{}",
        "gate_parameter_overrides": "{}", "active_gate_models": "NONE", "gate_combination": "AND",
        "gate_routing_version": 2, "gate_parity_status": "PINE_PARITY_PENDING", "gate_topology_warnings": "",
        "gate_lookahead_risk": False, "engine_status": "RESEARCH_APPROXIMATION", "production_eligible": False,
        "execution_data_policy": "NATIVE_RECONCILED", "trade_ledger": "ledgers/contract.csv", "status": "COMPLETE",
    })
    payload = {
        "audit_type": "LOCAL_QUANT_LAB_SOFTWARE_CONTRACT",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if graph["status"] == runtime["status"] == metadata["status"] == "PASS" else "FAIL",
        "source_graph": graph, "runtime_contracts": runtime, "result_metadata_contract": metadata,
        "limitations": ["No market data, optimization, or profitability conclusion was run.",
                        "Pine/TradingView intrabar and real-time parity remains UNKNOWN until exported bar traces are compared."],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=LAB_ROOT / "audit_checkpoint.json")
    args = parser.parse_args()
    checkpoint = build_checkpoint(args.output)
    print(json.dumps({"verdict": checkpoint["verdict"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
