"""Golden, bar-by-bar audit for the read-only HL Pine source.

This is intentionally an auditor, not a Pine emulator approval switch.  It
compares the two diagnostic plots already exposed by ``hl gate.pine`` against
the local confirmed-bar translation using the exact 1m OHLCV feed.  No source
Pine file is modified and a successful comparison does not establish strategy
fill, Master Gate, or live/realtime parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hl_model import hl_gate


LAB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_ROOT.parent
HL_SOURCE = PROJECT_ROOT / "sources" / "hl gate.pine"
REQUIRED_INPUTS = {
    "left", "right", "mtf_minutes", "htf_minutes", "activation",
    "confirmation", "close_source", "close_mode",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def template() -> dict[str, Any]:
    """Return the smallest reproducible runtime contract for a HL export."""
    return {
        "schema": "local-quant-lab-hl-golden-v1",
        "symbol": "BINANCE:BTCUSDT.P",
        "chart_timeframe_minutes": 1,
        "inputs": {
            "left": 5, "right": 5, "mtf_minutes": 15, "htf_minutes": 60,
            "activation": "HTF HL", "confirmation": "Moderate",
            "close_source": "HTF", "close_mode": "Any Next Signal",
        },
        "export_columns": {
            "time": "time",
            "long_gate": "Long Gate",
            "gate_status": "Gate Diagnostic State",
        },
        "minimum_rows": 500,
        "require_activation_and_close": True,
        "notes": [
            "Export Chart Data from the same 1-minute BINANCE perpetual chart with hl gate.pine added.",
            "Include time, OHLC and the existing Long Gate plus Gate Diagnostic State plots.",
            "The CSV window must be fully contained in the supplied immutable 1m OHLCV feed.",
        ],
    }


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _column(frame: pd.DataFrame, requested: str, semantic: str) -> str:
    if requested in frame.columns:
        return requested
    exact = [column for column in frame.columns if _normal(column) == _normal(requested)]
    if len(exact) == 1:
        return exact[0]
    aliases = {
        "time": {"time", "date", "timestamp"},
        "long_gate": {"longgate"},
        "gate_status": {"gatediagnosticstate"},
    }[semantic]
    candidates = [column for column in frame.columns
                  if _normal(column) in aliases or any(_normal(column).endswith(item) for item in aliases)]
    if len(candidates) != 1:
        raise ValueError(f"Export column unresolved for {semantic}: requested={requested!r}, candidates={candidates}")
    return candidates[0]


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != "local-quant-lab-hl-golden-v1":
        raise ValueError("Unsupported HL golden manifest schema")
    if manifest.get("chart_timeframe_minutes") != 1:
        raise ValueError("HL golden audit currently requires a 1m producer chart")
    inputs = manifest.get("inputs", {})
    missing = REQUIRED_INPUTS - set(inputs)
    if missing:
        raise ValueError("HL golden manifest inputs missing: " + ", ".join(sorted(missing)))
    if not isinstance(manifest.get("export_columns"), dict):
        raise ValueError("HL golden manifest export_columns required")
    return manifest


def load_ohlcv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError("Calculation feed missing: " + ", ".join(sorted(missing)))
    raw.index = pd.to_datetime(raw.pop("time"), utc=True, errors="coerce")
    if raw.index.hasnans or not raw.index.is_unique or not raw.index.is_monotonic_increasing:
        raise ValueError("Calculation feed timestamps invalid")
    result = raw[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(result.to_numpy()).all() or (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Calculation feed OHLCV invalid")
    if ((result.high < result[["open", "close", "low"]].max(axis=1)) |
            (result.low > result[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Calculation feed OHLC bounds invalid")
    if len(result) < 2 or not (result.index.to_series().diff().dropna() == pd.Timedelta(minutes=1)).all():
        raise ValueError("HL golden audit requires contiguous 1m calculation feed")
    return result


def load_export(path: Path, manifest: dict[str, Any]) -> pd.DataFrame:
    raw = pd.read_csv(path)
    columns = manifest["export_columns"]
    time_column = _column(raw, columns.get("time", "time"), "time")
    long_column = _column(raw, columns.get("long_gate", "Long Gate"), "long_gate")
    status_column = _column(raw, columns.get("gate_status", "Gate Diagnostic State"), "gate_status")
    index = pd.DatetimeIndex(pd.to_datetime(raw[time_column], utc=True, errors="coerce"))
    if index.isna().any() or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("TradingView export timestamps invalid")
    result = pd.DataFrame({
        "tv_long_gate": pd.to_numeric(raw[long_column], errors="coerce").to_numpy(),
        "tv_gate_status": pd.to_numeric(raw[status_column], errors="coerce").to_numpy(),
    }, index=index)
    if result.isna().any().any() or not result.tv_long_gate.isin([0, 1]).all() or not result.tv_gate_status.isin([0, 1, 2, 3, 4]).all():
        raise ValueError("TradingView Long Gate / Gate Diagnostic State values invalid")
    return result.astype(int)


def audit(export: pd.DataFrame, feed: pd.DataFrame, manifest: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    inputs = manifest["inputs"]
    local = hl_gate(feed, **inputs)
    local = local.reindex(export.index)
    if local.isna().any().any():
        missing = int(local.isna().any(axis=1).sum())
        raise ValueError(f"TradingView export is outside calculation-feed coverage: {missing} rows")
    comparison = export.join(local[["gate_output", "status", "hierarchy_valid"]])
    comparison["long_gate_match"] = comparison.tv_long_gate.eq(comparison.gate_output)
    comparison["status_match"] = comparison.tv_gate_status.eq(comparison.status)
    comparison["row_match"] = comparison.long_gate_match & comparison.status_match
    minimum_rows = int(manifest.get("minimum_rows", 500))
    activation_count = int(comparison.tv_gate_status.eq(2).sum())
    close_count = int(comparison.tv_gate_status.eq(3).sum())
    exact = bool(comparison.row_match.all())
    material = len(comparison) >= minimum_rows and (not manifest.get("require_activation_and_close", True) or (activation_count > 0 and close_count > 0))
    status = "PASS" if exact and material else "FAIL" if not exact else "INCONCLUSIVE"
    summary = {
        "audit_id": "HL-GOLDEN-BAR-PARITY-V1",
        "status": status,
        "scope": "HL producer Long Gate + Gate Diagnostic State only; no Master/Strategy/fill parity",
        "source_hl_gate_sha256": sha256(HL_SOURCE),
        "export_rows": len(comparison),
        "minimum_rows": minimum_rows,
        "long_gate_mismatches": int((~comparison.long_gate_match).sum()),
        "status_mismatches": int((~comparison.status_match).sum()),
        "all_rows_match": exact,
        "activation_events": activation_count,
        "close_events": close_count,
        "all_local_hierarchies_valid": bool(comparison.hierarchy_valid.astype(bool).all()),
        "inputs": inputs,
        "symbol": manifest.get("symbol"),
        "chart_timeframe_minutes": manifest["chart_timeframe_minutes"],
        "limitations": [
            "PASS validates only exported historical HL producer states for this pinned runtime profile.",
            "It does not validate TradingView realtime behavior, Master G6/EXT consumer timing, broker fills, or profitability.",
        ],
    }
    return comparison, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="HL Pine golden-export parity auditor")
    parser.add_argument("--write-template", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--export-csv", type=Path)
    parser.add_argument("--calculation-feed-csv", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.write_template:
        args.write_template.parent.mkdir(parents=True, exist_ok=True)
        args.write_template.write_text(json.dumps(template(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(args.write_template)
        return
    if not all((args.manifest, args.export_csv, args.calculation_feed_csv, args.output_dir)):
        parser.error("manifest, export-csv, calculation-feed-csv and output-dir are required")
    manifest = load_manifest(args.manifest)
    comparison, summary = audit(load_export(args.export_csv, manifest), load_ohlcv(args.calculation_feed_csv), manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(args.output_dir / "hl_bar_comparison.csv", index_label="time")
    summary["input_hashes"] = {
        "manifest": sha256(args.manifest), "tradingview_export": sha256(args.export_csv),
        "calculation_feed": sha256(args.calculation_feed_csv),
    }
    (args.output_dir / "hl_parity_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
