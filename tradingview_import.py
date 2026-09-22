"""Read TradingView Strategy Tester workbooks into a traceable Quant Lab profile."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


def _value_map(path: Path, sheet: str) -> dict[str, object]:
    frame = pd.read_excel(path, sheet_name=sheet, header=None)
    result = {}
    for _, row in frame.iloc[1:].iterrows():
        if len(row) < 2 or pd.isna(row.iloc[0]):
            continue
        result[str(row.iloc[0]).strip()] = row.iloc[1]
    return result


def _metric_value(path: Path, sheet: str, label: str, column: int = 1):
    """Return a named Strategy Tester metric from its intended numeric column.

    TradingView's ``Trades analysis`` puts percentage values in the third
    column (``All %``), unlike Performance where the second column is USDT.
    Keeping the column explicit prevents a blank USDT cell from being
    mistaken for a zero win rate.
    """
    frame = pd.read_excel(path, sheet_name=sheet, header=None)
    matches = frame.index[frame.iloc[:, 0].astype(str).str.strip() == label]
    if len(matches) == 0 or len(frame.columns) <= column:
        return None
    value = frame.iloc[matches[0], column]
    return None if pd.isna(value) else value


def _bool(value):
    return str(value).strip().lower() in {"on", "true", "1", "yes"}


def _num(value):
    return int(value) if float(value).is_integer() else float(value)


def _timeframe_minutes(value) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*(minute|minutes|hour|hours|day|days)\s*", str(value), flags=re.I)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    return amount * {"minute": 1, "minutes": 1, "hour": 60, "hours": 60, "day": 1440, "days": 1440}[unit]


def _period(value):
    raw = str(value).replace("—", "-").strip()
    parts = re.split(r"\s+-\s+", raw, maxsplit=1)
    if len(parts) != 2:
        return {"raw": raw}
    parsed = []
    for item in parts:
        try:
            parsed.append(pd.Timestamp(item).tz_localize(timezone.utc).isoformat())
        except (TypeError, ValueError):
            return {"raw": raw}
    return {"start": parsed[0], "end_inclusive": parsed[1], "raw": raw}


def _source_id(value):
    text = str(value).upper()
    for needle, source in (("MASTER GATE", "MASTER_GATE"), ("VWAP", "VWAP"), ("HIGH/LOW", "HL"), ("HL GATE", "HL"), ("BIAS", "BIAS")):
        if needle in text:
            return source
    return None


def import_strategy_workbook(path: str | Path) -> dict:
    path = Path(path).resolve()
    if not path.exists() or path.suffix.lower() != ".xlsx":
        raise ValueError("TradingView Strategy Tester .xlsx dosyası bulunamadı")
    workbook = pd.ExcelFile(path)
    required = {"Performance", "Trades analysis", "Risk-adjusted performance", "Properties"}
    missing = required - set(workbook.sheet_names)
    if missing:
        raise ValueError("TradingView Strategy Tester sayfaları eksik: " + ", ".join(sorted(missing)))
    props = _value_map(path, "Properties")
    performance = _value_map(path, "Performance")
    trades = _value_map(path, "Trades analysis")
    risk = _value_map(path, "Risk-adjusted performance")
    symbol = str(props.get("Symbol", "")).replace("BINANCE:", "").replace(".P", "")
    settings = {}
    aliases = {
        "Gate Panelini Göster": ("show_gate_panel", _bool), "'GO' Etiketlerini Göster": ("show_go_labels", _bool),
        "Debug: Data Window plotları": ("debug_data_window", _bool), "Tarih Filtresi Kullan": ("use_date_filter", _bool),
        "Master Gate Aktif Et": ("strategy_master_enabled", _bool), "External Gate Birleşim Modu": ("ext_mode", str),
        "External Gate 1 Aktif": ("ext1_enabled", _bool), "External Gate 2 Aktif": ("ext2_enabled", _bool),
        "Stop Loss Kullan": ("strategy_sl_enabled", _bool), "Stop Loss (%)": ("stop_loss_pct", _num),
        "Take Profit (Kar Al) Kullan": ("strategy_tp_enabled", _bool), "Take Profit (%)": ("strategy_tp_pct", _num),
        "Fixed Trailing Kullan": ("strategy_trail_enabled", _bool), "Trailing Başlama Karı (%)": ("trailing_activation_pct", _num),
        "Trailing Stop Mesafesi (%)": ("trailing_distance_pct", _num), "MOST Çıkışı Aktif": ("exit_use_most", _bool),
        "MOST Çıkış Modu": ("exit_most_mode", str), "MOST Çıkış Timeframe": ("exit_most_minutes", _num),
        "MOST Periyot": ("exit_most_length", _num), "MOST Yüzde": ("exit_most_percent", _num),
        "RSI < SMA Olunca Çık": ("exit_use_rsi", _bool), "RSI Çıkış Timeframe": ("exit_rsi_minutes", _num),
        "RSI Uzunluk (Çıkış)": ("exit_rsi_length", _num), "SMA Uzunluk (Çıkış)": ("exit_rsi_sma_length", _num),
        "TILLSON Çıkışı Aktif": ("exit_use_t3", _bool), "TILLSON Çıkış Modu": ("exit_t3_mode", str),
        "Tillson Çıkış Timeframe": ("exit_t3_minutes", _num), "Tillson Length": ("exit_t3_length", _num),
        "Tillson V-Factor": ("exit_t3_factor", _num), "request.security lookahead": ("exit_lookahead", str),
        "Use Daily Entry Limit": ("daily_enabled", _bool), "Max entries per day": ("daily_max_entries", _num),
        "ER Length": ("er_length", _num), "ER Min": ("er_threshold", _num), "Persist (N bar)": ("er_persist_bars", _num),
        "ROLLING/WEIGHTED pencere (son X trade)": ("winrate_window", _num), "Minimum trade (LIVE başlasın)": ("winrate_min_trades", _num),
        "Min trade dolmadan BLOKLA": ("winrate_block_before_min", _bool), "Min Winrate (0-1)": ("winrate_threshold", _num),
        "Winrate BLOK olunca POZİSYONU KAPAT": ("winrate_close_on_block", _bool),
        "Initial capital": ("initial_capital", _num), "Default order size": ("cash_per_order", _num), "Commission": ("commission_pct_per_order", _num),
    }
    for label, (key, converter) in aliases.items():
        if label in props and not pd.isna(props[label]):
            settings[key] = converter(props[label])
    ext1 = props.get("External Gate 1 Sinyali (+1/0/-1)")
    ext2 = props.get("External Gate 2 Sinyali (+1/0/-1)")
    for key, raw in (("ext1_source", ext1), ("ext2_source", ext2)):
        inferred = _source_id(raw)
        if inferred:
            settings[key] = inferred
    er = "OFF" if not _bool(props.get("ER Gate Aktif", "Off")) else str(props.get("ER Versiyon", "v2"))
    winrate = "OFF"
    if _bool(props.get("Winrate Gate Aktif", "Off")):
        winrate = "SH_WEIGHTED_CLOSE" if _bool(props.get("Winrate BLOK olunca POZİSYONU KAPAT", "Off")) else "SH_WEIGHTED_HOLD"
    reference = {
        "source_type": "TradingView Strategy Tester workbook", "source_file": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "imported_at_utc": datetime.now(timezone.utc).isoformat(),
        "chart": {"symbol": props.get("Symbol"), "timeframe": props.get("Timeframe"), "timeframe_minutes": _timeframe_minutes(props.get("Timeframe")),
                  "chart_type": props.get("Chart type"), "currency": props.get("Currency"), "tick_size": props.get("Tick size"), "point_value": props.get("Point value")},
        "period": _period(props.get("Trading range", "")),
        "strategy_properties": {key: props.get(key) for key in ("Pyramiding", "Bar detalization", "Script execution", "Slippage", "Limit order execution", "Order execution delay", "Long leverage", "Short leverage") if key in props},
        "performance": {
            "net_profit_usdt": performance.get("Net profit"),
            "commission_usdt": performance.get("Commission paid"),
            "max_drawdown_usdt": performance.get("Max drawdown (intrabar)") or performance.get("Max drawdown (close-to-close)"),
        },
        "trade_statistics": {
            "total_trades": trades.get("Total trades"), "winners": trades.get("Total winners"),
            "losers": trades.get("Total losers"),
            "win_rate_pct": _metric_value(path, "Trades analysis", "Percent profitable", 2),
        },
        "risk_statistics": {"profit_factor": risk.get("Profit factor"), "sharpe": risk.get("Sharpe ratio"), "sortino": risk.get("Sortino ratio")},
        "unmapped_properties": {key: value for key, value in props.items() if key not in aliases and key not in {"External Gate 1 Sinyali (+1/0/-1)", "External Gate 2 Sinyali (+1/0/-1)"}},
    }
    return {"reference": reference, "symbol": symbol, "settings": settings, "categorical": {"er": er, "winrate_state": winrate}, "external_display_names": {"ext1": ext1, "ext2": ext2}}
