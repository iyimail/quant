"""Local Quant Lab: offline-first TradingView audit and Binance backtest runner.

The tool uses no language-model API.  It downloads public Binance candles,
audits TradingView exports, runs the project's causal research emulator and
writes a compact handoff report for later review by Codex.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from lab_safety import atomic_write_json, update_queue, file_lock, validate_parameters, file_digest, engine_identity, validate_engine
from gate_settings import ROUTING_DEFAULTS, ROUTING_CHOICES, ROUTING_INTS
from hl_model import hl_gate
from component_settings import FLOATS, TEXT_FIELDS, validate_component
from bias_model import bias_gate
from master_models import core_matrix, pmax_matrix, most_line, t3_line
from pine_math import aggregate, confirmed, ema
from gate_data import load_feed_manifest
from strategy_models import exit_conditions
from gate_wiring import g6_pass, master_latch, strategy_external, ExternalRouting
from topology_safety import audit_explicit_topology

import numpy as np
import pandas as pd


def configure_console_utf8() -> None:
    """Keep Turkish progress messages safe on Windows pipes and consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


configure_console_utf8()


LAB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_ROOT.parent
RESEARCH_SCRIPTS = PROJECT_ROOT / "research" / "scripts"
sys.path.insert(0, str(RESEARCH_SCRIPTS))

from market_data import load_binance_archives, quality_report  # noqa: E402
from run_master_ref00_parity import Ref00Config  # noqa: E402
from run_wct_categorical_combo_screen import Combo, simulate  # noqa: E402
from run_wct_exit_family_screen import build_conditions  # noqa: E402
from gate_models import (  # noqa: E402
    VwapGateConfig,
    causal_most_gate1,
    confirmed_tillson_gate2,
    strategy_gate_open,
    vwap_ext_score,
    price_source, confirmed_macro_reset,
)


REQUIRED_TV_SHEETS = {
    "Performance",
    "Trades analysis",
    "Risk-adjusted performance",
    "Trades",
    "Properties",
}
VOLATILE_PROPERTIES = {
    "Gate Panelini Göster",
    "'GO' Etiketlerini Göster",
    "Debug: Data Window plotları",
    "Panelde Bugünün Realized PnL'ini Göster",
}
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]

GATE_PARAMETER_DEFAULTS = {
    **ROUTING_DEFAULTS,
    "external_gate_closes_position": True,
    "vwap_enabled": False,
    "vwap_band_bps": 0,
    "vwap_persist_bars": 3,
    "vwap_via_g6": True,
    "most_enabled": False,
    "most_length": 14,
    "most_percent": 2.0,
    "tillson_enabled": False,
    "tillson_length": 8,
    "tillson_factor": 0.7,
}
GATE_BOOL_FIELDS = {key for key, value in GATE_PARAMETER_DEFAULTS.items() if isinstance(value, bool)}
GATE_INT_FIELDS = {"vwap_band_bps", "vwap_persist_bars", "most_length", "tillson_length"}
GATE_INT_FIELDS |= ROUTING_INTS

# Native 30m candles remain the default execution source. This opt-in policy
# is research-only for a pinned 1m feed that demonstrably disagrees with its
# native 30m archive; it never weakens native reconciliation.
EXECUTION_DATA_POLICIES = {"NATIVE_RECONCILED", "CALCULATION_1M_DERIVED"}
RESEARCH_DECISION_INTERVALS = {"5m", "30m"}


def interval_minutes(interval: str) -> int:
    """Convert the supported Binance interval into a fixed candle duration."""
    match = re.fullmatch(r"(\d+)(m|h|d)", str(interval))
    if not match:
        raise ValueError(f"Unsupported interval: {interval!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"m": 1, "h": 60, "d": 1440}[unit]


def jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if pd.isna(value):
        return None
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=jsonable)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("market") != "futures/um":
        raise ValueError("İlk sürüm yalnız Binance USD-M futures verisini destekler")
    return config


def two_column_sheet(workbook: Path, sheet: str) -> dict[str, Any]:
    frame = pd.read_excel(workbook, sheet_name=sheet, header=None)
    return {
        str(key): jsonable(value)
        for key, value in frame.iloc[1:, :2].itertuples(index=False, name=None)
        if pd.notna(key)
    }


def metric(sheet: pd.DataFrame, row: str, column: str) -> Any:
    try:
        return jsonable(sheet.loc[row, column])
    except KeyError:
        return None


def longest_streak(values: Iterable[float], winning: bool) -> int:
    longest = current = 0
    for value in values:
        matched = value > 0 if winning else value < 0
        current = current + 1 if matched else 0
        longest = max(longest, current)
    return longest


def max_close_drawdown_pct(pnl: pd.Series, initial_capital: float) -> float:
    equity = initial_capital + pnl.cumsum()
    origin = pd.Series([initial_capital])
    curve = pd.concat([origin, equity], ignore_index=True)
    peak = curve.cummax()
    return float(((curve - peak) / peak * 100.0).min())


def canonical_trades(trades: pd.DataFrame) -> pd.DataFrame:
    result = trades.copy()
    for column in result.columns:
        if "Date and time" == str(column):
            result[column] = pd.to_datetime(result[column], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        elif pd.api.types.is_numeric_dtype(result[column]):
            result[column] = pd.to_numeric(result[column], errors="coerce").round(12)
    return result.reset_index(drop=True)


def trade_signature(trades: pd.DataFrame) -> str:
    """Create the same signature for semantically equal XLSX and CSV trades."""
    normalized = canonical_trades(trades)
    # TradingView recomputes/rounds this derived display column differently in
    # XLSX and CSV. Price, quantity, PnL and every decision field remain in the
    # signature, so omitting it does not hide a strategy behavior change.
    normalized = normalized.drop(columns=["Size (value)"], errors="ignore")
    rows: list[list[str]] = []
    for row in normalized.itertuples(index=False, name=None):
        values: list[str] = []
        for value in row:
            if pd.isna(value):
                values.append("<NA>")
            elif isinstance(value, (int, float, np.integer, np.floating)):
                # TradingView CSV exports round some XLSX numeric cells (most
                # visibly Size (value)) by a few 1e-8. Ten significant digits
                # preserves trading values while treating that export-format
                # rounding as semantic equality.
                values.append(format(float(value), ".8g"))
            else:
                values.append(str(value).strip())
        rows.append(values)
    return stable_hash({"columns": [str(c) for c in normalized.columns], "rows": rows})


def audit_tv_workbook(path: Path) -> tuple[dict[str, Any], pd.DataFrame | None]:
    record: dict[str, Any] = {
        "file": str(path),
        "file_name": path.name,
        "sha256": sha256_file(path),
        "status": "OK",
        "issues": [],
    }
    try:
        excel = pd.ExcelFile(path)
        missing = sorted(REQUIRED_TV_SHEETS - set(excel.sheet_names))
        if missing:
            raise ValueError(f"Eksik TradingView sekmeleri: {', '.join(missing)}")
        props = two_column_sheet(path, "Properties")
        perf = pd.read_excel(path, sheet_name="Performance", index_col=0)
        trade_stats = pd.read_excel(path, sheet_name="Trades analysis", index_col=0)
        risk = pd.read_excel(path, sheet_name="Risk-adjusted performance", index_col=0)
        trades = pd.read_excel(path, sheet_name="Trades")
        canonical = canonical_trades(trades)
        exits = canonical[canonical["Type"].astype(str).str.lower().eq("exit long")].copy()
        entries = canonical[canonical["Type"].astype(str).str.lower().eq("entry long")].copy()
        pnl = pd.to_numeric(exits.get("Net PnL USDT"), errors="coerce").dropna()
        initial_capital = float(props.get("Initial capital") or 1000.0)
        summary_trades = metric(trade_stats, "Total trades", "All USDT")
        reported_net = metric(perf, "Net profit", "All USDT")
        rounded_net = float(pnl.sum()) if len(pnl) else 0.0
        if len(entries) != len(exits):
            record["issues"].append(f"Entry/exit sayısı eşit değil: {len(entries)}/{len(exits)}")
        if summary_trades is not None and int(summary_trades) != len(exits):
            record["issues"].append(f"Özet işlem sayısı Trades sekmesiyle uyuşmuyor: {summary_trades}/{len(exits)}")
        if reported_net is not None and abs(float(reported_net) - rounded_net) > max(0.25, len(exits) * 0.005):
            record["issues"].append(f"Yuvarlanmış işlem PnL toplamı rapordan sapıyor: {rounded_net:.2f}/{reported_net}")

        settings = {k: v for k, v in props.items() if k not in VOLATILE_PROPERTIES}
        record.update({
            "symbol": props.get("Symbol"),
            "timeframe": props.get("Timeframe"),
            "trading_range": props.get("Trading range"),
            "backtesting_range": props.get("Backtesting range"),
            "properties": props,
            "settings_hash": stable_hash(settings),
            "trade_signature": trade_signature(canonical),
            "rows": len(canonical),
            "closed_trades": len(exits),
            "winners": int((pnl > 0).sum()),
            "losers": int((pnl < 0).sum()),
            "win_rate_pct": None if len(pnl) == 0 else float((pnl > 0).mean() * 100.0),
            "net_profit_usdt": reported_net,
            "gross_profit_usdt": metric(perf, "Gross profit", "All USDT"),
            "gross_loss_usdt": metric(perf, "Gross loss", "All USDT"),
            "commission_usdt": metric(perf, "Commission paid", "All USDT"),
            "profit_factor": metric(risk, "Profit factor", "All USDT"),
            "sharpe": metric(risk, "Sharpe ratio", "All USDT"),
            "sortino": metric(risk, "Sortino ratio", "All USDT"),
            "max_intrabar_dd_usdt": metric(perf, "Max drawdown (intrabar)", "All USDT"),
            "max_intrabar_dd_pct_initial": metric(perf, "Max drawdown as % of initial capital (intrabar)", "All %"),
            "median_trade_pnl_usdt": None if len(pnl) == 0 else float(pnl.median()),
            "close_curve_max_dd_pct": None if len(pnl) == 0 else max_close_drawdown_pct(pnl, initial_capital),
            "max_consecutive_wins": longest_streak(pnl, True),
            "max_consecutive_losses": longest_streak(pnl, False),
            "top_5_winner_share_gross_profit": None,
            "top_10_winner_share_net_profit": None,
        })
        wins = pnl[pnl > 0].sort_values(ascending=False)
        if len(wins) and wins.sum() != 0:
            record["top_5_winner_share_gross_profit"] = float(wins.head(5).sum() / wins.sum())
        if len(wins) and reported_net not in (None, 0):
            record["top_10_winner_share_net_profit"] = float(wins.head(10).sum() / float(reported_net))
        record["status"] = "CHECK" if record["issues"] else "OK"
        return record, canonical
    except Exception as exc:  # one corrupt export must not stop the full folder
        record["status"] = "ERROR"
        record["issues"].append(str(exc))
        return record, None


def audit_tv_csv(path: Path) -> dict[str, Any] | None:
    """Inventory a TradingView trade-list CSV without inventing missing settings."""
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    required = {"Trade number", "Type", "Date and time", "Price USDT", "Net PnL USDT"}
    if not required.issubset(frame.columns):
        return None
    canonical = canonical_trades(frame)
    exits = canonical[canonical["Type"].astype(str).str.lower().eq("exit long")]
    pnl = pd.to_numeric(exits["Net PnL USDT"], errors="coerce").dropna()
    symbol_match = re.search(r"BINANCE_(.+?)\.P_", path.name)
    return {
        "file": str(path),
        "file_name": path.name,
        "sha256": sha256_file(path),
        "symbol": None if symbol_match is None else f"BINANCE:{symbol_match.group(1)}.P",
        "rows": len(canonical),
        "closed_trades": len(exits),
        "winners": int((pnl > 0).sum()),
        "losers": int((pnl < 0).sum()),
        "rounded_net_profit_usdt": float(pnl.sum()) if len(pnl) else 0.0,
        "trade_signature": trade_signature(canonical),
        "matching_workbook_file": None,
    }


def comparable_properties(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in record.get("properties", {}).items()
        if key not in VOLATILE_PROPERTIES
    }


def compare_tv_records(
    records: list[dict[str, Any]], trade_frames: dict[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for left_index, left in enumerate(records):
        if left.get("status") == "ERROR":
            continue
        for right in records[left_index + 1:]:
            if right.get("status") == "ERROR":
                continue
            same_scope = all(left.get(k) == right.get(k) for k in ("symbol", "timeframe", "trading_range"))
            if not same_scope:
                continue
            lp, rp = comparable_properties(left), comparable_properties(right)
            changes = [
                {"setting": key, "left": lp.get(key), "right": rp.get(key)}
                for key in sorted(set(lp) | set(rp)) if str(lp.get(key)) != str(rp.get(key))
            ]
            if len(changes) > 5:
                continue
            left_frame = trade_frames.get(left["file"])
            right_frame = trade_frames.get(right["file"])
            exact = bool(
                left_frame is not None and right_frame is not None
                and left_frame.fillna("<NA>").astype(str).equals(right_frame.fillna("<NA>").astype(str))
            )
            comparisons.append({
                "left_file": left["file_name"],
                "right_file": right["file_name"],
                "symbol": left.get("symbol"),
                "property_changes": changes,
                "property_change_count": len(changes),
                "trades_exactly_equal": exact,
                "left_trade_signature": left.get("trade_signature"),
                "right_trade_signature": right.get("trade_signature"),
                "delta_trades": (right.get("closed_trades") or 0) - (left.get("closed_trades") or 0),
                "delta_net_profit_usdt": None if left.get("net_profit_usdt") is None or right.get("net_profit_usdt") is None else float(right["net_profit_usdt"]) - float(left["net_profit_usdt"]),
                "delta_profit_factor": None if left.get("profit_factor") is None or right.get("profit_factor") is None else float(right["profit_factor"]) - float(left["profit_factor"]),
                "verdict": "NO_BEHAVIOR_CHANGE" if exact else "BEHAVIOR_CHANGED",
            })
    return comparisons


def month_starts(start: date, end: date) -> list[date]:
    current = date(start.year, start.month, 1)
    result = []
    while current < end:
        result.append(current)
        current = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return result


def download_file(url: str, target: Path, timeout: int, retries: int) -> tuple[bool, str]:
    if target.exists() and target.stat().st_size > 0:
        return True, "cached"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LocalQuantLab/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as output:
                shutil.copyfileobj(response, output)
            temp.replace(target)
            return True, "downloaded"
        except urllib.error.HTTPError as exc:
            if temp.exists():
                temp.unlink()
            if exc.code == 404:
                return False, "not_available"
            last = f"HTTP {exc.code}"
        except Exception as exc:
            if temp.exists():
                temp.unlink()
            last = str(exc)
        if attempt < retries:
            time.sleep(min(2 ** attempt, 4))
    return False, last


def verify_checksum(zip_path: Path, checksum_path: Path) -> tuple[bool, str]:
    if not checksum_path.exists():
        return False, "checksum_missing"
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(zip_path).lower()
    return actual == expected, "verified" if actual == expected else "checksum_mismatch"


def extract_archive(zip_path: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        safe = []
        for member in archive.infolist():
            name = Path(member.filename).name
            if not name.lower().endswith(".csv"):
                continue
            output = destination / name
            if not output.exists():
                with archive.open(member) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
            safe.append(output)
    return safe


def download_symbol(config: dict[str, Any], symbol: str, output_root: Path) -> list[dict[str, Any]]:
    interval = config["interval"]
    options = config["download"]
    start = date.fromisoformat(options["start"])
    end = date.fromisoformat(options["end"])
    timeout = int(options.get("timeout_seconds", 30))
    retries = int(options.get("retries", 2))
    records: list[dict[str, Any]] = []
    base = "https://data.binance.vision/data/futures/um"
    symbol_root = output_root / symbol / interval
    for month in month_starts(start, end):
        next_month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
        monthly_complete = next_month <= end
        label = month.strftime("%Y-%m")
        name = f"{symbol}-{interval}-{label}.zip"
        url = f"{base}/monthly/klines/{symbol}/{interval}/{name}"
        target = symbol_root / name
        ok, status = download_file(url, target, timeout, retries)
        checksum_status = "not_checked"
        extracted: list[Path] = []
        if ok:
            if options.get("verify_checksums", True):
                checksum = target.with_suffix(target.suffix + ".CHECKSUM")
                checksum_ok, _ = download_file(url + ".CHECKSUM", checksum, timeout, retries)
                verified, checksum_status = verify_checksum(target, checksum) if checksum_ok else (False, "checksum_unavailable")
                if not verified:
                    ok = False
            if ok:
                extracted = extract_archive(target, symbol_root)
        records.append({"symbol": symbol, "period": label, "url": url, "status": status if ok else "FAILED", "download_status": status, "checksum": checksum_status, "csv_files": [str(p) for p in extracted]})

        # If a monthly file is not yet published, request completed daily files.
        if not ok and not monthly_complete:
            day = max(start, month)
            last_day = min(end, next_month)
            while day < last_day:
                daily_name = f"{symbol}-{interval}-{day.isoformat()}.zip"
                daily_url = f"{base}/daily/klines/{symbol}/{interval}/{daily_name}"
                daily_target = symbol_root / daily_name
                day_ok, day_status = download_file(daily_url, daily_target, timeout, retries)
                day_checksum_status = "not_checked"
                daily_extracted: list[Path] = []
                if day_ok:
                    if options.get("verify_checksums", True):
                        checksum = daily_target.with_suffix(daily_target.suffix + ".CHECKSUM")
                        checksum_ok, _ = download_file(daily_url + ".CHECKSUM", checksum, timeout, retries)
                        verified, day_checksum_status = verify_checksum(daily_target, checksum) if checksum_ok else (False, "checksum_unavailable")
                        if not verified:
                            day_ok = False
                    if day_ok:
                        daily_extracted = extract_archive(daily_target, symbol_root)
                records.append({"symbol": symbol, "period": day.isoformat(), "url": daily_url, "status": day_status if day_ok else "FAILED", "download_status": day_status, "checksum": day_checksum_status, "csv_files": [str(p) for p in daily_extracted]})
                day += timedelta(days=1)
    return records


def discover_market_roots(data_root: Path, symbols: list[str], interval: str) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    # The lab's execution model is USD-M only. Symbol names alone are NOT identity.
    root = data_root.resolve()
    parts = {part.lower() for part in root.parts}
    if parts & {"binance_spot", "binance_cm"}:
        raise ValueError("Vadeli test spot/coin-margined veri klasörünü kullanamaz")
    market_root = root if "binance_um" in parts else root / "binance_um"
    for symbol in symbols:
        pattern = re.compile(re.escape(f"{symbol}-{interval}-") + r"\d{4}-\d{2}(?:-\d{2})?\.csv")
        candidates = market_root.rglob(f"{symbol}-{interval}-*.csv")
        result[symbol] = sorted({p.resolve() for p in candidates if pattern.fullmatch(p.name)
                                 and p.resolve().is_relative_to(market_root.resolve())
                                 and not {"binance_spot", "binance_cm"} & {part.lower() for part in p.resolve().parts}})
    return result


def audit_market_data(data_root: Path, symbols: list[str], interval: str) -> list[dict[str, Any]]:
    reports = []
    for symbol, paths in discover_market_roots(data_root, symbols, interval).items():
        if not paths:
            reports.append({"symbol": symbol, "status": "NO_DATA", "files": 0})
            continue
        try:
            bars = load_binance_archives(paths)
            quality = quality_report(bars, interval)
            bad = any(getattr(quality, key) for key in ("conflicting_duplicate_timestamps", "missing_intervals", "nonpositive_prices", "negative_volume_rows", "invalid_ohlc_rows", "incomplete_last_bar", "out_of_order_rows"))
            bad = bad or not np.isfinite(bars[["open", "high", "low", "close", "volume", "quote_volume"]].to_numpy()).all()
            reports.append({"symbol": symbol, "status": "FAIL" if bad else "OK", "files": len(paths), **quality.to_dict()})
        except Exception as exc:
            reports.append({"symbol": symbol, "status": "ERROR", "files": len(paths), "error": str(exc)})
    return reports


def independent_combinations() -> list[Combo]:
    result: list[Combo] = []
    for exit_family in ("NONE", "MOST", "RSI_SMA", "TILLSON"):
        for er in ("OFF", "v1", "v2"):
            result.append(Combo(exit_family, er, False, "SHADOW", "WEIGHTED", False))
            result.append(Combo(exit_family, er, True, "SHADOW", "WEIGHTED", False))
            result.append(Combo(exit_family, er, True, "SHADOW", "WEIGHTED", True))
    return result


def strategy_config(config: dict[str, Any], overrides: dict[str, Any] | None = None) -> Ref00Config:
    values = {**config["strategy"], **(overrides or {})}
    validate_parameters(values)
    return Ref00Config(
        cash_per_order=float(values["cash_per_order"]),
        initial_capital=float(values["initial_capital"]),
        commission_pct_per_order=float(values["commission_pct_per_order"]),
        quantity_step=float(values["quantity_step_fallback"]),
        tick_size=float(values["tick_size_fallback"]),
        er_length=int(values["er_length"]),
        er_threshold=float(values["er_threshold"]),
        er_persist_bars=int(values["er_persist_bars"]),
        stop_loss_pct=float(values["stop_loss_pct"]),
        trailing_activation_pct=float(values["trailing_activation_pct"]),
        trailing_distance_pct=float(values["trailing_distance_pct"]),
        winrate_window=int(values["winrate_window"]),
        winrate_min_trades=int(values["winrate_min_trades"]),
        winrate_threshold=float(values["winrate_threshold"]),
        winrate_block_before_min=bool(values["winrate_block_before_min"]),
    )


EXCHANGE_INFO_SNAPSHOT = LAB_ROOT.parent / "research" / "data" / "raw" / "binance_um_exchangeInfo_2026-09-01.json"


def load_execution_rules(path: Path = EXCHANGE_INFO_SNAPSHOT) -> dict[str, dict[str, Any]]:
    """Load recorded USD-M price/quantity increments without a network guess."""
    if not path.is_file():
        raise ValueError(f"Yerel Binance piyasa kuralı kaydı bulunamadı: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("symbols", []):
        if item.get("contractType") != "PERPETUAL":
            continue
        filters = {row.get("filterType"): row for row in item.get("filters", [])}
        try:
            tick_size = float(filters["PRICE_FILTER"]["tickSize"])
            quantity_step = float(filters["LOT_SIZE"]["stepSize"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(tick_size) or tick_size <= 0 or not math.isfinite(quantity_step) or quantity_step <= 0:
            continue
        result[str(item["symbol"])] = {
            "tick_size": tick_size,
            "quantity_step": quantity_step,
            "source": str(path),
            "snapshot_server_time": payload.get("serverTime"),
        }
    return result


def symbol_strategy_config(
    config: dict[str, Any], symbol: str, overrides: dict[str, Any] | None,
    execution_rules: dict[str, dict[str, Any]],
) -> tuple[Ref00Config, dict[str, Any]]:
    """Apply symbol-specific recorded exchange rules; never cross-symbol fallbacks."""
    metadata = execution_rules.get(symbol)
    if metadata is None:
        raise ValueError(f"{symbol} için yerel tick/miktar adımı bulunamadı")
    cfg = replace(
        strategy_config(config, overrides),
        tick_size=float(metadata["tick_size"]),
        quantity_step=float(metadata["quantity_step"]),
    )
    return cfg, metadata


def selected_combinations(job: dict[str, Any]) -> list[Combo]:
    categorical = job.get("categorical", {})
    exits = set(categorical.get("exit_family", ["NONE", "MOST", "RSI_SMA", "TILLSON"]))
    ers = set(categorical.get("er", ["OFF", "v1", "v2"]))
    states = set(categorical.get("winrate_state", ["OFF", "SH_WEIGHTED_HOLD", "SH_WEIGHTED_CLOSE"]))
    allowed_exits = {"NONE", "MOST", "RSI_SMA", "TILLSON", "COMBINED"}
    allowed_er = {"OFF", "v1", "v2"}
    allowed_states = {"OFF", "SH_WEIGHTED_HOLD", "SH_WEIGHTED_CLOSE"}
    if not exits <= allowed_exits or not ers <= allowed_er or not states <= allowed_states:
        raise ValueError("İş emrinde desteklenmeyen kategorik seçenek var")
    result = []
    base=independent_combinations()
    if "COMBINED" in exits:
        base += [replace(c,exit_family="COMBINED") for c in base if c.exit_family=="NONE"]
    for combo in base:
        state = "OFF" if not combo.winrate_enabled else "SH_WEIGHTED_CLOSE" if combo.close_on_block else "SH_WEIGHTED_HOLD"
        if combo.exit_family in exits and combo.er in ers and state in states:
            result.append(combo)
    return result


def historical_universe_masks(prepared: dict[str, pd.DataFrame], spec: dict[str, Any] | None) -> dict[str, pd.Series | None]:
    """Create causal Top-N entry masks from information known at each bar close."""
    if not spec or not spec.get("enabled"):
        return {symbol: None for symbol in prepared}
    mode = spec.get("mode")
    if mode not in {"TOP_GAINERS_24H", "TOP_VOLUME_24H", "NEW_LISTED"}:
        raise ValueError("Bilinmeyen tarihsel evren filtresi")
    top_n = int(spec.get("top_n", 20))
    if not 1 <= top_n <= 2000:
        raise ValueError("Tarihsel Top N değeri 1–2000 arasında olmalı")
    symbols = sorted(prepared)
    if not symbols:
        return {}
    if mode == "TOP_GAINERS_24H":
        values = pd.concat({symbol: prepared[symbol]["close"].astype(float) for symbol in symbols}, axis=1)
        scores = values / values.shift(48) - 1.0
    elif mode == "TOP_VOLUME_24H":
        values = pd.concat({symbol: prepared[symbol]["quote_volume"].astype(float) for symbol in symbols}, axis=1)
        scores = values.rolling(48, min_periods=48).sum()
    else:
        catalog_path = LAB_ROOT / "coin_catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"rows": []}
        onboard = {row.get("symbol"): row.get("onboard_ms") for row in catalog.get("rows", [])}
        union = sorted(set().union(*(frame.index for frame in prepared.values())))
        scores = pd.DataFrame(index=pd.DatetimeIndex(union), columns=symbols, dtype=float)
        for symbol in symbols:
            value = onboard.get(symbol)
            if value is None:
                continue
            listed = pd.Timestamp(float(value), unit="ms", tz="UTC")
            valid = (scores.index >= listed) & scores.index.isin(prepared[symbol].index)
            scores.loc[valid, symbol] = float(value)
    # Alphabetically sorted columns make equal-score selection deterministic.
    membership = scores.rank(axis=1, ascending=False, method="first") <= top_n
    return {symbol: membership[symbol].reindex(prepared[symbol].index, fill_value=False).astype(bool) for symbol in symbols}


def parameter_sets(job: dict[str, Any]) -> list[dict[str, Any]]:
    grid = {**job.get("parameter_grid", {}), **job.get("gate_parameter_grid", {})}
    if not grid:
        return [{}]
    supported = {
        "commission_pct_per_order", "stop_loss_pct", "trailing_activation_pct",
        "trailing_distance_pct", "er_length", "er_threshold", "er_persist_bars",
        "winrate_window", "winrate_min_trades", "winrate_threshold",
        "winrate_block_before_min", "cash_per_order", "initial_capital",
    }
    supported |= set(GATE_PARAMETER_DEFAULTS)
    unknown = set(grid) - supported
    if unknown:
        raise ValueError(f"Desteklenmeyen parametreler: {', '.join(sorted(unknown))}")
    keys = sorted(grid)
    values = [items if isinstance(items, list) else [items] for items in (grid[key] for key in keys)]
    if any(not items for items in values):
        raise ValueError("Parametre listesi boş olamaz")
    count = math.prod(len(items) for items in values)
    if count > int(job.get("max_runs", 1000)):
        raise ValueError("Parametre kombinasyonları güvenlik sınırını aşıyor")
    for key, items in zip(keys, values):
        for item in items:
            validate_component(key,item)
            if key in TEXT_FIELDS:
                if not isinstance(item, str) or not re.fullmatch(r"[A-Z0-9_]+:[A-Z0-9_.]+", item):
                    raise ValueError(f"{key}: EXCHANGE:SYMBOL required")
            elif key in FLOATS:
                if isinstance(item,bool) or not isinstance(item,(int,float)) or not math.isfinite(item):
                    raise ValueError(f"{key}: finite number required")
            elif key in ROUTING_CHOICES:
                if item not in ROUTING_CHOICES[key]:
                    raise ValueError(f"{key}: unsupported choice {item!r}")
            elif key in GATE_BOOL_FIELDS:
                if not isinstance(item, bool):
                    raise ValueError(f"{key}: true/false gerekli")
            elif key in GATE_INT_FIELDS:
                minimum = 0 if key in {"vwap_band_bps","pmax_tf_minutes"} else 1
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or int(item) != item or int(item) < minimum:
                    raise ValueError(f"{key}: {'sıfır veya daha büyük' if minimum == 0 else 'en az 1'} tam sayı gerekli")
            elif key == "g7_level":
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                    raise ValueError("g7_level: finite number required")
            elif key == "most_percent":
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 <= float(item) < 100:
                    raise ValueError("most_percent: 0 ile 100 arasında olmalı")
            elif key == "tillson_factor":
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0.1 <= float(item) <= 5:
                    raise ValueError("tillson_factor: kaynak aralığı 0.1 ile 5")
            else:
                validate_parameters({key: item})
    return [{key:int(value) if key in GATE_INT_FIELDS else value for key,value in zip(keys,combination)}
            for combination in itertools.product(*values)]


def split_parameter_overrides(overrides: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep strategy inputs and gate inputs in one immutable test row, but apply them separately."""
    return (
        {key: value for key, value in overrides.items() if key not in GATE_PARAMETER_DEFAULTS},
        {**GATE_PARAMETER_DEFAULTS, **{key: value for key, value in overrides.items() if key in GATE_PARAMETER_DEFAULTS}},
    )


def external_gate_series(bars: pd.DataFrame, gate_values: dict[str, Any], *, feeds=None, chart_symbol=None, calculation_bars=None, trace_sink=None) -> tuple[pd.Series | None, dict[str, Any]]:
    """Evaluate versioned research gates, with explicit source/consumer clocks.

    EXPLICIT uses producers + Master state + EXT consumers. LEGACY retains the
    earlier isolated AND model. Neither mode establishes TradingView parity.
    """
    gate_values = {**GATE_PARAMETER_DEFAULTS, **gate_values}
    if gate_values["routing_mode"] == "EXPLICIT":
        return explicit_external_gate_series(bars, gate_values, feeds=feeds, chart_symbol=chart_symbol, calculation_bars=calculation_bars,trace_sink=trace_sink)
    if any(gate_values[key] for key in ("core_enabled","g3_enabled","g4_enabled","g5_enabled","pmax_enabled",
                                        "g6_enabled","g7_enabled","macro_enabled","ext2_enabled")):
        raise ValueError("Selected Master components require EXPLICIT routing; LEGACY will not silently ignore them")
    active: dict[str, pd.Series] = {}
    if gate_values["vwap_enabled"]:
        score = vwap_ext_score(
            bars,
            VwapGateConfig(
                use_daily=gate_values["vwap_use_daily"], use_weekly=gate_values["vwap_use_weekly"],
                mode=gate_values["vwap_mode"], daily_source=gate_values["vwap_daily_source"],
                weekly_source=gate_values["vwap_weekly_source"],
                band_bps=int(gate_values["vwap_band_bps"]),
                persist_bars=int(gate_values["vwap_persist_bars"]),
            ),
        )["ext_score"]
        wiring = "MASTER_GATE_G6" if gate_values["vwap_via_g6"] else "DIRECT"
        active["VWAP"] = strategy_gate_open(score, wiring).reindex(bars.index).fillna(False).astype(bool)
    if gate_values["most_enabled"]:
        active["MOST"] = causal_most_gate1(
            bars, length=int(gate_values["most_length"]), percent=float(gate_values["most_percent"]),
        )["strategy_gate_open"].reindex(bars.index).fillna(False).astype(bool)
    if gate_values["tillson_enabled"]:
        active["TILLSON"] = confirmed_tillson_gate2(
            bars, length=int(gate_values["tillson_length"]), factor=float(gate_values["tillson_factor"]),
        )["strategy_gate_open"].reindex(bars.index).fillna(False).astype(bool)
    if not active:
        return None, {"active_gate_models": [], "combination": "NO_EXTERNAL_GATE"}
    combined = pd.concat(active, axis=1).all(axis=1).astype(bool)
    return combined, {
        "active_gate_models": list(active),
        "combination": "AND",
        "gate_open_share": float(combined.mean()),
    }


def causal_calculation_to_decision(source: pd.Series | pd.DataFrame,
                                   decision_index: pd.DatetimeIndex, *,
                                   allow_uncovered_prefix: bool = False) -> pd.Series | pd.DataFrame:
    """Sample a calculation-feed plot at each execution bar's close.

    The calculation feed is an LTF chart for an independent Pine producer.  A
    30m decision at ``t`` closes after the source bars ``t .. t+29m``.  Its
    last *confirmed* calculation value is therefore at ``t+29m`` (for a 1m
    feed), not at ``t``.  This intentionally does not shift the value: G6 and
    EXT consumers apply their own source-code ``[1]`` delays later.

    Refusing incomplete/misaligned buckets is safer than silently using an
    earlier value, which would make the research clock ambiguous.  A caller
    may explicitly fail-close an *uncovered prefix* when the decision history
    includes pre-job warm-up bars but the independently bound producer feed
    intentionally starts at the job boundary.  A missing suffix is never
    permitted.
    """
    if not isinstance(source.index, pd.DatetimeIndex) or source.index.tz is None:
        raise ValueError("Calculation producer requires timezone-aware timestamps")
    if not isinstance(decision_index, pd.DatetimeIndex) or decision_index.tz is None:
        raise ValueError("Decision bars require timezone-aware timestamps")
    if len(source.index) < 2 or len(decision_index) < 2:
        raise ValueError("Calculation-to-decision alignment requires at least two bars")
    source_step = source.index[1] - source.index[0]
    decision_step = decision_index[1] - decision_index[0]
    if source_step <= pd.Timedelta(0) or decision_step <= source_step or decision_step % source_step:
        raise ValueError("Decision timeframe must be an integer multiple of calculation timeframe")
    if not source.index.is_unique or not source.index.is_monotonic_increasing:
        raise ValueError("Calculation producer timestamps must be unique and ordered")
    if not decision_index.is_unique or not decision_index.is_monotonic_increasing:
        raise ValueError("Decision timestamps must be unique and ordered")
    if not (source.index.to_series().diff().dropna() == source_step).all():
        raise ValueError("Calculation producer has missing or irregular timestamps")
    if not (decision_index.to_series().diff().dropna() == decision_step).all():
        raise ValueError("Decision bars have missing or irregular timestamps")
    last_confirmed = decision_index + decision_step - source_step
    mapped = source.reindex(last_confirmed)
    missing_rows = mapped.isna().any(axis=1) if isinstance(mapped, pd.DataFrame) else mapped.isna()
    if missing_rows.any() and allow_uncovered_prefix:
        uncovered_prefix = last_confirmed < source.index.min()
        if (missing_rows & ~uncovered_prefix).any():
            raise ValueError("Calculation feed does not cover every decision bar close")
        # There is no producer state before its explicit feed contract begins.
        # Zero/False is a closed gate, never a synthetic passing signal.
        mapped = mapped.copy()
        if isinstance(mapped, pd.DataFrame):
            mapped.loc[missing_rows, :] = 0
        else:
            mapped.loc[missing_rows] = 0
        missing_rows = mapped.isna().any(axis=1) if isinstance(mapped, pd.DataFrame) else mapped.isna()
    missing = missing_rows.any()
    if missing:
        raise ValueError("Calculation feed does not cover every decision bar close")
    mapped.index = decision_index
    return mapped


def explicit_external_gate_series(bars, values, *, feeds=None, chart_symbol=None, calculation_bars=None,trace_sink=None):
    """Resolve current-bar plots, then apply each consumer's [1] once.

    Source-live lookahead is opt-in and marked unsafe for performance evidence.
    Missing feeds raise errors, never fabricated open gates.
    """
    calculation_bars = bars if calculation_bars is None else calculation_bars
    topology_warnings = audit_explicit_topology(values, bars.index, calculation_bars.index)
    cache = {"close": bars.close}
    models = []
    def resolve(name):
        if name in cache:
            return cache[name]
        if name == "VWAP":
            producer = vwap_ext_score(bars, VwapGateConfig(
                use_daily=values["vwap_use_daily"], use_weekly=values["vwap_use_weekly"],
                mode=values["vwap_mode"], band_bps=values["vwap_band_bps"],
                persist_bars=values["vwap_persist_bars"], daily_source=values["vwap_daily_source"],
                weekly_source=values["vwap_weekly_source"]))
            cache[name]=producer["ext_score"]
            if trace_sink is not None: trace_sink.update({f"vwap_{k}":producer[k] for k in producer})
            models.append("VWAP")
        elif name == "BIAS":
            if not chart_symbol:
                raise ValueError("BIAS chart symbol identity required")
            producer=bias_gate(bars, values, feeds or {}, chart_symbol)
            cache[name] = producer["gate_output"]
            if trace_sink is not None: trace_sink.update({f"bias_{k}":producer[k] for k in producer})
            models.append("BIAS_PARITY_PENDING")
        elif name == "HL":
            # Source hl gate.pine requires Chart < MTF < HTF.  In a 30m
            # strategy job its default 15m/60m producer must consequently run
            # on the finer calculation feed, then be sampled only at each
            # execution bar close.  Do not execute it on the 30m gate history.
            producer = hl_gate(calculation_bars, left=values["hl_left"], right=values["hl_right"],
                mtf_minutes=values["hl_mtf_minutes"], htf_minutes=values["hl_htf_minutes"],
                activation=values["hl_activation"], confirmation=values["hl_confirmation"],
                close_source=values["hl_close_source"], close_mode=values["hl_close_mode"])
            # A 5m HL chart can itself be the decision chart.  Its source
            # already exposes only confirmed MTF/HTF primitives via [1], so
            # applying calculation-to-decision sampling again would both fail
            # (equal steps) and introduce an artificial delay.
            if not producer.index.equals(bars.index):
                producer_step = producer.index[1] - producer.index[0]
                decision_step = bars.index[1] - bars.index[0]
                if producer_step == decision_step:
                    # Same chart clock with a longer warm-up prefix: alignment
                    # is a direct timestamp selection, not an LTF→HTF sample.
                    producer = producer.reindex(bars.index)
                    if producer.isna().any(axis=None):
                        raise ValueError("HL same-clock producer does not cover every decision bar")
                else:
                    producer = causal_calculation_to_decision(producer, bars.index,
                        allow_uncovered_prefix=True)
            cache[name]=producer["gate_output"]
            if trace_sink is not None: trace_sink.update({f"hl_{k}":producer[k] for k in producer})
            models.append("HL_CALCULATION_FEED_CAUSAL_PARITY_PENDING")
        elif name == "MASTER_GATE":
            auxiliary = {}
            core_set = core_reset = None
            if values["core_enabled"] or values["g3_enabled"] or values["g4_enabled"]:
                n=values["master_tf_minutes"]
                computed=confirmed(core_matrix(aggregate(calculation_bars,n),values),bars.index,n)
                if values["g3_enabled"]: auxiliary["G3"]=computed.g3.fillna(False).astype(bool)
                if values["g4_enabled"]: auxiliary["G4"]=computed.g4.fillna(False).astype(bool)
                if values["core_enabled"]:
                    core_set=computed.core_set.fillna(False).astype(bool)
                    core_reset=~computed.st_bull.fillna(False).astype(bool)
                    if values["core_hysteresis"]:
                        core_reset |= (bars.close<computed.exit_threshold)&(computed.slope<0)
                models.append("CORE_G3_G4_PARITY_PENDING")
            if values["pmax_enabled"]:
                n=values["pmax_tf_minutes"] or values["master_tf_minutes"]
                auxiliary["G8"]=confirmed(pmax_matrix(aggregate(calculation_bars,n),values).state,bars.index,n).fillna(False).astype(bool)
                models.append("PMAX_PARITY_PENDING")
            if values["g6_enabled"]:
                auxiliary["G6"] = g6_pass(resolve(values["g6_source"]), values["g6_mode"])
            for gate,enabled,n,length,percent in (
                ("G1",values["most_enabled"],values["g1_tf_minutes"],values["most_length"],values["most_percent"]),
                ("G5",values["g5_enabled"],values["g5_tf_minutes"],values["most2_length"],values["most2_percent"])):
                if enabled:
                    requested=aggregate(calculation_bars,n)
                    line=most_line(requested.close,length,percent)
                    if values["line_timing"]!="SOURCE_HISTORICAL_LOOKAHEAD":
                        line=line.shift(1)
                    auxiliary[gate]=bars.close.gt(line.reindex(bars.index,method="ffill"))
                    models.append(gate+"_"+values["line_timing"])
            if values["tillson_enabled"]:
                n=values["g2_tf_minutes"]
                requested=aggregate(calculation_bars,n)
                line=t3_line(requested.close,values["tillson_length"],values["tillson_factor"])
                if values["tillson_execution"]=="Confirmed HTF":
                    raw=requested.close>line if values["tillson_mode"]=="Price vs T3" else line>line.shift(1).fillna(line)
                    auxiliary["G2"]=confirmed(raw,bars.index,n).fillna(False).astype(bool)
                    models.append("G2_CONFIRMED")
                else:
                    if values["line_timing"]!="SOURCE_HISTORICAL_LOOKAHEAD": line=line.shift(1)
                    mapped=line.reindex(bars.index,method="ffill")
                    auxiliary["G2"]=bars.close>mapped if values["tillson_mode"]=="Price vs T3" else mapped>mapped.shift(1).fillna(mapped)
                    models.append("G2_LIVE_CROSS_"+values["line_timing"])
            macro_reset = None
            if values["macro_enabled"]:
                n=values["macro_minutes"]
                requested=aggregate(calculation_bars,n)
                armor=confirmed(ema(requested.close,values["macro_length"]),bars.index,n).fillna(bars.close.shift(1).rolling(200).mean())
                macro_reset=bars.close.lt(armor).fillna(False)
                if core_set is not None:
                    core_set &= bars.close>armor
            g7_sma = price_source(bars, values["g7_source"]).rolling(values["g7_length"]).mean() if values["g7_enabled"] else None
            state = master_latch(bars.index, auxiliary, core_set=core_set,core_reset=core_reset,macro_reset=macro_reset,
                g7_sma=g7_sma, g7_level=values["g7_level"], g7_mode=values["g7_mode"],
                g7_reset_on_fail=values["g7_reset_on_fail"],
                g7_reset_each_day=values["g7_reset_each_day"])
            cache[name]=state["master_output"]
            if trace_sink is not None:
                trace_sink.update({key if key.startswith("master_") else f"master_{key}":state[key] for key in state})
                trace_sink.update({f"{key}_pass":series for key,series in auxiliary.items()})
                if core_set is not None: trace_sink["core_set_qualified"]=core_set
                if core_reset is not None: trace_sink["core_reset"]=core_reset
                if macro_reset is not None: trace_sink["macro_reset"]=macro_reset
            models.append("MASTER_GATE_PARITY_PENDING")
        else:
            raise ValueError(f"{name}: producer not implemented; refusing substituted gate results")
        return cache[name]
    ext1 = resolve(values["ext1_source"]) if values["ext1_enabled"] else None
    ext2 = resolve(values["ext2_source"]) if values["ext2_enabled"] else None
    trace = strategy_external(bars.index, ext1, ext2, ExternalRouting(
        mode=values["ext_mode"], use_ext1=values["ext1_enabled"], use_ext2=values["ext2_enabled"],master_enabled=values["strategy_master_enabled"]))
    result = trace["is_ext_open"]
    if trace_sink is not None:
        trace_sink.update({f"plot_{key}":series for key,series in cache.items()})
        trace_sink.update({key:trace[key] for key in trace})
    return result, {"active_gate_models": models, "combination": values["ext_mode"],
                    "gate_open_share": float(result.mean()), "routing_version": 2,
                    "parity_status": "PINE_PARITY_PENDING",
                    "lookahead_risk": False,
                    "topology_warnings": topology_warnings}


def checked_bars(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, interval: str):
    """Use available coverage inside the request; reject internal corruption/gaps."""
    if interval not in RESEARCH_DECISION_INTERVALS:
        raise ValueError("Araştırma karar motoru şu an yalnız 5m veya 30m için kalibre edilmiştir")
    if start >= end:
        raise ValueError("Başlangıç bitişten önce olmalı")
    selected = bars[(bars.index >= start) & (bars.index < end)]
    quality = quality_report(selected, interval).to_dict()
    issues = []
    minutes = interval_minutes(interval)
    expected = pd.date_range(start, end, freq=pd.Timedelta(minutes=minutes), inclusive="left")
    if len(selected.index.difference(expected)):
        issues.append("Mum zamanları uyumsuz")
    for key in ("conflicting_duplicate_timestamps", "out_of_order_rows", "missing_intervals", "nonpositive_prices", "negative_volume_rows", "invalid_ohlc_rows", "incomplete_last_bar"):
        if quality[key]:
            issues.append(key)
    columns = ["open", "high", "low", "close", "volume", "quote_volume"]
    if not np.isfinite(selected[columns].to_numpy(dtype=float)).all():
        issues.append("Eksik/sonsuz fiyat veya hacim")
    if len(selected) < 100:
        issues.append("Araştırma için en az 100 mum gerekli")
    delta = pd.Timedelta(minutes=minutes)
    if len(selected) and not ((selected["close_time"] >= selected.index) & (selected["close_time"] < selected.index + delta)).all():
        issues.append("Mum kapanış zamanı uyumsuz")
    quality.update(status="FAIL" if issues else "PASS", issues=issues, requested_start=start.isoformat(), requested_end_exclusive=end.isoformat(),
                   coverage="FULL" if len(selected) == len(expected) else "PARTIAL",
                   tested_start=selected.index.min().isoformat() if len(selected) else None,
                   tested_end_exclusive=(selected.index.max() + delta).isoformat() if len(selected) else None,
                   available_bars=len(selected), requested_bars=len(expected))
    return selected, quality


def local_data_readiness(config: dict[str, Any], symbols: list[str], data_root: Path,
                         start: pd.Timestamp, end: pd.Timestamp) -> tuple[list[str], list[dict[str, Any]]]:
    """Identify symbols that do not yet have usable local data for this job."""
    needed: list[str] = []
    checks: list[dict[str, Any]] = []
    for symbol, paths in discover_market_roots(data_root, symbols, config["interval"]).items():
        try:
            _, check = checked_bars(load_binance_archives(paths), start, end, config["interval"])
        except Exception as exc:
            check = {"status": "FAIL", "coverage": "NO_DATA", "available_bars": 0, "issues": [str(exc)]}
        row = {"symbol": symbol, "files": len(paths), **check}
        checks.append(row)
        # PARTIAL can mean either a newly listed coin or an archive not yet
        # downloaded locally. Try to fill it; the final backtest may still use
        # a valid partial range when Binance has no earlier history.
        if check.get("status") != "PASS" or check.get("coverage") != "FULL":
            needed.append(symbol)
    return needed, checks


def auto_download_job_data(config: dict[str, Any], job: dict[str, Any], data_root: Path,
                           output_dir: Path) -> dict[str, Any]:
    """Download only unusable/missing symbols, without failing the whole test batch."""
    output_dir.mkdir(parents=True, exist_ok=True)
    symbols = list(job.get("symbols") or config["symbols"])
    start = pd.Timestamp(job.get("start", config["download"]["start"]), tz="UTC")
    end = pd.Timestamp(job.get("end", config["download"]["end"]), tz="UTC")
    needed, before = local_data_readiness(config, symbols, data_root, start, end)
    download_config = json.loads(json.dumps(config))
    download_config["download"]["start"] = start.date().isoformat()
    download_config["download"]["end"] = end.date().isoformat()
    download_root = data_root / "binance_um" / "local_quant_lab"
    records: list[dict[str, Any]] = []
    for symbol in needed:
        print(f"Test öncesi eksik veri indiriliyor: {symbol}", flush=True)
        try:
            records.extend(download_symbol(download_config, symbol, download_root))
        except Exception as exc:
            # One unavailable/delisted symbol must not stop the remaining batch.
            records.append({"symbol": symbol, "status": "FAILED", "error": str(exc)})
            print(f"Veri indirilemedi, bu parite daha sonra atlanacak: {symbol} · {exc}", flush=True)
    still_incomplete, after = local_data_readiness(config, symbols, data_root, start, end)
    still_unusable = [row["symbol"] for row in after if row.get("status") != "PASS"]
    summary = {
        "enabled": True,
        "requested_start": start.isoformat(),
        "requested_end_exclusive": end.isoformat(),
        "symbols_checked": symbols,
        "download_requested_for": needed,
        "usable_before": [row["symbol"] for row in before if row.get("status") == "PASS"],
        "usable_after": [row["symbol"] for row in after if row.get("status") == "PASS"],
        "still_incomplete": still_incomplete,
        "still_unusable": still_unusable,
        "checks_before": before,
        "checks_after": after,
        "downloads": records,
    }
    atomic_write_json(output_dir / "automatic_data_download.json", summary)
    if needed:
        print(f"Otomatik veri kontrolü tamamlandı: {len(needed)} parite için indirme denendi; "
              f"{len(still_unusable)} parite kullanılamıyor ve yalnız onlar atlanacak.", flush=True)
    else:
        print("Otomatik veri kontrolü tamamlandı: seçili paritelerin yerel verisi hazır.", flush=True)
    return summary


def write_tradingview_comparison(job: dict[str, Any], config: dict[str, Any], rows: list[dict[str, Any]], output_dir: Path) -> dict | None:
    """Write a fail-closed comparison with a pinned Strategy Tester export.

    A matching coin alone is not evidence of parity.  We require identical
    decision timeframe, one local configuration, and matching requested
    period boundaries before showing numerical deltas.
    """
    reference = job.get("tradingview_reference")
    if not reference:
        return None
    chart = reference.get("chart", {})
    complete = [row for row in rows if row.get("status") == "COMPLETE"]
    reasons = []
    config_minutes = int(str(config.get("interval", "")).removesuffix("m")) if str(config.get("interval", "")).endswith("m") else None
    if chart.get("timeframe_minutes") != config_minutes:
        reasons.append("TIMEFRAME_MISMATCH")
    source_symbol = str(chart.get("symbol", "")).replace("BINANCE:", "").replace(".P", "")
    if len(complete) != 1 or {row.get("symbol") for row in complete} != {source_symbol}:
        reasons.append("SYMBOL_OR_CONFIGURATION_MISMATCH")
    period = reference.get("period", {})
    if not period.get("start") or not period.get("end_inclusive"):
        reasons.append("REFERENCE_PERIOD_UNKNOWN")
    else:
        # TradingView reports an inclusive final bar. The lab accepts an
        # exclusive end, so exact hour alignment remains mandatory here.
        expected_start = str(period["start"]).replace("+00:00", "Z")
        expected_end = (pd.Timestamp(period["end_inclusive"]) + pd.Timedelta(minutes=config_minutes or 0)).isoformat().replace("+00:00", "Z")
        actual_start = pd.Timestamp(job["start"], tz="UTC").isoformat().replace("+00:00", "Z")
        actual_end = pd.Timestamp(job["end"], tz="UTC").isoformat().replace("+00:00", "Z")
        if (actual_start, actual_end) != (expected_start, expected_end):
            reasons.append("PERIOD_BOUNDARY_MISMATCH")
    comparison = {
        "status": "COMPARABLE" if not reasons else "NOT_COMPARABLE",
        "reasons": reasons,
        "reference": reference,
        "local_job": {"id": job.get("id"), "interval": config.get("interval"), "start": job.get("start"), "end_exclusive": job.get("end")},
    }
    if not reasons:
        local = complete[0]
        tv = reference.get("performance", {}) | reference.get("trade_statistics", {}) | reference.get("risk_statistics", {})
        local_metrics = {
            "net_profit_usdt": local.get("total_pnl_usdt"), "total_trades": local.get("trades"),
            "win_rate_pct": local.get("win_rate_pct"), "profit_factor": local.get("profit_factor"),
        }
        comparison["local_metrics"] = local_metrics
        comparison["deltas_local_minus_tradingview"] = {
            key: (None if local_metrics.get(key) is None or tv.get(key) is None else float(local_metrics[key]) - float(tv[key]))
            for key in local_metrics
        }
    atomic_write_json(output_dir / "tradingview_comparison.json", comparison)
    return comparison


def run_job(config: dict[str, Any], job: dict[str, Any], data_root: Path, output_dir: Path, progress=None) -> list[dict[str, Any]]:
    symbols = list(job.get("symbols") or config["symbols"])
    if config.get("market") != "futures/um":
        raise ValueError("Yalnız Binance USD-M vadeli piyasa destekleniyor")
    if len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z0-9]{1,30}(USDT|USDC)", symbol) for symbol in symbols):
        raise ValueError("Tekrarsız ve geçerli USD-M pariteleri seçin")
    execution_data_policy = job.get("execution_data_policy", "NATIVE_RECONCILED")
    if execution_data_policy not in EXECUTION_DATA_POLICIES:
        raise ValueError(f"Bilinmeyen execution_data_policy: {execution_data_policy}")
    if config["interval"] not in RESEARCH_DECISION_INTERVALS:
        raise ValueError("İş karar zaman dilimi yalnız 5m veya 30m olabilir")
    decision_minutes = interval_minutes(config["interval"])
    combos = selected_combinations(job)
    parameters = parameter_sets(job)
    planned = len(symbols) * len(combos) * len(parameters)
    cap = int(job.get("max_runs", 1000))
    if planned < 1 or cap < 1:
        raise ValueError("En az bir koşu gerekli")
    if planned > cap:
        raise ValueError(f"Planlanan {planned} koşu güvenlik sınırı {cap} değerini aşıyor")
    for overrides in parameters:
        strategy_overrides, _ = split_parameter_overrides(overrides)
        strategy_config(config, strategy_overrides)
    output_dir.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(job.get("start", config["download"]["start"]), tz="UTC")
    end = pd.Timestamp(job.get("end", config["download"]["end"]), tz="UTC")
    automatic_download = None
    if job.get("auto_download_missing", False):
        automatic_download = auto_download_job_data(config, job, data_root, output_dir)
    discovered = discover_market_roots(data_root, symbols, config["interval"])
    execution_rules = load_execution_rules()
    prepared = {}
    calculation_history = {}
    gate_feeds, gate_feed_evidence = load_feed_manifest(job.get("gate_feed_manifest"))
    if job.get("gate_feed_snapshot") is not None and gate_feed_evidence != job["gate_feed_snapshot"]:
        raise ValueError("Gate feeds changed after job creation; create a new job")
    checks = []
    data_files = []
    for symbol, paths in discovered.items():
        try:
            history = load_binance_archives(paths)
            native_bars, native_check = checked_bars(history, start, end, config["interval"])
            chart_id = f"BINANCE:{symbol}.P"
            execution_history = history
            if execution_data_policy == "CALCULATION_1M_DERIVED":
                if chart_id not in gate_feeds:
                    raise ValueError(f"Calculation-derived execution için doğrulanmış 1m chart feed yok: {chart_id}")
                derived_ohlcv = aggregate(gate_feeds[chart_id], decision_minutes)
                # Preserve ancillary native exchange columns (quote volume,
                # trade count, etc.) while replacing only the five execution
                # OHLCV fields with their verified 1m aggregation.
                execution_history = history.reindex(derived_ohlcv.index).copy()
                if execution_history.isna().all(axis=1).any():
                    raise ValueError(f"Native ancillary execution rows missing for derived feed: {chart_id}")
                execution_history[["open", "high", "low", "close", "volume"]] = derived_ohlcv[["open", "high", "low", "close", "volume"]]
                # checked_bars validates candle completion using close_time.
                # The pinned gate feed is OHLCV-only, so derive the canonical
                # close timestamp without altering either source dataset.
                execution_history["close_time"] = execution_history.index + pd.Timedelta(minutes=decision_minutes) - pd.Timedelta(milliseconds=1)
                bars, check = checked_bars(execution_history, start, end, config["interval"])
                check["native_execution_check"] = native_check
                check["execution_source"] = "CALCULATION_1M_DERIVED"
                check["execution_source_warning"] = (
                    "Native 30m ile verified 1m aggregation uyuşmadığı için giriş/fill mumları "
                    "verified 1m feed'den türetildi; research-only, Pine parity pending"
                )
            else:
                bars, check = native_bars, native_check
                check["execution_source"] = "NATIVE_RECONCILED"
            _, metadata = symbol_strategy_config(config, symbol, None, execution_rules)
            check.update(
                tick_size=metadata["tick_size"],
                quantity_step=metadata["quantity_step"],
                execution_metadata_source=metadata["source"],
                execution_metadata_snapshot_server_time=metadata["snapshot_server_time"],
                execution_metadata_temporal_status="END_SNAPSHOT_NOT_HISTORICAL",
            )
            if check["status"] == "PASS":
                prepared[symbol] = bars
                calculation_history[symbol] = execution_history.loc[execution_history.index<end]
            data_files.extend({"symbol": symbol, "path": str(path), "sha256": file_digest(path)} for path in paths)
        except Exception as exc:
            check = {"status": "FAIL", "issues": [str(exc)]}
        checks.append({"symbol": symbol, **check})
    universe_spec = job.get("historical_universe")
    atomic_write_json(output_dir / "gate_source_manifest.json", {
        "pine_sources": {name:file_digest(PROJECT_ROOT / "sources" / name) for name in
            ("master.pine","master gate.pine","bias.pine","hl gate.pine","vwap gate.pine")},
        "gate_feeds":gate_feed_evidence,
        "execution_data_policy": execution_data_policy,
        "execution_data_warning": (
            "CALCULATION_1M_DERIVED: native 30m reconciliation remains fail-closed; this opt-in job uses "
            "30m bars derived from the pinned 1m calculation feed" if execution_data_policy == "CALCULATION_1M_DERIVED" else None
        ),
        "status":"RESEARCH_PINE_PARITY_PENDING"})
    atomic_write_json(output_dir / "data_preflight.json", {"checks": checks, "files": data_files,
                                                            "historical_universe": universe_spec,
                                                            "automatic_download": automatic_download,
                                                            "requested_symbols": len(symbols), "usable_symbols": len(prepared),
                                                            "execution_data_policy": execution_data_policy})
    failed = [row for row in checks if row["status"] != "PASS"]
    universe_masks = historical_universe_masks(prepared, universe_spec)
    rows: list[dict[str, Any]] = []
    for check in failed:
        print(f"Atlandı: {check['symbol']}: {check['issues']}", flush=True)
        for parameter_number, overrides in enumerate(parameters, start=1):
            for combo in combos:
                rows.append({"job_id": job["id"], "symbol": check["symbol"], "parameter_id": f"P{parameter_number:04d}",
                             "combo_id": combo.combo_id, "status": "SKIPPED", "skip_reason": "; ".join(check["issues"]),
                             "engine_status": "RESEARCH_APPROXIMATION", "production_eligible": False,
                             "historical_universe_mode": (universe_spec or {}).get("mode", "OFF"),
                             "historical_universe_requested_symbols": len(symbols),
                             "historical_universe_usable_symbols": len(prepared)})
    for symbol, bars in prepared.items():
        conditions = build_conditions(bars)
        for parameter_number, overrides in enumerate(parameters, start=1):
            strategy_overrides, gate_values = split_parameter_overrides(overrides)
            cfg, metadata = symbol_strategy_config(config, symbol, strategy_overrides, execution_rules)
            chart_id=f"BINANCE:{symbol}.P"
            feeds={**gate_feeds}
            feeds.setdefault(chart_id,calculation_history[symbol])
            calculation=feeds[chart_id]
            calculation=calculation.loc[calculation.index<end]
            gate_history=calculation_history[symbol]
            gate_trace={} if job.get("save_gate_trace",False) else None
            if chart_id in gate_feeds:
                reconciled=aggregate(calculation,decision_minutes).reindex(bars.index)
                columns=["open","high","low","close","volume"]
                if not np.allclose(reconciled[columns].to_numpy(),bars[columns].to_numpy(),rtol=1e-7,atol=1e-10):
                    if execution_data_policy == "NATIVE_RECONCILED":
                        raise ValueError(f"Calculation feed disagrees with execution candles: {chart_id}")
                    raise ValueError(f"Calculation-derived execution construction mismatch: {chart_id}")
            # EXPLICIT must consume producer plots at the Strategy decision chart.
            # Passing the 1m calculation feed here wrongly applied EXT/G6 [1]
            # delays on the producer clock rather than the Strategy chart clock.
            external_gate, gate_metadata = external_gate_series(bars, gate_values,
                feeds=feeds,chart_symbol=chart_id,calculation_bars=calculation,trace_sink=gate_trace)
            if external_gate is not None:
                external_gate=external_gate.reindex(bars.index)
                if external_gate.isna().any(): raise ValueError("Gate output missing execution timestamps")
                gate_metadata["gate_open_share"]=float(external_gate.mean())
            run_conditions=exit_conditions(bars,calculation,gate_values) if gate_values["exit_model"]=="SOURCE_MTF" else conditions
            if any(c.exit_family=="COMBINED" for c in combos):
                combined=pd.Series(False,index=bars.index)
                for family,key in (("MOST","exit_use_most"),("RSI_SMA","exit_use_rsi"),("TILLSON","exit_use_t3")):
                    if gate_values[key]: combined |= run_conditions[family]
                run_conditions={**run_conditions,"COMBINED":combined}
            parameter_id = f"P{parameter_number:04d}"
            if gate_trace:
                pd.DataFrame(gate_trace).reindex(bars.index).to_csv(output_dir/f"{symbol}_{parameter_id}_gate_trace.csv.gz",compression="gzip",index_label="time")
            for combo in combos:
                combo=replace(combo,winrate_source=combo.winrate_source if gate_values["wr_source"]=="FOLLOW_COMBO" else gate_values["wr_source"],
                    winrate_mode=combo.winrate_mode if gate_values["wr_mode"]=="FOLLOW_COMBO" else gate_values["wr_mode"])
                ledger, summary = simulate(
                    bars, run_conditions[combo.exit_family], combo,
                    base_config=cfg, last_bar_is_incomplete=False,
                    entry_allowed=universe_masks.get(symbol),
                    external_gate_open=external_gate,
                    external_gate_closes_position=bool(gate_values["external_gate_closes_position"]),
                    master_enabled=gate_values["strategy_master_enabled"],
                    daily_entry_limit=gate_values["daily_max_entries"] if gate_values["daily_enabled"] else None,
                    use_stop=gate_values["strategy_sl_enabled"],use_trail=gate_values["strategy_trail_enabled"],
                    take_profit_pct=gate_values["strategy_tp_pct"] if gate_values["strategy_tp_enabled"] else None,
                )
                ledger_name = f"ledgers/{symbol}_{parameter_id}_{hashlib.sha256(combo.combo_id.encode()).hexdigest()[:16]}.csv"
                (output_dir / "ledgers").mkdir(exist_ok=True)
                ledger.reindex(columns=["trade_number", "entry_time", "entry_price", "exit_time", "exit_price", "quantity", "gross_pnl", "commission", "net_pnl", "return_pct", "exit_reason", "exit_decision_time"]).to_csv(output_dir / ledger_name, index=False)
                flat = {k: v for k, v in summary.items() if k != "exit_reason_counts"}
                flat["trade_ledger"] = ledger_name
                flat.update({
                    "job_id": job["id"], "symbol": symbol, "parameter_id": parameter_id,
                    "parameter_overrides": json.dumps(overrides, sort_keys=True),
                    "strategy_parameter_overrides": json.dumps(strategy_overrides, sort_keys=True),
                    "gate_parameter_overrides": json.dumps(gate_values, sort_keys=True),
                    "active_gate_models": "+".join(gate_metadata["active_gate_models"]) or "NONE",
                    "gate_combination": gate_metadata["combination"],
                    "gate_open_share": gate_metadata.get("gate_open_share", 1.0),
                    "gate_routing_version": gate_metadata.get("routing_version", 0),
                    "gate_parity_status": gate_metadata.get("parity_status", "LEGACY_APPROXIMATION"),
                    "gate_topology_warnings": " | ".join(gate_metadata.get("topology_warnings", [])),
                    "gate_lookahead_risk": gate_metadata.get("lookahead_risk", False) or (gate_values["exit_model"]=="SOURCE_MTF" and gate_values["exit_lookahead"]=="ON"),
                    "status": "COMPLETE", "engine_status": "RESEARCH_APPROXIMATION",
                    "production_eligible": False,
                    "execution_data_policy": execution_data_policy,
                    "execution_data_warning": (
                        "CALCULATION_1M_DERIVED; research-only, Pine parity pending"
                        if execution_data_policy == "CALCULATION_1M_DERIVED" else ""
                    ),
                    "tick_size": metadata["tick_size"],
                    "quantity_step": metadata["quantity_step"],
                    "execution_metadata_source": metadata["source"],
                    "execution_metadata_temporal_status": "END_SNAPSHOT_NOT_HISTORICAL",
                    "historical_universe_mode": (universe_spec or {}).get("mode", "OFF"),
                    "historical_universe_top_n": (universe_spec or {}).get("top_n"),
                    "historical_universe_entry_only": True,
                    "historical_universe_requested_symbols": len(symbols),
                    "historical_universe_usable_symbols": len(prepared),
                    "historical_universe_survivorship_status": "CURRENT_ACTIVE_CATALOG_BIASED" if universe_spec else "NOT_APPLICABLE",
                })
                check = next(item for item in checks if item["symbol"] == symbol)
                flat.update({key: check[key] for key in ("requested_start", "requested_end_exclusive", "tested_start", "tested_end_exclusive", "available_bars", "requested_bars", "coverage")})
                for parameter_name, parameter_value in overrides.items():
                    flat[f"parameter__{parameter_name}"] = parameter_value
                rows.append(flat)
            pd.DataFrame(rows).to_csv(output_dir / "job_results_checkpoint.csv", index=False)
            if progress:
                progress(len(rows), planned)
        print(f"İş paketi sembolü tamamlandı: {symbol}", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(output_dir / "job_results.csv", index=False)
    write_tradingview_comparison(job, config, rows, output_dir)
    return rows


def run_queued_job(
    config_path: Path, queue_path: Path, data_root: Path,
    requested_job_id: str | None = None,
) -> Path:
    with file_lock(LAB_ROOT / "worker.lock", timeout=0):
        return _run_queued_job_locked(config_path, queue_path, data_root, requested_job_id)


def _run_queued_job_locked(config_path, queue_path, data_root, requested_job_id):
    def claim(queue):
        for row in queue["jobs"]:
            if row.get("status") == "RUNNING":
                row["status"] = "INTERRUPTED"
                row["error"] = "Önceki motor durdu. Eski çıktılar korunuyor; yeniden deneme yeni klasöre yazılır."
        candidates = [row for row in queue["jobs"] if (row.get("id") == requested_job_id and row.get("status") in {"READY", "FAILED", "INTERRUPTED"})] if requested_job_id else [row for row in queue["jobs"] if row.get("status") == "READY"]
        if not candidates:
            raise ValueError("Başlatılabilir iş bulunamadı")
        row = candidates[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row["id"]):
            raise ValueError("Güvensiz iş kimliği")
        row.setdefault("attempts", []).append({key: row.get(key) for key in ("status", "error", "report", "started_at_utc")})
        row.update(status="RUNNING", completed_runs=0, started_at_utc=datetime.now(timezone.utc).isoformat())
        row.pop("error", None)
        row.pop("excel_report_error", None)
        row.pop("report", None)
        row.pop("excel_report", None)
        return json.loads(json.dumps(row))
    job = update_queue(queue_path, claim)
    def persist():
        def mutate(queue):
            row = next(row for row in queue["jobs"] if row["id"] == job["id"])
            row.update(job)
        update_queue(queue_path, mutate)
    def progress(done, planned):
        job.update(completed_runs=done, planned_runs=planned)
        persist()
    job_output = LAB_ROOT / "reports" / "jobs" / job["id"] / datetime.now(timezone.utc).strftime("attempt-%Y%m%dT%H%M%S%f")
    job["output_dir"] = str(job_output)
    try:
        job_output.mkdir(parents=True, exist_ok=False)
        config = job.get("config_snapshot") or load_config(config_path)
        identity = engine_identity(LAB_ROOT)
        atomic_write_json(job_output / "run_manifest.json", {"job": job, "config": config, "engine": identity, "warmup_policy": "Indicators and Shadow start at first available bar within requested period; no pre-period warmup; research only"})
        validate_engine(job, identity)
        rows = run_job(config, job, data_root, job_output, progress)
        if len(rows) != job.get("planned_runs") or any(row.get("status") not in {"COMPLETE", "SKIPPED"} for row in rows):
            raise ValueError("Tamamlanan koşular planlanan sayı ile eşleşmiyor")
        job["status"] = "COMPLETE"
        job.pop("error", None)
        job["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        job["completed_runs"] = sum(row.get("status") == "COMPLETE" for row in rows)
        job["skipped_runs"] = sum(row.get("status") == "SKIPPED" for row in rows)
        job["coverage_note"] = "Her coin istenen aralıktaki mevcut verisiyle test edilir; farklı sürelerin getirileri doğrudan karşılaştırılamaz."
        job["report"] = str(job_output / "codex_handoff.json")
        preflight_path = job_output / "data_preflight.json"
        market = [{**row, "status": "OK" if row["status"] == "PASS" else "FAIL"} for row in json.loads(preflight_path.read_text(encoding="utf-8"))["checks"]] if preflight_path.exists() else []
        report = write_reports(config, job_output, [], [], market, rows, [], [], universe_snapshot=job.get("universe_snapshot"))
        workbook_input = job_output / "workbook_input.json"
        workbook_input.write_text(json.dumps({"job": job, "rows": rows}, ensure_ascii=False, indent=2, default=jsonable) + "\n", encoding="utf-8")
        node = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
        builder = LAB_ROOT / "build_job_workbook.mjs"
        excel_report = job_output / "test_sonuclari.xlsx"
        if node.exists() and builder.exists() and (LAB_ROOT / "node_modules" / "@oai" / "artifact-tool").exists():
            try:
                subprocess.run([str(node), str(builder), str(workbook_input), str(excel_report)], cwd=LAB_ROOT, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
                if not excel_report.is_file():
                    raise RuntimeError("Excel üreticisi sonuç dosyasını oluşturmadı")
                job["excel_report"] = str(excel_report)
            except Exception as workbook_exc:
                job["excel_report_error"] = str(workbook_exc)
        else:
            job["excel_report_error"] = "Artifact Tool çalışma ortamı bulunamadı; CSV ve JSON raporları üretildi"
    except Exception as exc:
        job["status"] = "FAILED"
        job["error"] = str(exc)
        raise
    finally:
        persist()
    return report


def run_local_matrix(
    config: dict[str, Any], data_root: Path, output_dir: Path,
    symbols_override: list[str] | None = None,
) -> list[dict[str, Any]]:
    symbols = symbols_override or (config["symbols"] + config.get("calibration_only", []))
    discovered = discover_market_roots(data_root, symbols, config["interval"])
    execution_rules = load_execution_rules()
    rows: list[dict[str, Any]] = []
    ledgers = output_dir / "ledgers"
    ledgers.mkdir(parents=True, exist_ok=True)
    for symbol, paths in discovered.items():
        start = pd.Timestamp(config["download"]["start"], tz="UTC")
        end = pd.Timestamp(config["download"]["end"], tz="UTC")
        try:
            bars, check = checked_bars(load_binance_archives(paths), start, end, config["interval"])
            cfg, metadata = symbol_strategy_config(config, symbol, None, execution_rules)
            check.update(
                tick_size=metadata["tick_size"],
                quantity_step=metadata["quantity_step"],
                execution_metadata_source=metadata["source"],
                execution_metadata_snapshot_server_time=metadata["snapshot_server_time"],
                execution_metadata_temporal_status="END_SNAPSHOT_NOT_HISTORICAL",
            )
        except Exception as exc:
            check = {"status": "FAIL", "issues": [str(exc)]}
        atomic_write_json(output_dir / f"preflight_{symbol}.json", check)
        if check["status"] != "PASS":
            rows.append({"symbol": symbol, "status": "SKIPPED", "skip_reason": "; ".join(check['issues']), "production_eligible": False})
            continue
        conditions = build_conditions(bars)
        for combo in independent_combinations():
            ledger, summary = simulate(
                bars, conditions[combo.exit_family], combo,
                base_config=cfg, last_bar_is_incomplete=False,
            )
            summary["symbol"] = symbol
            summary["engine_status"] = "RESEARCH_APPROXIMATION"
            summary["production_eligible"] = False
            summary["source_bars"] = len(bars)
            summary["status"] = "COMPLETE"
            summary["tick_size"] = metadata["tick_size"]
            summary["quantity_step"] = metadata["quantity_step"]
            summary["execution_metadata_source"] = metadata["source"]
            summary["execution_metadata_snapshot_server_time"] = metadata["snapshot_server_time"]
            summary["execution_metadata_temporal_status"] = "END_SNAPSHOT_NOT_HISTORICAL"
            summary.update({key: check[key] for key in ("requested_start", "requested_end_exclusive", "tested_start", "tested_end_exclusive", "available_bars", "requested_bars", "coverage")})
            rows.append({k: v for k, v in summary.items() if k != "exit_reason_counts"})
            ledger.to_csv(ledgers / f"{symbol}__{combo.combo_id}.csv", index=False)
        # Persist a symbol checkpoint immediately. Long portfolio runs can then
        # be inspected even if Windows or the terminal closes later.
        symbol_rows = [row for row in rows if row.get("symbol") == symbol]
        pd.DataFrame(symbol_rows).to_csv(output_dir / f"checkpoint_{symbol}.csv", index=False)
        print(f"Yerel backtest tamamlandı: {symbol} ({len(symbol_rows)} kombinasyon)", flush=True)
    if rows:
        frame = pd.DataFrame(rows)
        frame.to_csv(output_dir / "local_backtest_all.csv", index=False)
        usable = frame[frame["status"] == "COMPLETE"]
        top = usable.sort_values(["profit_factor", "total_pnl_usdt"], ascending=[False, False]).groupby("symbol", as_index=False).head(int(config["research"]["top_results"])) if not usable.empty else usable
        top.to_csv(output_dir / "local_backtest_top.csv", index=False)
    return rows


def scan_tv(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files: list[Path] = []
    csv_files: list[Path] = []
    for raw in config.get("tradingview_input_folders", []):
        folder = Path(raw)
        if folder.exists():
            files.extend(folder.glob("*.xlsx"))
            csv_files.extend(folder.glob("*.csv"))
    records: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(set(files)):
        record, frame = audit_tv_workbook(path)
        # Ignore unrelated Excel files in Downloads unless they look like TV.
        if record["status"] == "ERROR" and "Eksik TradingView sekmeleri" in " ".join(record["issues"]):
            continue
        records.append(record)
        if frame is not None:
            frames[str(path)] = frame
    csv_records = [record for path in sorted(set(csv_files)) if (record := audit_tv_csv(path)) is not None]
    workbook_by_signature = {record.get("trade_signature"): record["file_name"] for record in records}
    for record in csv_records:
        record["matching_workbook_file"] = workbook_by_signature.get(record["trade_signature"])
    return records, compare_tv_records(records, frames), csv_records


def source_code_inventory() -> list[dict[str, Any]]:
    paths = list((PROJECT_ROOT / "sources").glob("*.pine"))
    paths += list(PROJECT_ROOT.glob("*.pine"))
    return [
        {"file": str(path.relative_to(PROJECT_ROOT)), "sha256": sha256_file(path)}
        for path in sorted(set(paths))
    ]


def write_reports(
    config: dict[str, Any], output_dir: Path, tv: list[dict[str, Any]],
    comparisons: list[dict[str, Any]], market: list[dict[str, Any]],
    backtests: list[dict[str, Any]], downloads: list[dict[str, Any]],
    tv_csv: list[dict[str, Any]], universe_snapshot=None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat()
    pd.DataFrame([{k: v for k, v in row.items() if k not in ("properties", "issues")} for row in tv]).to_csv(output_dir / "tradingview_summary.csv", index=False)
    pd.DataFrame(comparisons).to_csv(output_dir / "ab_comparisons.csv", index=False)
    pd.DataFrame(tv_csv).to_csv(output_dir / "tradingview_csv_summary.csv", index=False)
    pd.DataFrame(market).to_csv(output_dir / "market_data_quality.csv", index=False)
    pd.DataFrame(downloads).to_csv(output_dir / "download_log.csv", index=False)

    behavior_changes = [c for c in comparisons if c["verdict"] == "BEHAVIOR_CHANGED"]
    exact_matches = [c for c in comparisons if c["verdict"] == "NO_BEHAVIOR_CHANGE"]
    missing_market = [r["symbol"] for r in market if r.get("status") in {"NO_DATA", "FAIL"}]
    tv_errors = [r for r in tv if r.get("status") != "OK"]
    best_by_symbol: list[dict[str, Any]] = []
    if backtests:
        backtest_frame = pd.DataFrame(backtests)
        usable = backtest_frame[backtest_frame["closed_trades"] >= 30] if "closed_trades" in backtest_frame else pd.DataFrame()
        if not usable.empty:
            best_by_symbol = usable.sort_values(["profit_factor", "total_pnl_usdt"], ascending=[False, False]).groupby("symbol", as_index=False).head(1).to_dict(orient="records")

    handoff = {
        "data_coverage": market,
        "skipped_runs": [row for row in backtests if row.get("status") == "SKIPPED"],
        "universe_selection": universe_snapshot,
        "schema": "local-quant-lab-handoff-v1",
        "generated_at_utc": generated,
        "config_hash": stable_hash(config),
        "summary": {
            "tradingview_workbooks": len(tv),
            "tradingview_trade_csv_files": len(tv_csv),
            "csv_files_matched_to_workbook": sum(bool(row.get("matching_workbook_file")) for row in tv_csv),
            "tradingview_errors_or_checks": len(tv_errors),
            "ab_behavior_changes": len(behavior_changes),
            "ab_exact_matches": len(exact_matches),
            "market_symbols_with_data": sum(r.get("status") == "OK" for r in market),
            "market_symbols_missing": missing_market,
            "local_backtest_runs": sum(row.get("status", "COMPLETE") == "COMPLETE" for row in backtests),
            "skipped_runs": sum(row.get("status") == "SKIPPED" for row in backtests),
        },
        "ab_behavior_changes": behavior_changes,
        "ab_exact_matches": exact_matches,
        "best_local_approximation_by_symbol": best_by_symbol,
        "pine_source_inventory": source_code_inventory(),
        "limitations_and_next_work": [
            "Each symbol uses only available bars within the requested interval. Actual periods differ; returns are not directly comparable. Internal gaps/corrupt data are skipped, never filled with invented prices.",
            "Python broker emulator is a research approximation; TradingView high-detail intrabar fills can differ.",
            "Master Gate, Bias, HL and VWAP must each pass Pine golden-export parity before production conclusions.",
            "The current local matrix covers external-off Master behavior and mutually exclusive NONE/MOST/RSI/Tillson exits.",
            "BTC/ETH candles are downloaded and audited, but relative BTC/ETH regime is not yet wired into trade decisions.",
            "Default indicator-exit parameters currently follow the WCT calibration harness and are not fine-tuned per coin.",
            "Public Binance archives provide market candles, not hidden TradingView indicator state or broker-emulator internals.",
            "All local backtest rows are RESEARCH_APPROXIMATION and production_eligible=false.",
        ],
    }
    (output_dir / "codex_handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2, default=jsonable) + "\n", encoding="utf-8")

    lines = [
        "# Local Quant Lab Raporu",
        "",
        f"Üretim zamanı (UTC): {generated}",
        "",
        "## Özet",
        "",
        f"- TradingView çalışma kitabı: {len(tv)}",
        f"- TradingView işlem CSV dosyası: {len(tv_csv)}",
        f"- A/B davranış değişikliği: {len(behavior_changes)}",
        f"- A/B birebir aynı sonuç: {len(exact_matches)}",
        f"- Yerel piyasa verisi bulunan sembol: {sum(r.get('status') == 'OK' for r in market)}",
        f"- Verisi eksik semboller: {', '.join(missing_market) if missing_market else 'Yok'}",
        f"- Tamamlanan koşu: {sum(row.get('status', 'COMPLETE') == 'COMPLETE' for row in backtests)}; atlanan: {sum(row.get('status') == 'SKIPPED' for row in backtests)}",
        "",
        "## A/B karşılaştırmaları",
        "",
    ]
    lines += ["## Kullanılan veri aralıkları (UTC)", "", "Her coin seçilen aralık içindeki mevcut verisiyle test edilir. Farklı sürelerin getirilerini doğrudan kıyaslamayın.", ""]
    for item in market:
        lines.append(f"- {item['symbol']}: {item.get('status')} | {item.get('tested_start', '?')} → {item.get('tested_end_exclusive', '?')} (bitiş hariç) | {item.get('available_bars', '?')} mum | {item.get('coverage', '')} | {item.get('issues', [])}")
    if comparisons:
        for item in comparisons:
            changed = ", ".join(change["setting"] for change in item["property_changes"]) or "ayar farkı yok"
            lines.append(f"- {item['symbol']}: {item['verdict']} | değişen: {changed} | işlem farkı {item['delta_trades']} | PnL farkı {item['delta_net_profit_usdt']}")
    else:
        lines.append("- Karşılaştırılabilir test çifti bulunamadı.")
    lines += ["", "## Yerel backtest notu", "", "Sonuçlar Pine/TradingView üretim eşitliği kanıtı değildir. Yalnız araştırma ve aday eleme amacıyla kullanılır.", "", "## Codex'e gönderilecek dosya", "", "`codex_handoff.json` dosyasını yeni testlerden sonra Codex'e verin.", ""]
    report = output_dir / "rapor.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def execute(
    config_path: Path, mode: str, data_root: Path, output_dir: Path,
    symbols_override: list[str] | None = None, start_override=None, end_override=None, interval_override=None,
) -> Path:
    config = load_config(config_path)
    if interval_override:
        if interval_override not in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}:
            raise ValueError("Desteklenmeyen Binance zaman dilimi")
        config["interval"] = interval_override
    if start_override:
        config["download"]["start"] = start_override
    if end_override:
        config["download"]["end"] = end_override
    if date.fromisoformat(config["download"]["start"]) >= date.fromisoformat(config["download"]["end"]):
        raise ValueError("Başlangıç bitişten önce olmalı")
    symbols = symbols_override or (config["symbols"] + config.get("references", []) + config.get("calibration_only", []))
    downloads: list[dict[str, Any]] = []
    if mode in ("download", "all"):
        download_root = data_root / "binance_um" / "local_quant_lab"
        for symbol in symbols:
            print(f"Veri indiriliyor: {symbol}", flush=True)
            try:
                downloads.extend(download_symbol(config, symbol, download_root))
            except Exception as exc:
                downloads.append({"symbol": symbol, "status": "FAILED", "error": str(exc)})
    tv, comparisons, tv_csv = scan_tv(config) if mode in ("scan", "all") else ([], [], [])
    market = audit_market_data(data_root, symbols, config["interval"])
    backtests = run_local_matrix(config, data_root, output_dir, symbols_override) if mode in ("backtest", "all") else []
    report = write_reports(config, output_dir, tv, comparisons, market, backtests, downloads, tv_csv)
    if mode in ("download", "all"):
        # Missing symbols/periods are reported individually, not a batch failure.
        start = pd.Timestamp(config["download"]["start"], tz="UTC")
        end = pd.Timestamp(config["download"]["end"], tz="UTC")
        checks = []
        for symbol, paths in discover_market_roots(data_root, symbols, config["interval"]).items():
            try:
                _, check = checked_bars(load_binance_archives(paths), start, end, config["interval"])
            except Exception as exc:
                check = {"status": "FAIL", "issues": [str(exc)]}
            checks.append({"symbol": symbol, **check})
        atomic_write_json(output_dir / "download_coverage.json", {"checks": checks})
        for check in checks:
            print(f"{check['symbol']}: {check['status']} | {check.get('coverage', 'NO_DATA')} | {check.get('available_bars', 0)} mum | {check.get('issues', [])}", flush=True)
        market = [{**check, "status": "OK" if check["status"] == "PASS" else "FAIL"} for check in checks]
        report = write_reports(config, output_dir, tv, comparisons, market, backtests, downloads, tv_csv)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Yerel ve kredi kullanmayan quant test laboratuvarı")
    parser.add_argument("mode", choices=["scan", "download", "backtest", "all", "job"], nargs="?", default="scan")
    parser.add_argument("--config", type=Path, default=LAB_ROOT / "config.json")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "research" / "data" / "raw")
    parser.add_argument("--output-dir", type=Path, default=LAB_ROOT / "reports" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--symbols", nargs="+", help="Yalnız seçilen sembolleri çalıştır (ör. WCTUSDT BTCUSDT)")
    parser.add_argument("--queue", type=Path, default=LAB_ROOT / "jobs.json")
    parser.add_argument("--job-id", help="Kuyruktaki belirli bir işi çalıştır")
    parser.add_argument("--start", help="İndirme başlangıcı UTC, YYYY-MM-DD")
    parser.add_argument("--end", help="İndirme bitişi UTC, hariç, YYYY-MM-DD")
    parser.add_argument("--interval", choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"],
                        help="Yalnız bu indirme/denetim çalışmasında kullanılacak Binance mum zaman dilimi")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.mode == "job":
        report = run_queued_job(args.config, args.queue, args.data_root, args.job_id)
    else:
        report = execute(args.config, args.mode, args.data_root, args.output_dir, args.symbols, args.start, args.end, args.interval)
    print(f"\nTamamlandı: {report}")


if __name__ == "__main__":
    main()
