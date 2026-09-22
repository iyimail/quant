"""Check whether VWAP regime conclusions survive three causal label speeds."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from analyze_vwap_causal_regimes import JOBS, SYMBOLS, metrics, regime_series
from quant_lab import LAB_ROOT, PROJECT_ROOT, discover_market_roots
from market_data import load_binance_archives


SETTINGS = {"FAST": (48, 12), "BASE": (96, 24), "SLOW": (192, 48)}
OUTPUT = LAB_ROOT / "reports" / "analysis" / "vwap_regime_sensitivity"


def main() -> None:
    queue = json.loads((LAB_ROOT / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    by_id = {row["id"]: row for row in queue}
    data_root = PROJECT_ROOT / "research" / "data" / "raw"
    bars_by_symbol = {
        symbol: load_binance_archives(discover_market_roots(data_root, [symbol], "30m")[symbol])
        for symbol in SYMBOLS
    }
    rows: list[dict] = []
    for setting, (span, slope_bars) in SETTINGS.items():
        labels = {symbol: regime_series(bars, span, slope_bars) for symbol, bars in bars_by_symbol.items()}
        for variant, job_id in JOBS.items():
            job = by_id[job_id]
            results = pd.read_csv(Path(job["output_dir"]) / "job_results.csv")
            combined: list[pd.DataFrame] = []
            for item in results.loc[results["status"].eq("COMPLETE")].to_dict("records"):
                ledger = pd.read_csv(Path(job["output_dir"]) / item["trade_ledger"])
                ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
                ledger["regime"] = labels[item["symbol"]].reindex(pd.DatetimeIndex(ledger["entry_time"])).to_numpy()
                combined.append(ledger)
            pooled = pd.concat(combined, ignore_index=True)
            for regime, group in pooled.groupby("regime", sort=True):
                rows.append({
                    "setting": setting, "ema_span": span, "slope_bars": slope_bars,
                    "variant": variant, "regime": regime, **metrics(group),
                })
    report = pd.DataFrame(rows).sort_values(["setting", "regime", "variant"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report.to_csv(OUTPUT / "pooled_sensitivity.csv", index=False)
    winners = (report.sort_values(["setting", "regime", "avg_net_pnl_usdt"], ascending=[True, True, False])
               .groupby(["setting", "regime"], as_index=False).first())
    winners.to_csv(OUTPUT / "winners_by_average_trade.csv", index=False)
    print(winners[["setting", "regime", "variant", "trades", "avg_net_pnl_usdt", "profit_factor"]].to_json(orient="records"))


if __name__ == "__main__":
    main()
