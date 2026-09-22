"""WCT G3 source-default 24m clock/data probe; no economic promotion."""

import json
import sys

import pandas as pd

import quant_lab as lab
from gate_models import confirmed_bollinger_gate3

sys.path.insert(0, str(lab.PROJECT_ROOT / "research/scripts"))
from market_data import load_binance_archives, quality_report  # noqa: E402
from parity_pmax import resample_ohlcv  # noqa: E402


ONE_MIN = lab.PROJECT_ROOT / "research/data/raw/binance_um/master_ref00/WCTUSDT/1m"
THIRTY_MIN = lab.PROJECT_ROOT / "research/data/raw/binance_um/pilot13/WCTUSDT/30m"
MONTHS = ("2026-05", "2026-06", "2026-07", "2026-08")
OUT = lab.LAB_ROOT / "reports/g3_wct_clock_probe_v1"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    one = load_binance_archives([ONE_MIN / f"WCTUSDT-1m-{month}.csv" for month in MONTHS])
    native_paths = sorted(path for month in MONTHS for path in THIRTY_MIN.glob(f"WCTUSDT-30m-{month}*.csv"))
    native = load_binance_archives(native_paths)
    one_q = quality_report(one, "1m").to_dict()
    native_q = quality_report(native, "30m").to_dict()
    defects = ("conflicting_duplicate_timestamps", "missing_intervals", "invalid_ohlc_rows", "nonpositive_prices")
    if any(one_q[k] for k in defects) or any(native_q[k] for k in defects):
        raise ValueError(f"Archive quality failure: one={one_q}, native={native_q}")
    chart = resample_ohlcv(one, "30min")
    matched = chart[["open", "high", "low", "close"]].join(
        native[["open", "high", "low", "close"]], how="inner", lsuffix="_1m", rsuffix="_native")
    if len(matched) != len(native):
        raise ValueError(f"1m/30m matched rows {len(matched)} vs native {len(native)}")
    native_diff = {field: int(((matched[field + "_1m"] - matched[field + "_native"]).abs() > 1e-10).sum())
                   for field in ("open", "high", "low", "close")}
    counts = one.close.resample("24min", label="left", closed="left", origin="start_day").size()
    if counts.ne(24).any():
        raise ValueError(f"Incomplete 24m groups: {counts[counts.ne(24)].to_dict()}")
    source = confirmed_bollinger_gate3(one, chart.index)
    proxy = confirmed_bollinger_gate3(chart, chart.index, requested_rule="30min")
    august = pd.DataFrame({"source24m_pass": source.gate_pass,
                           "source24m_strategy_open": source.strategy_gate_open,
                           "proxy30m_pass": proxy.gate_pass,
                           "proxy30m_strategy_open": proxy.strategy_gate_open}, index=chart.index)
    august = august.loc["2026-08-01":"2026-08-31"].copy()
    august["decision_diff"] = august.source24m_strategy_open.ne(august.proxy30m_strategy_open)
    august.to_csv(OUT / "wct_august_g3_source24m_vs_30m_proxy.csv", encoding="utf-8-sig")
    result = {
        "status": "RESEARCH_APPROXIMATION_NOT_PINE_PARITY",
        "test_id": "CLOCK-G3-WCT-24M-001", "source_months": MONTHS,
        "source": "Binance USD-M perpetual WCTUSDT, 1m → 24m, chart 30m",
        "source_default": "G3 BB middle SMA12; close > mid; one requested-TF offset + one chart-TF Strategy offset",
        "one_minute_quality": one_q, "native_30m_quality": native_q,
        "complete_24m_bars": int(len(counts)), "matched_native_30m_bars": int(len(matched)),
        "one_minute_vs_native_30m_ohlc_disagreement_bars": native_diff,
        "august_chart_bars": int(len(august)),
        "source24m_open_share": float(august.source24m_strategy_open.mean()),
        "proxy30m_open_share": float(august.proxy30m_strategy_open.mean()),
        "different_strategy_decision_bars": int(august.decision_diff.sum()),
        "different_strategy_decision_share": float(august.decision_diff.mean()),
        "limitations": ["Clock/data probe only, not Strategy economics",
                        "1m-resampled 30m OHLC does not perfectly equal native 30m archives",
                        "No TradingView G3 state export supplied, so Pine parity not established",
                        "Other Pilot13 symbols lack local 1m archives"]}
    (OUT / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("complete_24m_bars", "august_chart_bars",
                                                    "different_strategy_decision_bars",
                                                    "different_strategy_decision_share")}, indent=2))


if __name__ == "__main__":
    main()
