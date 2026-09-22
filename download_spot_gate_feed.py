"""Create an explicit Binance spot feed for the Bias gate.

The research engine intentionally never substitutes a futures candle series
for a requested spot identity.  This utility downloads Binance Vision's spot
monthly 1m archives, creates a canonical ISO-UTC OHLCV CSV, and writes a new
manifest containing both the existing BTC perpetual feed and BINANCE:BTCUSDT.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import ssl
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
DEFAULT_START = "2025-09"
DEFAULT_END = "2026-08"


def months(start: str, end: str) -> list[pd.Period]:
    first, last = pd.Period(start, freq="M"), pd.Period(end, freq="M")
    if first > last:
        raise ValueError("start month must not be after end month")
    return list(pd.period_range(first, last, freq="M"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_month(symbol: str, month: pd.Period) -> pd.DataFrame:
    label = str(month)
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1m/{symbol}-1m-{label}.zip"
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(url, timeout=90, context=context) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        raw = pd.read_csv(archive.open(name), header=None)
    raw = raw.iloc[:, :6]
    raw.columns = ["open_time", "open", "high", "low", "close", "volume"]
    # Binance Vision migrated some spot archives to microsecond open times.
    # Detect the unit from the magnitude; interpreting µs as ms pushes dates
    # into the year 57,000 and silently breaks time alignment.
    open_time = pd.to_numeric(raw.pop("open_time"), errors="raise")
    unit = "us" if open_time.abs().median() >= 10**14 else "ms"
    raw["time"] = pd.to_datetime(open_time, unit=unit, utc=True)
    for field in ("open", "high", "low", "close", "volume"):
        raw[field] = pd.to_numeric(raw[field], errors="raise")
    return raw[["time", "open", "high", "low", "close", "volume"]]


def build(symbol: str, start: str, end: str, gate_dir: Path) -> tuple[Path, Path]:
    parts = [fetch_month(symbol, month) for month in months(start, end)]
    frame = pd.concat(parts, ignore_index=True).drop_duplicates("time").sort_values("time")
    expected = pd.date_range(frame.time.iloc[0], frame.time.iloc[-1], freq="min", tz="UTC")
    if len(frame) != len(expected) or not frame.time.reset_index(drop=True).equals(pd.Series(expected)):
        raise ValueError("Spot archive has missing or non-contiguous 1m timestamps")
    spot = gate_dir / f"{symbol}_spot_1m.csv"
    frame.to_csv(spot, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    perp = gate_dir / "btc_perp_1m.csv"
    if not perp.exists():
        raise FileNotFoundError(f"Existing perpetual feed missing: {perp}")
    manifest = gate_dir / "btc_spot_perp_1m_manifest.json"
    manifest.write_text(json.dumps({"feeds": {
        "BINANCE:BTCUSDT.P": {"path": perp.name, "sha256": digest(perp)},
        "BINANCE:BTCUSDT": {"path": spot.name, "sha256": digest(spot)},
    }}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return spot, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Download explicit Binance spot feed for Bias testing")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--gate-dir", type=Path, default=ROOT / "gate_feeds")
    args = parser.parse_args()
    spot, manifest = build(args.symbol.upper(), args.start, args.end, args.gate_dir)
    print(json.dumps({"spot_csv": str(spot), "manifest": str(manifest), "rows": len(pd.read_csv(spot))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
