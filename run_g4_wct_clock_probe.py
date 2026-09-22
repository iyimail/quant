"""WCT G4 24m momentum clock probe; source-timing approximation only."""

import json

import pandas as pd

import quant_lab as lab
from gate_models import confirmed_momentum_gate4
from run_g3_wct_clock_probe import MONTHS, ONE_MIN, THIRTY_MIN, load_binance_archives, quality_report, resample_ohlcv


OUT = lab.LAB_ROOT / "reports/g4_wct_clock_probe_v1"


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
    if len(chart) != len(native):
        raise ValueError("Chart/native 30m counts differ")
    counts = one.close.resample("24min", label="left", closed="left", origin="start_day").size()
    if counts.ne(24).any():
        raise ValueError(f"Incomplete 24m groups: {counts[counts.ne(24)].to_dict()}")
    source = confirmed_momentum_gate4(one, chart.index)
    proxy = confirmed_momentum_gate4(chart, chart.index, requested_rule="30min")
    august = pd.DataFrame({"source24m_pass": source.gate_pass,
                           "source24m_strategy_open": source.strategy_gate_open,
                           "proxy30m_pass": proxy.gate_pass,
                           "proxy30m_strategy_open": proxy.strategy_gate_open}, index=chart.index)
    august = august.loc["2026-08-01":"2026-08-31"].copy()
    august["decision_diff"] = august.source24m_strategy_open.ne(august.proxy30m_strategy_open)
    august.to_csv(OUT / "wct_august_g4_source24m_vs_30m_proxy.csv", encoding="utf-8-sig")
    result = {
        "status": "RESEARCH_APPROXIMATION_NOT_PINE_PARITY", "test_id": "CLOCK-G4-WCT-24M-001",
        "source_months": MONTHS, "symbol": "WCTUSDT",
        "source_default": "24m linreg50, slope EMA5, threshold1.5; one requested-TF offset + one Strategy chart offset",
        "one_minute_quality": one_q, "native_30m_quality": native_q,
        "complete_24m_bars": int(len(counts)), "august_chart_bars": int(len(august)),
        "source24m_open_share": float(august.source24m_strategy_open.mean()),
        "proxy30m_open_share": float(august.proxy30m_strategy_open.mean()),
        "different_strategy_decision_bars": int(august.decision_diff.sum()),
        "different_strategy_decision_share": float(august.decision_diff.mean()),
        "source24m_rising_edges": int((august.source24m_strategy_open & ~august.source24m_strategy_open.shift(1).fillna(False)).sum()),
        "proxy30m_rising_edges": int((august.proxy30m_strategy_open & ~august.proxy30m_strategy_open.shift(1).fillna(False)).sum()),
        "limitations": ["Timing/clock probe, not economic gate-value test",
                        "Python rolling linreg and EMA have not passed TradingView value-by-value parity",
                        "Only WCTUSDT currently has local 1m data among Pilot13",
                        "Source name Override does not bypass other active Master gates"]}
    (OUT / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("complete_24m_bars", "august_chart_bars",
                                                    "different_strategy_decision_bars",
                                                    "different_strategy_decision_share",
                                                    "source24m_open_share", "proxy30m_open_share")}, indent=2))


if __name__ == "__main__":
    main()
