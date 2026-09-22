"""Attribute completed control/VWAP trades to causal entry-time regimes.

The regime label uses only information available before the entry bar:
previous close versus a 96-bar EMA and the EMA's 24-bar slope.  Matching
direction on both measures is UP/DOWN; disagreement is SIDEWAYS.  This is a
descriptive diagnostic, not a promoted trading filter.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quant_lab import LAB_ROOT, PROJECT_ROOT, discover_market_roots
from market_data import load_binance_archives


JOBS = {
    "CONTROL": "AUTO-XMKT-S8-OPEN-GATE-CONTROL",
    "VWAP_DIRECT": "AUTO-XMKT-S7-VWAP-DIRECT-ENTRY-ONLY",
    "VWAP_VIA_G6": "AUTO-XMKT-S7-VWAP-VIA-G6-ENTRY-ONLY",
}
SYMBOLS = ["WCTUSDT", "DUSKUSDT", "TAOUSDT"]
OUTPUT = LAB_ROOT / "reports" / "analysis" / "vwap_causal_regimes"


def regime_series(bars: pd.DataFrame, ema_span: int = 96, slope_bars: int = 24) -> pd.Series:
    known_close = bars["close"].shift(1)
    ema = known_close.ewm(span=ema_span, adjust=False, min_periods=ema_span).mean()
    slope = ema - ema.shift(slope_bars)
    up = known_close.gt(ema) & slope.gt(0)
    down = known_close.lt(ema) & slope.lt(0)
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default="SIDEWAYS"), index=bars.index)


def metrics(frame: pd.DataFrame) -> dict:
    pnl = frame["net_pnl"].astype(float)
    gross_win = pnl[pnl > 0].sum()
    gross_loss = -pnl[pnl < 0].sum()
    return {
        "trades": int(len(frame)),
        "wins": int((pnl > 0).sum()),
        "win_rate_pct": float((pnl > 0).mean() * 100) if len(frame) else None,
        "net_pnl_usdt": float(pnl.sum()),
        "avg_net_pnl_usdt": float(pnl.mean()) if len(frame) else None,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
    }


def main() -> None:
    queue = json.loads((LAB_ROOT / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    by_id = {row["id"]: row for row in queue}
    data_root = PROJECT_ROOT / "research" / "data" / "raw"
    regimes: dict[str, pd.Series] = {}
    for symbol in SYMBOLS:
        paths = discover_market_roots(data_root, [symbol], "30m")[symbol]
        bars = load_binance_archives(paths)
        regimes[symbol] = regime_series(bars)

    rows: list[dict] = []
    for variant, job_id in JOBS.items():
        job = by_id[job_id]
        result = pd.read_csv(Path(job["output_dir"]) / "job_results.csv")
        for item in result.loc[result["status"].eq("COMPLETE")].to_dict("records"):
            symbol = item["symbol"]
            ledger = pd.read_csv(Path(job["output_dir"]) / item["trade_ledger"])
            ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
            ledger["regime"] = regimes[symbol].reindex(pd.DatetimeIndex(ledger["entry_time"])).to_numpy()
            if ledger["regime"].isna().any():
                raise ValueError(f"Regime timestamp missing: {symbol} {variant}")
            for regime, group in ledger.groupby("regime", sort=True):
                rows.append({"symbol": symbol, "variant": variant, "regime": regime, **metrics(group)})

    detail = pd.DataFrame(rows).sort_values(["symbol", "regime", "variant"])
    pooled_rows: list[dict] = []
    for variant, job_id in JOBS.items():
        job = by_id[job_id]
        result = pd.read_csv(Path(job["output_dir"]) / "job_results.csv")
        combined: list[pd.DataFrame] = []
        for item in result.loc[result["status"].eq("COMPLETE")].to_dict("records"):
            ledger = pd.read_csv(Path(job["output_dir"]) / item["trade_ledger"])
            ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
            ledger["regime"] = regimes[item["symbol"]].reindex(pd.DatetimeIndex(ledger["entry_time"])).to_numpy()
            combined.append(ledger)
        pooled = pd.concat(combined, ignore_index=True)
        for regime, group in pooled.groupby("regime", sort=True):
            pooled_rows.append({"variant": variant, "regime": regime, **metrics(group)})

    pooled = pd.DataFrame(pooled_rows).sort_values(["regime", "variant"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    detail.to_csv(OUTPUT / "by_symbol_regime.csv", index=False)
    pooled.to_csv(OUTPUT / "pooled_regime.csv", index=False)
    (OUTPUT / "method.json").write_text(json.dumps({
        "status": "RESEARCH_APPROXIMATION",
        "production_eligible": False,
        "pine_parity": "PENDING",
        "label_clock": "entry-time, previous close only",
        "definition": "EMA96(previous close), 24-bar EMA slope; aligned directions UP/DOWN, disagreement SIDEWAYS",
        "warning": "Post-hoc descriptive segmentation; regime definition sensitivity not yet tested.",
        "source_jobs": JOBS,
    }, indent=2) + "\n", encoding="utf-8")
    print(pooled.to_json(orient="records"))


if __name__ == "__main__":
    main()
