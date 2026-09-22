"""Read-only market discovery and recoverable named lists. No trading APIs."""
from __future__ import annotations
import copy
import json
import math
import re
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from lab_safety import atomic_write_json, file_lock


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_store(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(default)


def parse_symbols(text: str, quote="USDT"):
    values = []
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        symbol = re.sub(r"^BINANCE:", "", token.upper())
        symbol = re.sub(r"\.P$", "", symbol).replace("/", "")
        if not symbol.endswith(("USDT", "USDC")):
            symbol += quote
        if not re.fullmatch(r"[A-Z0-9]{1,30}(USDT|USDC)", symbol):
            raise ValueError(f"Geçersiz parite: {token}")
        if symbol not in values:
            values.append(symbol)
    return values


def save_list(path, name, symbols, provenance, list_id=None):
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Liste adı 1–100 karakter olmalı")
    if not symbols:
        raise ValueError("Listeye en az bir parite ekleyin")
    normalized = parse_symbols(" ".join(symbols))
    with file_lock(path.with_suffix(".lock")):
        store = read_store(path, {"schema": 1, "lists": []})
        if any(row["name"].casefold() == name.casefold() and row["id"] != list_id and not row.get("deleted_at") for row in store["lists"]):
            raise ValueError("Bu adla kayıtlı liste var; açıp güncelleyin veya farklı ad kullanın")
        old = next((row for row in store["lists"] if row["id"] == list_id and not row.get("deleted_at")), None)
        if list_id and not old:
            raise ValueError("Liste artık mevcut değil; yeni liste olarak kaydedin")
        record = {"id": list_id or uuid.uuid4().hex, "name": name, "symbols": normalized,
                  "created_at": old["created_at"] if old else utc_now(), "updated_at": utc_now(),
                  "provenance": copy.deepcopy(provenance)}
        if old:
            record["history"] = old.get("history", []) + [{k: v for k, v in old.items() if k != "history"}]
            store["lists"][store["lists"].index(old)] = record
        else:
            store["lists"].append(record)
        atomic_write_json(path, store)
    return copy.deepcopy(record)


def delete_list(path, list_id):
    with file_lock(path.with_suffix(".lock")):
        store = read_store(path, {"lists": []})
        row = next(row for row in store["lists"] if row["id"] == list_id)
        row["deleted_at"] = utc_now()  # Recoverable; never touches jobs, data or reports.
        atomic_write_json(path, store)


def active_lists(path):
    return [row for row in read_store(path, {"lists": []})["lists"] if not row.get("deleted_at")]


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def make_catalog(exchange, tickers, observed_at=None):
    if not isinstance(exchange, dict) or not isinstance(exchange.get("symbols"), list) or not isinstance(tickers, list):
        raise ValueError("Binance yanıt biçimi beklenenden farklı")
    ticker_map = {row["symbol"]: row for row in tickers if isinstance(row, dict) and "symbol" in row}
    rows = []
    for item in exchange["symbols"]:
        if item.get("status") != "TRADING" or item.get("contractType") != "PERPETUAL" or item.get("quoteAsset") not in {"USDT", "USDC"}:
            continue
        if item.get("underlyingType") not in {None, "COIN"}:
            continue
        symbol = item.get("symbol", "")
        if not re.fullmatch(r"[A-Z0-9]{1,30}(USDT|USDC)", symbol):
            continue
        ticker = ticker_map.get(symbol, {})
        raw_tags = item.get("underlyingSubType", [])
        tags = [tag for tag in raw_tags if isinstance(tag, str) and tag] if isinstance(raw_tags, list) else []
        rows.append({"symbol": symbol, "quote": item["quoteAsset"], "categories": tags,
                     "onboard_ms": number(item.get("onboardDate")), "change_pct": number(ticker.get("priceChangePercent")),
                     "quote_volume": number(ticker.get("quoteVolume")), "ticker_close_ms": number(ticker.get("closeTime"))})
    if not rows:
        raise ValueError("Yanıtta desteklenen aktif vadeli parite bulunamadı; eski katalog korunuyor")
    return {"schema": 1, "source": "Binance USD-M public exchangeInfo + 24hr ticker", "observed_at": observed_at or utc_now(), "rows": rows}


def refresh_catalog(path):
    def get(route):
        request = urllib.request.Request("https://fapi.binance.com/fapi/v1/" + route, headers={"User-Agent": "LocalQuantLab/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    # Failures never replace a last-known-good cache with an empty/partial response.
    catalog = make_catalog(get("exchangeInfo"), get("ticker/24hr"))
    with file_lock(path.with_suffix(".lock")):
        atomic_write_json(path, catalog)
    return catalog


def screen(catalog, mode="Tümü", search="", category="Tümü", quote="USDT", limit=20, custom=None):
    if not 1 <= limit <= 2000:
        raise ValueError("Liste sınırı 1–2000 arasında olmalı")
    custom = custom or {}
    rows = [row for row in catalog.get("rows", []) if row["quote"] == quote and search.upper().strip() in row["symbol"]]
    if category != "Tümü":
        rows = [row for row in rows if category in ["Binance: " + tag for tag in row["categories"]] + ["Kişisel: " + tag for tag in custom.get(row["symbol"], [])]]
    if mode in {"Yükselenler (24s)", "Hacim (24s)", "Yeni listelenen"}:
        field = {"Yükselenler (24s)": "change_pct", "Hacim (24s)": "quote_volume", "Yeni listelenen": "onboard_ms"}[mode]
        rows = [row for row in rows if row.get(field) is not None and row[field] > 0]
        rows.sort(key=lambda row: (-row[field], row["symbol"]))
    else:
        rows.sort(key=lambda row: row["symbol"])
    return rows[:limit]


def selection_snapshot(symbols, name, provenance):
    return {"name": name.strip() or "Kaydedilmemiş liste", "symbols": list(symbols), "selected_at": utc_now(),
            "provenance": copy.deepcopy(provenance), "historical_universe_validated": False,
            "warning": "Bugünkü aktif/öne çıkan pariteleri geçmişte seçilmiş kabul etmeyin; seçim sonrası doğrulama gerekir."}
