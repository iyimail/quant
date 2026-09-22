"""Causal configuration guards derived from the Pine source audit.

These guards protect the *research emulator*.  They do not claim that
TradingView rejects the same input choices; source/master gate.pine has no
global timeframe validator.  Refusing ambiguous configurations is deliberate:
the local lab must not turn an unverified MTF clock into performance evidence.
"""
from __future__ import annotations

import pandas as pd


def chart_minutes(index: pd.DatetimeIndex) -> int:
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        raise ValueError("Timeframe audit requires at least two datetime-indexed bars")
    step = index[1] - index[0]
    if step <= pd.Timedelta(0) or not (index.to_series().diff().dropna() == step).all():
        raise ValueError("Timeframe audit requires regular, ordered chart bars")
    seconds = step.total_seconds()
    if seconds % 60:
        raise ValueError("Timeframe audit supports whole-minute chart bars only")
    return int(seconds // 60)


def audit_explicit_topology(values: dict, decision_index: pd.DatetimeIndex,
                            calculation_index: pd.DatetimeIndex | None = None) -> list[str]:
    """Return non-fatal topology warnings or fail closed on causal violations."""
    decision_tf = chart_minutes(decision_index)
    # The local lab may calculate an independently bound Master Gate/HL source
    # on a finer producer chart, then deliver its plot to the strategy chart.
    # Validate Master Gate requests against that producer clock, never against
    # the strategy consumer clock.  The two clocks are recorded as a warning
    # because the actual TradingView binding still needs a parity export.
    producer_tf = chart_minutes(calculation_index) if calculation_index is not None else decision_tf
    problems: list[str] = []

    if values.get("line_timing") == "SOURCE_HISTORICAL_LOOKAHEAD":
        problems.append("SOURCE_HISTORICAL_LOOKAHEAD is diagnostic-only and cannot run a performance test")
    if values.get("exit_model") == "SOURCE_MTF" and values.get("exit_lookahead") == "ON":
        problems.append("SOURCE_MTF exit lookahead=ON is not causal and cannot run a performance test")

    def require_higher(label: str, minutes: int, *, base_tf: int) -> None:
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or int(minutes) != minutes:
            problems.append(f"{label}: a whole-number timeframe is required")
        elif int(minutes) <= base_tf:
            problems.append(
                f"{label}={int(minutes)}m must be strictly above chart={base_tf}m for causal local research; a finer feed cannot make this lower timeframe valid"
            )

    if values.get("core_enabled") or values.get("g3_enabled") or values.get("g4_enabled"):
        require_higher("Master/Core TF", values.get("master_tf_minutes"), base_tf=producer_tf)
    if values.get("most_enabled"):
        require_higher("Gate 1 TF", values.get("g1_tf_minutes"), base_tf=producer_tf)
    if values.get("g5_enabled"):
        require_higher("Gate 5 TF", values.get("g5_tf_minutes"), base_tf=producer_tf)
    if values.get("tillson_enabled"):
        require_higher("Gate 2 TF", values.get("g2_tf_minutes"), base_tf=producer_tf)
    if values.get("pmax_enabled"):
        require_higher("PMAX TF", values.get("pmax_tf_minutes") or values.get("master_tf_minutes"), base_tf=producer_tf)
    if values.get("macro_enabled"):
        require_higher("Macro TF", values.get("macro_minutes"), base_tf=producer_tf)
    if values.get("exit_model") == "SOURCE_MTF":
        if values.get("exit_use_most"):
            require_higher("Exit MOST TF", values.get("exit_most_minutes"), base_tf=decision_tf)
        if values.get("exit_use_rsi"):
            require_higher("Exit RSI TF", values.get("exit_rsi_minutes"), base_tf=decision_tf)
        if values.get("exit_use_t3"):
            require_higher("Exit Tillson TF", values.get("exit_t3_minutes"), base_tf=decision_tf)

    if problems:
        raise ValueError("Causal topology blocked: " + "; ".join(problems))

    warnings: list[str] = []
    if not values.get("strategy_master_enabled", True):
        warnings.append(
            "Master Strategy external-gate master toggle is OFF; source logic blocks all new entries even if an EXT plot is positive"
        )
    if producer_tf != decision_tf:
        warnings.append(
            f"Local model uses a {producer_tf}m producer clock and a {decision_tf}m Strategy clock; TradingView binding parity remains required"
        )
    uses_master_output = any(
        values.get(slot + "_enabled") and values.get(slot + "_source") == "MASTER_GATE"
        for slot in ("ext1", "ext2")
    )
    master_active = any(values.get(key) for key in (
        "core_enabled", "most_enabled", "g5_enabled", "tillson_enabled", "g3_enabled", "g4_enabled",
        "g6_enabled", "g7_enabled", "pmax_enabled",
    ))
    if uses_master_output and not master_active:
        warnings.append("MASTER_GATE is selected but no Master Gate component is active; source output remains closed")
    if any(values.get(slot + "_enabled") and values.get(slot + "_source") == "close" for slot in ("ext1", "ext2")):
        warnings.append("An EXT slot uses raw close; for positive-priced symbols this is effectively always-open, not a gate")
    if values.get("g6_enabled") and values.get("g6_source") == "close":
        warnings.append("G6 uses raw close; for positive-priced symbols it is effectively always-pass in Positive mode")
    return warnings
