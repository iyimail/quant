"""Windows desktop interface for Local Quant Lab.

The GUI only orchestrates the existing local research engine.  It does not
change Pine sources and it never labels an approximation as production-ready.
"""

from __future__ import annotations

import json
import hashlib
import difflib
import os
import re
import subprocess
import sys
import threading
import csv
import math
import tkinter as tk
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk, filedialog
from gate_data import load_feed_manifest
from typing import Any
from lab_safety import atomic_write_json, append_job, append_jobs, validate_parameters, engine_identity, validate_engine, recover_interrupted
from coin_list_panel import CoinListPanel
from coin_lists import selection_snapshot
from gate_settings import ROUTING_DEFAULTS, ROUTING_CHOICES, ROUTING_INTS
from component_settings import MASTER_DEFAULTS, BIAS_DEFAULTS, STRATEGY_DEFAULTS, TEXT_FIELDS, FLOATS, validate_component
from experiment_plan import build_baseline_ablation_plan, compare_plan
from result_analysis import ResultAnalysis
from result_seed import result_seed
from test_categories import load_categories, save_category
from tradingview_import import import_strategy_workbook
from system_audit import build_system_audit_plan, audit_summary


LAB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_ROOT.parent
CONFIG_PATH = LAB_ROOT / "config.json"
QUEUE_PATH = LAB_ROOT / "jobs.json"
STATUS_PATH = LAB_ROOT / "supervisor_status.json"
CATEGORIES_PATH = LAB_ROOT / "test_categories.json"
DATA_ROOT = PROJECT_ROOT / "research" / "data" / "raw"
REPORTS_ROOT = LAB_ROOT / "reports" / "jobs"
EXPERIMENTS_ROOT = LAB_ROOT / "reports" / "experiments"
CANDIDATES_ROOT = LAB_ROOT / "candidate_versions"
CANDIDATE_REGISTRY = CANDIDATES_ROOT / "registry.json"
TRADINGVIEW_PROFILES_ROOT = LAB_ROOT / "tradingview_profiles"

COMPONENT_FILES = {
    "Master Strategy": "master.pine",
    "Master Gate": "master gate.pine",
    "Bias Gate": "bias.pine",
    "High/Low Gate": "hl gate.pine",
    "VWAP Gate": "vwap gate.pine",
}

EXIT_OPTIONS = ("NONE", "MOST", "RSI_SMA", "TILLSON", "COMBINED")
ER_OPTIONS = ("OFF", "v1", "v2")
WINRATE_OPTIONS = ("OFF", "SH_WEIGHTED_HOLD", "SH_WEIGHTED_CLOSE")

PARAMETERS: tuple[tuple[str, str], ...] = (
    ("Başlangıç Sermayesi", "initial_capital"),
    ("İşlem Başına Tutar", "cash_per_order"),
    ("Komisyon (%)", "commission_pct_per_order"),
    ("Stop Loss (%)", "stop_loss_pct"),
    ("Trailing Başlama (%)", "trailing_activation_pct"),
    ("Trailing Mesafe (%)", "trailing_distance_pct"),
    ("ER Uzunluk", "er_length"),
    ("ER Eşiği", "er_threshold"),
    ("ER Persist Bar", "er_persist_bars"),
    ("Winrate Pencere", "winrate_window"),
    ("Minimum İşlem", "winrate_min_trades"),
    ("Minimum Winrate", "winrate_threshold"),
    ("Min İşlem Öncesi Blok (true/false)", "winrate_block_before_min"),
)
INTEGER_PARAMETERS = {"er_length", "er_persist_bars", "winrate_window", "winrate_min_trades"}
BOOLEAN_PARAMETERS = {"winrate_block_before_min"}

# Only these external-gate models are currently implemented by the local,
# causal research engine.  They are deliberately kept separate from Pine
# source files: changing an input here never changes a TradingView script.
GATE_PARAMETER_TABS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "Master Gate": (
        "Açık gate'ler AND ile birleşir. Bu ayarlar yerel araştırma motorunun giriş ve isteğe bağlı kapanış davranışını belirler.",
        (("Gate kapanınca pozisyonu kapat (true/false)", "external_gate_closes_position"),),
    ),
    "VWAP Gate": (
        "Daily/Weekly VWAP modelinin kaynakla aynı bar gecikmesini kullanan araştırma karşılığı.",
        (("VWAP Gate aktif (true/false)", "vwap_enabled"),
         ("Bant (bps)", "vwap_band_bps"),
         ("Süreklilik barı", "vwap_persist_bars"),
         ("G6 üzerinden bağla (true/false)", "vwap_via_g6")),
    ),
    "MOST Gate": (
        "G1 için yalnız önceki onaylanmış HTF MOST çizgisini kullanan araştırma karşılığı.",
        (("MOST Gate aktif (true/false)", "most_enabled"),
         ("MOST uzunluğu", "most_length"),
         ("MOST yüzde", "most_percent")),
    ),
    "Tillson Gate": (
        "G2 için yalnız onaylanmış HTF Tillson T3 değerini kullanan araştırma karşılığı.",
        (("Tillson Gate aktif (true/false)", "tillson_enabled"),
         ("T3 uzunluğu", "tillson_length"),
         ("T3 faktörü", "tillson_factor")),
    ),
}
GATE_INTEGER_PARAMETERS = {"vwap_band_bps", "vwap_persist_bars", "most_length", "tillson_length"}
GATE_BOOLEAN_PARAMETERS = {
    "external_gate_closes_position", "vwap_enabled", "vwap_via_g6", "most_enabled", "tillson_enabled",
}
GATE_DEFAULTS: dict[str, Any] = {
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
GATE_DEFAULTS.update(ROUTING_DEFAULTS)
GATE_DEFAULTS["routing_mode"] = "EXPLICIT"  # only new UI jobs; saved LEGACY jobs retain their model
GATE_DEFAULTS["exit_model"] = "SOURCE_MTF"
GATE_INTEGER_PARAMETERS.update(ROUTING_INTS)
GATE_PARAMETER_TABS["High/Low Gate"] = (
    "Onaylı pivot olay motoru. Zorunlu kural: Chart < MTF < HTF. Yerel 30m test için varsayılan 1h / 4h'tür. TradingView 1h grafik için 4h / 1D seçin.",
    (("Sol pivot barı", "hl_left"), ("Sağ onay barı", "hl_right"),
     ("MTF dakika", "hl_mtf_minutes"), ("HTF dakika", "hl_htf_minutes"),
     ("Aktivasyon", "hl_activation"), ("MTF onayı", "hl_confirmation"),
     ("Kapanış TF", "hl_close_source"), ("Kapanış olayı", "hl_close_mode")),
)
GATE_BOOLEAN_PARAMETERS.update(key for key, value in ROUTING_DEFAULTS.items() if isinstance(value, bool))
# MOST/Tillson are Master Gate components, not standalone Pine scripts.
GATE_PARAMETER_TABS["Master Gate"] = (
    "EXPLICIT: gerçek G6 → Master → EXT tüketici gecikmeleri. LEGACY: eski araştırma AND modeli. Tam Pine eşliği henüz yok.",
    GATE_PARAMETER_TABS["Master Gate"][1] + (
        ("Bağlantı modeli", "routing_mode"), ("EXT birleşimi", "ext_mode"),
        ("EXT1 aktif", "ext1_enabled"), ("EXT1 kaynak", "ext1_source"),
        ("EXT2 aktif", "ext2_enabled"), ("EXT2 kaynak", "ext2_source"),
        ("G6 aktif", "g6_enabled"), ("G6 kaynak", "g6_source"), ("G6 koşulu", "g6_mode"),
        ("Makro reset aktif (Pine varsayılanı true)", "macro_enabled"),
        ("Makro dakika", "macro_minutes"), ("Makro EMA uzunluk", "macro_length"),
        ("G7 aktif", "g7_enabled"), ("G7 modu", "g7_mode"), ("G7 kaynak", "g7_source"),
        ("G7 seviye", "g7_level"), ("G7 SMA uzunluk", "g7_length"),
        ("G7 hata reseti", "g7_reset_on_fail"), ("G7 günlük reset", "g7_reset_each_day"),
    ) + GATE_PARAMETER_TABS.pop("MOST Gate")[1] + GATE_PARAMETER_TABS.pop("Tillson Gate")[1],
)
GATE_PARAMETER_TABS["VWAP Gate"] = (
    "EXPLICIT modunda kaynak bağlantısı hesaplamayı etkinleştirir; eski aktif/G6 kutuları yalnız LEGACY moduna aittir.",
    GATE_PARAMETER_TABS["VWAP Gate"][1] + (
        ("Günlük VWAP kullan", "vwap_use_daily"), ("Haftalık VWAP kullan", "vwap_use_weekly"),
        ("Karar modu", "vwap_mode"), ("Günlük kaynak", "vwap_daily_source"),
        ("Haftalık kaynak", "vwap_weekly_source"),
    ),
)
GATE_PARAMETER_TABS["Master Gate"] = (
    "G0–G8 hesapları ve bağlantılar. SOURCE_HISTORICAL_LOOKAHEAD geleceği kullanan tarihsel tanı modudur; performans kanıtı değildir.",
    GATE_PARAMETER_TABS["Master Gate"][1] + tuple((key,key) for key in MASTER_DEFAULTS),
)
GATE_PARAMETER_TABS["Bias Gate"] = (
    "Gerçek venue/Spot/Perp verisi gerekir. Harici veri manifestinden bağlanır; eksik borsa veya referans verisi ikame edilmez.",
    tuple((key,key) for key in BIAS_DEFAULTS),
)
COIN_CATEGORIES = {
    "Aktif işlem coini": "symbols",
    "BTC/ETH referansı": "references",
    "Yalnız kalibrasyon": "calibration_only",
}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def safe_job_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw.strip()).strip("-")
    if not cleaned:
        cleaned = datetime.now().strftime("JOB-%Y%m%d-%H%M%S")
    return cleaned[:80].upper()


def parse_grid_value(raw: str) -> list[int | float | bool]:
    text = raw.strip()
    if ":" in text:
        parts = [part.strip() for part in text.split(":")]
        if len(parts) != 3:
            raise ValueError("Aralık biçimi başlangıç:bitiş:adım olmalıdır. Örnek: 2:5:0.25")
        try:
            start, end, step = (Decimal(part.replace(",", ".")) for part in parts)
        except InvalidOperation as exc:
            raise ValueError("Aralıkta geçersiz sayı var") from exc
        if not all(value.is_finite() for value in (start, end, step)):
            raise ValueError("Sonlu sayılar girin")
        if step <= 0:
            raise ValueError("Aralık adımı sıfırdan büyük olmalıdır")
        if start > end:
            raise ValueError("Aralık başlangıcı bitişten büyük olamaz")
        count = int(((end - start) / step).to_integral_value(rounding="ROUND_FLOOR")) + 1
        if count > 10_000:
            raise ValueError("Tek parametre için 10.000'den fazla değer üretilemez")
        decimals = [start + step * index for index in range(count)]
        if decimals[-1] < end and end - decimals[-1] < step / Decimal("1000000"):
            decimals[-1] = end
        integral = all(value == value.to_integral_value() for value in decimals)
        return [int(value) if integral else float(value) for value in decimals]
    values: list[int | float | bool] = []
    lowered_text = text.lower()
    if ";" in text:
        parts_to_parse = text.split(";")
    elif lowered_text in {"true,false", "false,true"} or text.count(",") > 1 or (text.count(",") == 1 and "." in text):
        parts_to_parse = text.split(",")
    else:
        # Turkish locale decimal: 0,17 is one number. Lists should use ';'.
        parts_to_parse = [text]
    for item in (part.strip() for part in parts_to_parse):
        if not item:
            continue
        lowered = item.lower()
        if lowered in {"true", "false"}:
            values.append(lowered == "true")
        elif re.fullmatch(r"[-+]?\d+", item):
            values.append(int(item))
        else:
            values.append(float(item.replace(",", ".")))
    if not values:
        raise ValueError("En az bir sayısal değer girilmelidir")
    if any(isinstance(value, float) and not math.isfinite(value) for value in values):
        raise ValueError("Sonlu sayılar girin")
    return values


def planned_runs(symbols: list[str], exits: list[str], ers: list[str], states: list[str], grid: dict[str, list[Any]]) -> int:
    multiplier = 1
    for values in grid.values():
        multiplier *= max(1, len(values))
    return len(symbols) * len(exits) * len(ers) * len(states) * multiplier


def parameter_values(single: str, start: str, end: str, step: str) -> list[int | float | bool]:
    range_parts = [start.strip(), end.strip(), step.strip()]
    if any(range_parts):
        if not all(range_parts):
            raise ValueError("Aralık için Başlangıç, Bitiş ve Adım alanlarının üçü de doldurulmalıdır")
        return parse_grid_value(":".join(range_parts))
    if not single.strip():
        return []
    return parse_grid_value(single)


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    symbol = re.sub(r"^BINANCE:", "", symbol)
    symbol = re.sub(r"\.P$", "", symbol)
    symbol = symbol.replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9]{1,30}(USDT|USDC)", symbol):
        raise ValueError("Binance USD-M paritesi girin: BTCUSDT, OPUSDT veya ETHUSDC. Spot/diğer borsalar desteklenmiyor.")
    return symbol


def register_symbol(config_path: Path, symbol: str, category_key: str) -> dict[str, Any]:
    if category_key not in {"symbols", "references", "calibration_only"}:
        raise ValueError("Bilinmeyen coin kategorisi")
    config = read_json(config_path, {})
    for key in ("symbols", "references", "calibration_only"):
        config[key] = [item for item in config.get(key, []) if item != symbol]
    config.setdefault(category_key, []).append(symbol)
    atomic_write_json(config_path, config)
    return config


def append_ready_job(queue_path: Path, job: dict[str, Any]) -> None:
    append_job(queue_path, job)


def bundled_python() -> Path | None:
    candidate = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
    return candidate if candidate.exists() else None


def audit_pine_candidate(code: str, source_code: str = "") -> dict[str, Any]:
    normalized = code.replace("\r\n", "\n").strip() + "\n"
    if not normalized.strip():
        raise ValueError("Pine kodu boş olamaz")
    issues: list[str] = []
    if not re.search(r"(?m)^\s*//@version=6\s*$", normalized):
        issues.append("Pine v6 bildirimi bulunamadı")
    if not re.search(r"\b(strategy|indicator)\s*\(", normalized):
        issues.append("strategy() veya indicator() bildirimi bulunamadı")
    if "lookahead_on" in normalized:
        issues.append("lookahead_on kullanımı var; future-leak denetimi zorunlu")
    security_calls = len(re.findall(r"request\.security\s*\(", normalized))
    source_lines = source_code.replace("\r\n", "\n").splitlines()
    candidate_lines = normalized.splitlines()
    additions = deletions = 0
    if source_lines:
        for line in difflib.ndiff(source_lines, candidate_lines):
            additions += line.startswith("+ ")
            deletions += line.startswith("- ")
    return {
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper(),
        "line_count": len(candidate_lines),
        "request_security_calls": security_calls,
        "has_barstate_confirmation": "barstate.isconfirmed" in normalized,
        "issues": issues,
        "added_lines": additions,
        "deleted_lines": deletions,
        "normalized_code": normalized,
    }


def save_pine_candidate(component: str, label: str, code: str) -> tuple[Path, dict[str, Any]]:
    if component not in COMPONENT_FILES:
        raise ValueError("Bilinmeyen Pine bileşeni")
    source_path = PROJECT_ROOT / "sources" / COMPONENT_FILES[component]
    source_code = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    audit = audit_pine_candidate(code, source_code)
    version_label = safe_job_id(label or datetime.now().strftime("V-%Y%m%d-%H%M%S"))
    component_slug = safe_job_id(component).lower()
    folder = CANDIDATES_ROOT / component_slug
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{datetime.now().strftime('%Y%m%d_%H%M%S%f')}__{version_label}.pine"
    with target.open("x", encoding="utf-8") as handle:
        handle.write(audit.pop("normalized_code"))
    registry = read_json(CANDIDATE_REGISTRY, {"schema": "local-quant-lab-candidate-registry-v1", "versions": []})
    record = {
        "id": f"{component_slug}__{version_label}__{audit['sha256'][:12]}",
        "component": component,
        "label": version_label,
        "file": str(target),
        "source_of_truth_file": str(source_path),
        "created_at_local": datetime.now().astimezone().isoformat(),
        **audit,
        "status": "PARITY_REQUIRED",
        "python_engine_connected": False,
        "production_eligible": False,
    }
    registry.setdefault("versions", []).append(record)
    CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CANDIDATE_REGISTRY, registry)
    return target, record


class QuantLabApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Local Quant Lab — Araştırma Kontrol Paneli")
        self.geometry("1280x820")
        self.minsize(1050, 680)
        self.configure(bg="#111827")
        self.process: subprocess.Popen[str] | None = None
        self.supervisor_process: subprocess.Popen[str] | None = None
        self._closing = False
        self.pine_candidate = None
        self.extra_symbols = []
        self.universe_snapshot = None
        self.config_data = read_json(CONFIG_PATH, {})
        self.symbol_vars: dict[str, tk.BooleanVar] = {}
        self.exit_vars: dict[str, tk.BooleanVar] = {}
        self.er_vars: dict[str, tk.BooleanVar] = {}
        self.winrate_vars: dict[str, tk.BooleanVar] = {}
        self.grid_vars: dict[str, dict[str, tk.StringVar]] = {}
        self.grid_modes: dict[str, tk.StringVar] = {}
        # A Strategy Tester export is evidence, never a replacement for the
        # executable model.  It is pinned into every job that uses it.
        self.tradingview_reference: dict[str, Any] | None = None
        self._configure_style()
        self._build()
        self.refresh_jobs()
        self.after(1500, self._periodic_refresh)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#111827")
        style.configure("Card.TFrame", background="#1f2937")
        style.configure("TLabel", background="#111827", foreground="#e5e7eb", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="#1f2937", foreground="#e5e7eb", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#111827", foreground="#f9fafb", font=("Segoe UI Semibold", 20))
        style.configure("Muted.TLabel", background="#111827", foreground="#9ca3af", font=("Segoe UI", 9))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=8)
        style.configure("TButton", font=("Segoe UI", 9), padding=6)
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#111827", insertcolor="#111827", padding=5)
        style.configure("TCombobox", fieldbackground="#ffffff", foreground="#111827", padding=5)
        style.configure("TCheckbutton", background="#1f2937", foreground="#e5e7eb", font=("Segoe UI", 9))
        style.configure("Treeview", background="#172033", fieldbackground="#172033", foreground="#e5e7eb", rowheight=28)
        style.configure("Treeview.Heading", background="#374151", foreground="#f9fafb", font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#2563eb")])
        style.configure("TNotebook", background="#111827", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI Semibold", 10))

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(22, 16, 22, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Local Quant Lab", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="RESEARCH_APPROXIMATION · gerçek para emri üretmez", style="Muted.TLabel").pack(side="left", padx=18, pady=(8, 0))
        self.engine_label = ttk.Label(header, text="Motor: Hazır", foreground="#34d399")
        self.engine_label.pack(side="right", pady=(8, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.setup_tab = ttk.Frame(self.notebook, padding=14)
        self.lists_tab = ttk.Frame(self.notebook)
        self.queue_tab = ttk.Frame(self.notebook, padding=14)
        self.experiments_tab = ttk.Frame(self.notebook, padding=14)
        self.code_tab = ttk.Frame(self.notebook, padding=14)
        self.results_tab = ttk.Frame(self.notebook, padding=14)
        self.log_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.lists_tab, text="Test Listeleri")
        self.notebook.add(self.setup_tab, text="Yeni Test")
        self.notebook.add(self.queue_tab, text="Çalışan Testler")
        self.notebook.add(self.experiments_tab, text="Araştırma Planı")
        self.notebook.add(self.results_tab, text="Sonuçları İncele")
        self.notebook.add(self.code_tab, text="Yeni Kod / Sürüm")
        self.notebook.add(self.log_tab, text="Canlı Kayıt")
        self._build_setup()
        self._build_queue()
        self._build_experiments()
        self._build_results()
        self._build_code()
        self._build_log()
        self.list_panel = CoinListPanel(self.lists_tab, LAB_ROOT, self.apply_test_list, lambda: self._selected(self.symbol_vars))
        self.list_panel.pack(fill="both", expand=True)

    def apply_test_list(self, symbols, snapshot):
        # "Yeni Test" yalnız seçilen listenin anlık çalışma alanıdır. Önceki
        # listeyi burada tutmak yeni testte eski coinlerin görünmesine yol açar.
        selected_symbols = list(dict.fromkeys(symbols))
        previous_vars = self.symbol_vars
        self.extra_symbols = selected_symbols
        self.symbol_vars = {}
        for symbol in selected_symbols:
            variable = previous_vars.get(symbol) or tk.BooleanVar()
            variable.set(True)
            self.symbol_vars[symbol] = variable
        self.universe_snapshot = snapshot
        self.symbol_search_var.set("")
        self._render_symbols()
        self._update_count()
        self.job_id_var.set(safe_job_id(snapshot["name"])[:45] + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        self.notebook.select(self.setup_tab)
        dynamic = snapshot.get("historical_filter")
        suffix = f" · tarihsel {dynamic['mode']} Top {dynamic['top_n']}" if dynamic else " · statik liste"
        self._log(f"Test seçimi değiştirildi: {snapshot['name']} ({len(symbols)} parite){suffix}. Henüz test başlatılmadı.")

    def _card(self, parent: tk.Widget, title: str) -> ttk.Frame:
        outer = ttk.Frame(parent, style="Card.TFrame", padding=12)
        ttk.Label(outer, text=title, style="Card.TLabel", font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 8))
        return outer

    def _parameter_grid_form(
        self,
        parent: tk.Widget,
        specs: tuple[tuple[str, str], ...],
        defaults: dict[str, Any],
        *,
        integer_fields: set[str],
        boolean_fields: set[str],
    ) -> None:
        """Render the same list/range editor in every parameter tab."""
        form = ttk.Frame(parent, style="Card.TFrame")
        form.pack(fill="x")
        for column, heading in enumerate(("Parametre", "Yöntem", "Tek değer / liste", "Başlangıç", "Bitiş", "Adım")):
            ttk.Label(form, text=heading, style="Card.TLabel", font=("Segoe UI Semibold", 8)).grid(
                row=0, column=column, sticky="ew", padx=3, pady=(0, 5)
            )
        for index, (label, key) in enumerate(specs, start=1):
            ttk.Label(form, text=label, style="Card.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 8), pady=3)
            default = defaults.get(key, "")
            default_text = str(default).lower() if isinstance(default, bool) else str(default).replace(".", ",") if isinstance(default,(int,float)) else str(default)
            variables = {field: tk.StringVar(value=default_text if field == "single" else "") for field in ("single", "start", "end", "step")}
            for variable in variables.values():
                variable.trace_add("write", lambda *_: self._update_count())
            self.grid_vars[key] = variables
            mode = tk.StringVar(value="Tek değer / liste")
            self.grid_modes[key] = mode
            options = ("Tek değer / liste",) if key in boolean_fields or key in ROUTING_CHOICES or key in TEXT_FIELDS else ("Tek değer / liste", "Aralık")
            ttk.Combobox(form, textvariable=mode, values=options, state="readonly", width=16).grid(row=index, column=1, sticky="ew", padx=3)
            if key in ROUTING_CHOICES or key in TEXT_FIELDS:
                ttk.Combobox(form, textvariable=variables["single"], values=ROUTING_CHOICES.get(key,()), width=18).grid(row=index, column=2, sticky="ew", padx=3, pady=3)
                for column in range(3, 6):
                    ttk.Label(form, text="—", style="Card.TLabel").grid(row=index, column=column, padx=3)
                continue
            if key in boolean_fields:
                ttk.Combobox(form, textvariable=variables["single"], values=("false", "true", "false;true"), state="readonly", width=15).grid(row=index, column=2, sticky="ew", padx=3, pady=3)
                for column in range(3, 6):
                    ttk.Label(form, text="—", style="Card.TLabel").grid(row=index, column=column, padx=3)
                continue
            entries = {}
            for column, field in enumerate(("single", "start", "end", "step"), start=2):
                entry = ttk.Entry(form, textvariable=variables[field], width=10)
                entry.grid(row=index, column=column, sticky="ew", padx=3, pady=3)
                entries[field] = entry

            def update_mode(*_, selected_mode=mode, widgets=entries):
                use_range = selected_mode.get() == "Aralık"
                for field, widget in widgets.items():
                    widget.configure(state="normal" if (field != "single") == use_range else "disabled")
                self._update_count()

            mode.trace_add("write", update_mode)
            update_mode()
        form.columnconfigure(0, weight=2)
        for column in range(1, 6):
            form.columnconfigure(column, weight=1)

    def _build_setup(self) -> None:
        # Reserve the action bar before packing the scrollable parameter area.
        footer = ttk.Frame(self.setup_tab, padding=(5, 10, 5, 10))
        footer.pack(side="bottom", fill="x")
        canvas = tk.Canvas(self.setup_tab, bg="#111827", highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.setup_tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_window, width=event.width))
        def scroll_setup(event):
            widget = event.widget
            while widget is not None:
                if widget == self.setup_tab:
                    canvas.yview_scroll(int(-event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)
        self.bind_all("<MouseWheel>", scroll_setup, add="+")
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(1, weight=1)

        identity = self._card(content, "1 · İş Kimliği ve Dönem")
        identity.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        row = ttk.Frame(identity, style="Card.TFrame")
        row.pack(fill="x")
        self.job_id_var = tk.StringVar(value=datetime.now().strftime("TEST-%Y%m%d-%H%M"))
        self.start_var = tk.StringVar(value=self.config_data.get("download", {}).get("start", "2025-09-01"))
        self.end_var = tk.StringVar(value=self.config_data.get("download", {}).get("end", date.today().isoformat()))
        self.max_runs_var = tk.StringVar(value="500")
        self.decision_interval_var = tk.StringVar(value=self.config_data.get("interval", "30m"))
        self.auto_download_var = tk.BooleanVar(value=True)
        self.max_runs_var.trace_add("write", lambda *_: self._update_count())
        for label, variable, width in (("İş adı", self.job_id_var, 28), ("Başlangıç", self.start_var, 12), ("Bitiş", self.end_var, 12), ("Güvenlik üst sınırı", self.max_runs_var, 10)):
            box = ttk.Frame(row, style="Card.TFrame")
            box.pack(side="left", padx=(0, 16))
            ttk.Label(box, text=label, style="Card.TLabel").pack(anchor="w")
            ttk.Entry(box, textvariable=variable, width=width).pack(anchor="w", pady=(3, 0))
        interval_box = ttk.Frame(row, style="Card.TFrame")
        interval_box.pack(side="left", padx=(0, 16))
        ttk.Label(interval_box, text="Test karar TF", style="Card.TLabel").pack(anchor="w")
        ttk.Combobox(interval_box, textvariable=self.decision_interval_var, values=("5m", "30m"),
                     state="readonly", width=8).pack(anchor="w", pady=(3, 0))

        left = ttk.Frame(content)
        right = ttk.Frame(content)
        left.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        right.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        ttk.Label(identity, text="Tarihler UTC'dir; bitiş günü dahil değildir. Araştırma motoru: Binance USD-M · 5m veya 30m karar grafiği.", style="Card.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(identity, text="Testten önce eksik yerel veriyi otomatik kontrol et ve indir", variable=self.auto_download_var).pack(anchor="w", pady=(5, 0))
        self.model_label = ttk.Label(identity, text="Test edilen: yerleşik Python Master modeli. Yapıştırılan Pine kodu çalıştırılmıyor.", style="Card.TLabel")
        self.model_label.pack(anchor="w", pady=4)

        import_row = ttk.Frame(identity, style="Card.TFrame")
        import_row.pack(fill="x", pady=(8, 0))
        self.tv_workbook_path_var = tk.StringVar()
        ttk.Label(import_row, text="TradingView Strategy Tester Excel", style="Card.TLabel").pack(side="left")
        ttk.Entry(import_row, textvariable=self.tv_workbook_path_var, width=58).pack(side="left", padx=6)
        ttk.Button(import_row, text="Dosya seç", command=self._choose_tradingview_workbook).pack(side="left")
        ttk.Button(import_row, text="Ayarları içe aktar", command=self.import_tradingview_workbook).pack(side="left", padx=6)
        self.tv_import_status = ttk.Label(identity, text="Referans Excel yüklenmedi.", style="Card.TLabel", wraplength=1050)
        self.tv_import_status.pack(anchor="w", pady=(4, 0))
        ttk.Button(identity, text="Ekrandan ayar tara · İndikatörler", command=self.open_settings_scanner).pack(anchor="w", pady=6)

        symbols_card = self._card(left, "2 · Coinler")
        symbols_card.pack(fill="both", expand=True)
        search_line = ttk.Frame(symbols_card, style="Card.TFrame")
        search_line.pack(fill="x", pady=(0, 7))
        ttk.Label(search_line, text="Ara", style="Card.TLabel").pack(side="left")
        self.symbol_search_var = tk.StringVar()
        self.symbol_search_var.trace_add("write", lambda *_: self._render_symbols())
        ttk.Entry(search_line, textvariable=self.symbol_search_var, width=18).pack(side="left", padx=6)

        add_line = ttk.Frame(symbols_card, style="Card.TFrame")
        add_line.pack(fill="x", pady=(0, 7))
        self.new_symbol_var = tk.StringVar()
        ttk.Entry(add_line, textvariable=self.new_symbol_var, width=17).pack(side="left")
        ttk.Button(add_line, text="Coin Ekle", command=self.add_symbol).pack(side="left")
        ttk.Label(add_line, text="Örnek: BTCUSDT / OPUSDT / ETHUSDC", style="Card.TLabel").pack(side="left", padx=8)
        ttk.Label(symbols_card, text="BTC ve ETH de normal test paritesidir; otomatik piyasa filtresi değildir. Eksik veri testten önce indirilmeye çalışılır; indirilemeyen parite atlanır, diğerleri devam eder.", style="Card.TLabel", wraplength=850).pack(anchor="w")

        self.symbol_grid = ttk.Frame(symbols_card, style="Card.TFrame")
        self.symbol_grid.pack(fill="both", expand=True)
        self._render_symbols()
        self.selection_label = ttk.Label(symbols_card, text="", style="Card.TLabel", wraplength=900)
        self.selection_label.pack(anchor="w", pady=5)
        actions = ttk.Frame(symbols_card, style="Card.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Tümünü seç", command=lambda: self._set_vars(self.symbol_vars, True)).pack(side="left")
        ttk.Button(actions, text="Temizle", command=lambda: self._set_vars(self.symbol_vars, False)).pack(side="left", padx=6)
        self.download_interval_var = tk.StringVar(value=self.config_data.get("interval", "30m"))
        ttk.Button(actions, text="Seçili Veriyi İndir", command=self.download_selected_symbols).pack(side="right")
        ttk.Combobox(actions, textvariable=self.download_interval_var,
                     values=("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"),
                     state="readonly", width=6).pack(side="right", padx=(5, 3))
        ttk.Label(actions, text="Veri TF", style="Card.TLabel").pack(side="right")

        settings = ttk.Notebook(right)
        self.gate_settings_notebook = settings
        self.gate_feed_manifest_var = tk.StringVar(value="")
        self.save_gate_trace_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right,text="Bar bazlı gate denetim dosyasını kaydet",variable=self.save_gate_trace_var).pack(anchor="w")
        ttk.Label(right,text="Gate veri manifesti (isteğe bağlı JSON yolu):").pack(anchor="w")
        ttk.Entry(right,textvariable=self.gate_feed_manifest_var).pack(fill="x",pady=(0,8))
        ttk.Button(right,text="Gate veri manifesti seç",command=lambda:self.gate_feed_manifest_var.set(
            filedialog.askopenfilename(parent=self,title="Gate OHLCV manifesti",filetypes=[("JSON","*.json")]) or self.gate_feed_manifest_var.get())).pack(anchor="w")
        settings.pack(fill="both", expand=True)
        master_page = ttk.Frame(settings, padding=10)
        settings.add(master_page, text="Master Strategy")
        choices = self._card(master_page, "3 · Davranış Varyasyonları")
        choices.pack(fill="x")
        ttk.Label(choices, text="Birden fazla kutu seçilirse kurallar aynı pozisyonda birleşmez; her seçenek ayrı bir test koşusu oluşturur.", style="Card.TLabel", wraplength=900).pack(anchor="w", pady=(0, 4))
        for title, options, holder, defaults in (
            ("Çıkış ailesi", EXIT_OPTIONS, self.exit_vars, {"NONE", "TILLSON"}),
            ("ER", ER_OPTIONS, self.er_vars, {"v2"}),
            ("Shadow/Winrate", WINRATE_OPTIONS, self.winrate_vars, {"SH_WEIGHTED_HOLD", "SH_WEIGHTED_CLOSE"}),
        ):
            ttk.Label(choices, text=title, style="Card.TLabel", font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(6, 2))
            line = ttk.Frame(choices, style="Card.TFrame")
            line.pack(fill="x")
            for option in options:
                variable = tk.BooleanVar(value=option in defaults)
                holder[option] = variable
                label = {"NONE": "Ek çıkış yok", "OFF": "Kapalı", "RSI_SMA": "RSI / SMA", "SH_WEIGHTED_HOLD": "Shadow: engelde pozisyonu koru", "SH_WEIGHTED_CLOSE": "Shadow: engelde pozisyonu kapat"}.get(option, option)
                ttk.Checkbutton(line, text=label, variable=variable, command=self._update_count).pack(side="left", padx=(0, 10))

        grid_card = self._card(master_page, "4 · Master Strategy Parametre Izgarası")
        grid_card.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(
            grid_card,
            text="Her satırda Tek değer/liste veya Aralık seçin. Liste: 2;2,5;3 · Ondalık: 0,17. Adımla ulaşılamayan bitiş eklenmez.",
            style="Card.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        self._parameter_grid_form(
            grid_card, PARAMETERS, self.config_data.get("strategy", {}),
            integer_fields=INTEGER_PARAMETERS, boolean_fields=BOOLEAN_PARAMETERS,
        )
        self._parameter_grid_form(grid_card,tuple((k,k) for k in STRATEGY_DEFAULTS),GATE_DEFAULTS,
            integer_fields=GATE_INTEGER_PARAMETERS,boolean_fields=GATE_BOOLEAN_PARAMETERS)

        for tab_name in ("Master Gate","Bias Gate","High/Low Gate","VWAP Gate"):
            description, specs = GATE_PARAMETER_TABS[tab_name]
            gate_page = ttk.Frame(settings, padding=10)
            settings.add(gate_page, text=tab_name)
            card = self._card(gate_page, tab_name)
            card.pack(fill="both", expand=True)
            ttk.Label(card, text=description, style="Card.TLabel", wraplength=800).pack(anchor="w", pady=(0, 8))
            ttk.Label(card, text="Liste ve aralık yapısı Master Strategy ile aynıdır. Seçilen her değer diğer seçili ayarlarla çarpılır.", style="Card.TLabel", wraplength=800).pack(anchor="w", pady=(0, 8))
            self._parameter_grid_form(
                card, specs, GATE_DEFAULTS,
                integer_fields=GATE_INTEGER_PARAMETERS, boolean_fields=GATE_BOOLEAN_PARAMETERS,
            )

        self.count_label = ttk.Label(footer, text="Planlanan koşu: 0", font=("Segoe UI Semibold", 11))
        self.count_label.pack(side="left")
        ttk.Button(footer, text="Kuyruğa Ekle", style="Accent.TButton", command=self.add_job).pack(side="right")
        ttk.Button(footer, text="Test Et (Ekle ve Başlat)", style="Accent.TButton", command=lambda: self.add_job(run=True)).pack(side="right", padx=8)
        self._update_count()

    def _build_queue(self) -> None:
        toolbar = ttk.Frame(self.queue_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Yenile", command=self.refresh_jobs).pack(side="left")
        ttk.Button(toolbar, text="Başlat / Yeniden Dene", command=self.run_selected_job).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Sonuçları Göster", command=self.show_results).pack(side="left")
        ttk.Button(toolbar, text="Rapor Klasörünü Aç", command=self.open_selected_report).pack(side="left")
        supervisor_bar = ttk.Frame(self.queue_tab)
        supervisor_bar.pack(fill="x", pady=(0, 8))
        ttk.Button(supervisor_bar, text="Kesilenleri Kurtar", command=self.recover_jobs).pack(side="left")
        ttk.Button(supervisor_bar, text="Gözetmeni Başlat", command=self.start_supervisor).pack(side="right")
        ttk.Button(supervisor_bar, text="Gözetmeni Durdur (iş bitince)", command=self.stop_supervisor).pack(side="right", padx=6)

        categories = ttk.LabelFrame(self.queue_tab, text="Test kategorileri", padding=8)
        categories.pack(fill="x", pady=(0, 8))
        self.category_name_var = tk.StringVar()
        self.category_filter_var = tk.StringVar(value="Tümü")
        ttk.Label(categories, text="Kategori adı").pack(side="left")
        self.category_entry = ttk.Combobox(categories, textvariable=self.category_name_var, width=20)
        self.category_entry.pack(side="left", padx=5)
        ttk.Button(categories, text="Oluştur", command=lambda: self.edit_category()).pack(side="left")
        ttk.Button(categories, text="Seçili testlere ata", command=lambda: self.edit_category(assign=True)).pack(side="left", padx=5)
        ttk.Button(categories, text="Kategoriden çıkar", command=lambda: self.edit_category(assign=True, clear=True)).pack(side="left")
        ttk.Label(categories, text="Göster").pack(side="left", padx=(12, 4))
        self.category_filter = ttk.Combobox(categories, textvariable=self.category_filter_var, state="readonly", width=18)
        self.category_filter.pack(side="left")
        self.category_filter.bind("<<ComboboxSelected>>", lambda _event: self.refresh_jobs())
        ttk.Label(self.queue_tab, text="Ctrl / Shift ile birden fazla test seçebilirsiniz. Kategoriden çıkarmak sonuçları silmez.").pack(anchor="w", pady=(0, 5))
        columns = ("id", "category", "status", "runs", "symbols", "period", "report")
        self.job_tree = ttk.Treeview(self.queue_tab, columns=columns, show="headings", selectmode="extended")
        headings = {"id": "İş", "category": "Kategori", "status": "Durum", "runs": "Koşu", "symbols": "Coin", "period": "Dönem", "report": "Rapor"}
        widths = {"id": 230, "category": 130, "status": 110, "runs": 80, "symbols": 70, "period": 190, "report": 420}
        for key in columns:
            self.job_tree.heading(key, text=headings[key])
            self.job_tree.column(key, width=widths[key], anchor="w")
        self.job_tree.pack(fill="both", expand=True)
        self.job_tree.bind("<Double-1>", lambda _event: self.open_selected_report())
        self.status_detail = ttk.Label(self.queue_tab, text="", style="Muted.TLabel")
        self.status_detail.pack(anchor="w", pady=(8, 0))

    def _build_log(self) -> None:
        self.log_text = tk.Text(self.log_tab, bg="#0b1020", fg="#d1d5db", insertbackground="white", font=("Cascadia Mono", 9), wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self._log("Arayüz hazır. Testler yerel bilgisayarda ve kredi kullanmadan çalışır.")

    def _build_experiments(self) -> None:
        """Controlled component-ablation planner, deliberately separate from sweeps."""
        intro = self._card(self.experiments_tab, "Baseline → Gate Ablation")
        intro.pack(fill="x", pady=(0, 10))
        ttk.Label(
            intro,
            text=(
                "Önce tek coin, tek ayar kombinasyonu ile referans koşu oluşturulur. Ardından yalnız aktif "
                "bileşenlerden biri kapatılır; diğer tüm ayarlar aynı kalır. Bu ekran optimizasyon yapmaz ve "
                "tek coin sonucunu production kanıtı saymaz."
            ),
            style="Card.TLabel", wraplength=1050,
        ).pack(anchor="w")
        ttk.Label(
            intro,
            text="Ön koşul: Yeni Test ekranında 1 coin; Exit, ER ve Shadow/Winrate için birer seçim; her parametre için tek değer.",
            style="Card.TLabel", foreground="#fbbf24", wraplength=1050,
        ).pack(anchor="w", pady=(7, 0))

        controls = ttk.Frame(self.experiments_tab)
        controls.pack(fill="x", pady=(0, 8))
        self.experiment_id_var = tk.StringVar(value=datetime.now().strftime("ABLATION-%Y%m%d-%H%M"))
        self.experiment_min_trades_var = tk.StringVar(value="10")
        ttk.Label(controls, text="Araştırma kimliği").pack(side="left")
        ttk.Entry(controls, textvariable=self.experiment_id_var, width=31).pack(side="left", padx=(6, 16))
        ttk.Label(controls, text="Minimum işlem").pack(side="left")
        ttk.Entry(controls, textvariable=self.experiment_min_trades_var, width=7).pack(side="left", padx=6)
        ttk.Button(controls, text="Baseline + Ablation Planını Kuyruğa Ekle", style="Accent.TButton", command=self.create_ablation_plan).pack(side="left", padx=8)
        ttk.Button(controls, text="Sonuçları Yenile", command=self.refresh_experiment_results).pack(side="right")

        self.experiment_heading = ttk.Label(
            self.experiments_tab,
            text="Henüz plan oluşturulmadı. Plan yalnız kuyruk oluşturur; Çalışan Testler ekranından gözetmeni başlatabilirsiniz.",
            style="Muted.TLabel", wraplength=1100,
        )
        self.experiment_heading.pack(anchor="w", pady=(3, 7))
        columns = ("label", "status", "trades", "pnl", "delta_pnl", "drawdown", "delta_dd", "verdict")
        self.experiment_tree = ttk.Treeview(self.experiments_tab, columns=columns, show="headings", height=13)
        headings = {
            "label": "Koşu", "status": "Durum", "trades": "İşlem", "pnl": "Net P&L", "delta_pnl": "Baseline farkı",
            "drawdown": "Maks. DD", "delta_dd": "DD farkı", "verdict": "Yorum",
        }
        widths = {"label": 190, "status": 120, "trades": 80, "pnl": 115, "delta_pnl": 125, "drawdown": 110, "delta_dd": 110, "verdict": 170}
        for key in columns:
            self.experiment_tree.heading(key, text=headings[key])
            self.experiment_tree.column(key, width=widths[key], anchor="w")
        self.experiment_tree.pack(fill="both", expand=True)
        self.experiment_warning = ttk.Label(self.experiments_tab, text="", style="Muted.TLabel", wraplength=1100)
        self.experiment_warning.pack(anchor="w", pady=(7, 0))

        audit = self._card(self.experiments_tab, "Sistem Denetimi · Bağlantılar ve Aralıklar")
        audit.pack(fill="x", pady=(12, 0))
        ttk.Label(audit, text=(
            "Master Strategy → Master Gate → G6 / EXT1 / EXT2 bağlantılarını; VWAP, Bias ve HL üreticilerini; "
            "seçilen sayısal aralıkları tek faktör değiştirerek sınar. Her alan düşük/orta/yüksek en fazla üç örneğe iner; "
            "alanlar birbiriyle çarpılmaz."), style="Card.TLabel", wraplength=1050).pack(anchor="w")
        row = ttk.Frame(audit, style="Card.TFrame"); row.pack(fill="x", pady=(8, 0))
        self.system_audit_id_var = tk.StringVar(value=datetime.now().strftime("SYSTEM-AUDIT-%Y%m%d-%H%M"))
        ttk.Label(row, text="Denetim kimliği", style="Card.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.system_audit_id_var, width=33).pack(side="left", padx=6)
        ttk.Button(row, text="Sistem Denetimini Kuyruğa Ekle", style="Accent.TButton", command=self.create_system_audit).pack(side="left")
        ttk.Button(row, text="Denetim Excel Raporunu Güncelle", command=self.refresh_system_audit).pack(side="right")
        self.system_audit_status = ttk.Label(audit, text="Henüz sistem denetimi oluşturulmadı.", style="Card.TLabel", wraplength=1050)
        self.system_audit_status.pack(anchor="w", pady=(7, 0))

    def create_ablation_plan(self) -> None:
        try:
            experiment_id = safe_job_id(self.experiment_id_var.get())[:48]
            base_job = self._build_job()
            plan = build_baseline_ablation_plan(base_job, experiment_id=experiment_id)
            append_jobs(QUEUE_PATH, plan.jobs)
            manifest = {
                "experiment_id": plan.experiment_id,
                "purpose": "ONE_COIN_COMPONENT_ABLATION",
                "created_at_local": datetime.now().astimezone().isoformat(),
                "active_interventions": plan.interventions,
                "job_ids": [job["id"] for job in plan.jobs],
                "warning": "Bu plan tek coin keşif kanıtıdır; OOS, çoklu-coin ve Pine parity olmadan promotion yapılamaz.",
            }
            atomic_write_json(EXPERIMENTS_ROOT / experiment_id / "plan.json", manifest)
        except Exception as exc:
            messagebox.showerror("Araştırma planı oluşturulamadı", str(exc), parent=self)
            return
        self._log(f"Ablation planı kuyruğa eklendi: {experiment_id} · {len(plan.jobs)} kontrollü koşu.")
        self.refresh_jobs(select_id=plan.jobs[0]["id"])
        self.refresh_experiment_results()
        self.notebook.select(self.experiments_tab)

    def refresh_experiment_results(self) -> None:
        for item in self.experiment_tree.get_children():
            self.experiment_tree.delete(item)
        try:
            experiment_id = safe_job_id(self.experiment_id_var.get())[:48]
            minimum_trades = int(self.experiment_min_trades_var.get())
            if minimum_trades < 1:
                raise ValueError("Minimum işlem en az 1 olmalı")
            comparison = compare_plan(QUEUE_PATH, experiment_id, minimum_trades=minimum_trades)
        except Exception as exc:
            self.experiment_heading.configure(text=f"Sonuç karşılaştırması hazır değil: {exc}")
            self.experiment_warning.configure(text="")
            return
        if comparison["status"] == "WAITING_BASELINE":
            self.experiment_heading.configure(text=f"{experiment_id}: baseline henüz tamamlanmadı.")
            self.experiment_warning.configure(text="Önce baseline tamamlanmalı; ablation yorumları ondan sonra geçerlidir.")
            return
        base = comparison["baseline"]
        self.experiment_tree.insert("", "end", values=(
            "BASELINE", "Tamamlandı", base["closed_trades"], f"{base['total_pnl_usdt']:.2f}", "—",
            f"{base['max_drawdown_usdt']:.2f}", "—", "REFERANS",
        ))
        for row in comparison["rows"]:
            if row["status"] == "WAITING":
                self.experiment_tree.insert("", "end", values=(row["label"], "Bekliyor", "—", "—", "—", "—", "—", "SONUÇ BEKLİYOR"))
                continue
            verdict = {
                "REMOVAL_WORSENS": "KALDIRMA ZARARLI GÖRÜNÜYOR",
                "REMOVAL_IMPROVES": "KALDIRMA İYİLEŞTİRİYOR",
                "MIXED": "KARIŞIK / İNCELE",
                "INSUFFICIENT_TRADES": "YETERSİZ İŞLEM",
            }.get(row["verdict"], row["verdict"])
            self.experiment_tree.insert("", "end", values=(
                row["label"], "Tamamlandı", row["closed_trades"], f"{row['total_pnl_usdt']:.2f}",
                f"{row['delta_total_pnl_usdt']:+.2f}", f"{row['max_drawdown_usdt']:.2f}",
                f"{row['delta_max_drawdown_usdt']:+.2f}", verdict,
            ))
        self.experiment_heading.configure(text=f"{experiment_id} · {comparison['status']} · baseline: {base['closed_trades']} işlem, {base['total_pnl_usdt']:.2f} net P&L")
        self.experiment_warning.configure(text=comparison["warning"])
        atomic_write_json(EXPERIMENTS_ROOT / experiment_id / "ablation_summary.json", comparison)

    def create_system_audit(self) -> None:
        try:
            audit_id = safe_job_id(self.system_audit_id_var.get())[:48]
            base_job = self._build_job(audit_mode=True)
            jobs, manifest = build_system_audit_plan(base_job, audit_id)
            append_jobs(QUEUE_PATH, jobs)
            audit_dir = EXPERIMENTS_ROOT / audit_id
            atomic_write_json(audit_dir / "system_audit_plan.json", manifest)
        except Exception as exc:
            messagebox.showerror("Sistem denetimi oluşturulamadı", str(exc), parent=self)
            return
        self._log(f"Sistem denetimi kuyruğa eklendi: {audit_id} · {len(jobs)} iş. Her aralık en fazla üç örnekle sınırlandı.")
        self.system_audit_status.configure(text=f"{audit_id}: {len(jobs)} kontrollü iş kuyruğa eklendi. Çalışan Testler'den gözetmeni başlat.")
        self.refresh_jobs(select_id=jobs[0]["id"])
        self.notebook.select(self.queue_tab)

    def refresh_system_audit(self) -> None:
        try:
            audit_id = safe_job_id(self.system_audit_id_var.get())[:48]
            summary = audit_summary(QUEUE_PATH, audit_id)
            audit_dir = EXPERIMENTS_ROOT / audit_id
            json_path = audit_dir / "system_audit_summary.json"
            atomic_write_json(json_path, summary)
            node = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
            builder = LAB_ROOT / "build_system_audit_workbook.mjs"
            output = audit_dir / "sistem_denetim_raporu.xlsx"
            if node.exists() and builder.exists() and (LAB_ROOT / "node_modules" / "@oai" / "artifact-tool").exists():
                subprocess.run([str(node), str(builder), str(json_path), str(output)], cwd=LAB_ROOT, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            verdict = summary["audit_agent"]
            self.system_audit_status.configure(text=f"{audit_id}: Denetim Agentı {verdict['verdict']} · {verdict['reason']} · Excel: {output.name if output.exists() else 'hazırlanıyor'}")
        except Exception as exc:
            self.system_audit_status.configure(text=f"Denetim raporu üretilemedi: {exc}")

    def _build_results(self):
        ttk.Button(self.results_tab, text="Seçili sonucu yeni teste aktar", command=self.apply_result_seed).pack(anchor="w")
        self.result_heading = ttk.Label(self.results_tab, text="Çalışan Testler'den bir iş seçip Sonuçları Göster'e basın.", wraplength=1000)
        self.result_heading.pack(anchor="w", pady=8)
        ttk.Label(self.results_tab, text="Araştırma sonucu ≠ doğrulanmış strateji. Başlığa tıklayarak sıralayın; satırı seçerek tüm ayarları görün.", wraplength=1000).pack(anchor="w")
        self.result_columns = (
            "total_pnl_usdt", "profit_factor", "max_drawdown_pct_initial", "closed_trades", "win_rate_pct",
            "symbol", "status", "combo_id", "parameter_id", "coverage", "available_bars",
            "tested_start", "tested_end_exclusive", "skip_reason",
        )
        frame = ttk.Frame(self.results_tab)
        frame.pack(fill="both", expand=True, pady=8)
        self.result_tree = ttk.Treeview(frame, columns=self.result_columns, show="headings", height=5)
        labels = (
            "Kâr (açık dahil)", "Kâr faktörü", "Düşüş / ilk sermaye %", "İşlem", "Kazanma %",
            "Parite", "Durum", "Davranış", "Ayar", "Veri kapsamı", "Mum sayısı",
            "Test başlangıcı UTC", "Test bitişi UTC (hariç)", "Atlama nedeni",
        )
        for key, label in zip(self.result_columns, labels):
            self.result_tree.heading(key, text=label, command=lambda k=key: self.sort_results(k))
            self.result_tree.column(key, width=140 if key != "combo_id" else 320, stretch=False)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.result_tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.result_tree.xview)
        self.result_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.result_analysis = ResultAnalysis(self.results_tab)
        self.result_analysis.pack(fill="both", expand=True)
        self.result_folder = REPORTS_ROOT
        self.result_rows = {}
        self.result_tree.bind("<<TreeviewSelect>>", self.show_result_detail)
        self.result_sort_reverse = {}

    def show_results(self):
        job_id = self._selected_job_id()
        job = next((row for row in read_json(QUEUE_PATH, {"jobs": []})["jobs"] if row["id"] == job_id), None)
        if not job:
            messagebox.showinfo("İş seçin", "Önce bir test seçin.", parent=self)
            return
        folder = Path(job.get("output_dir") or (Path(job["report"]).parent if job.get("report") else REPORTS_ROOT / job_id))
        path = folder / "job_results.csv"
        if not path.exists():
            messagebox.showinfo("Sonuç hazır değil", job.get("error") or "Bu işin sonuç dosyası henüz oluşmadı.", parent=self)
            return
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except Exception as exc:
            messagebox.showerror("Rapor okunamadı", str(exc), parent=self)
            return
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_folder = folder
        self.result_parent_job = job
        self.result_rows.clear()
        for row in rows:
            values = []
            for key in self.result_columns:
                raw = row.get(key, "")
                if key == "status":
                    raw = {"COMPLETE": "Tamamlandı", "SKIPPED": "Atlandı"}.get(raw, raw)
                if key == "coverage":
                    raw = {"FULL": "Tam dönem", "PARTIAL": "Mevcut dönem"}.get(raw, raw)
                try:
                    values.append(f"{float(raw):.2f}" if raw and key not in {"symbol", "combo_id", "parameter_id"} else raw or "—")
                except ValueError:
                    values.append(raw)
            item = self.result_tree.insert("", "end", values=values)
            self.result_rows[item] = row
        self.result_heading.configure(text=f"{job_id} · {len(rows)} koşu · {job.get('start')} → {job.get('end')} (UTC, bitiş hariç)\n{job.get('excel_report_error', '')}")
        self.notebook.select(self.results_tab)
        items = self.result_tree.get_children()
        if items:
            self.result_tree.selection_set(items[0])
            self.show_result_detail()

    def sort_results(self, key):
        numeric = key not in {"symbol", "combo_id", "parameter_id", "status", "coverage", "skip_reason", "tested_start", "tested_end_exclusive"}
        def sort_key(item):
            raw = self.result_rows[item].get(key, "")
            try:
                return float(raw) if numeric and raw else float("-inf") if numeric else raw
            except ValueError:
                return float("-inf")
        reverse = not self.result_sort_reverse.get(key, False)
        for index, item in enumerate(sorted(self.result_tree.get_children(), key=sort_key, reverse=reverse)):
            self.result_tree.move(item, "", index)
        self.result_sort_reverse[key] = reverse

    def show_result_detail(self, _event=None):
        selection = self.result_tree.selection()
        if selection:
            self.result_analysis.load(self.result_rows[selection[0]], self.result_folder)

    def apply_result_seed(self):
        selection = self.result_tree.selection()
        if not selection:
            messagebox.showinfo("Sonuç seçin", "Başlangıç olarak kullanacağınız sonuç satırını seçin.", parent=self)
            return
        try:
            seed = result_seed(self.result_rows[selection[0]], self.result_parent_job)
            missing = set(self.grid_vars) - set(seed['values'])
            if missing:
                raise ValueError('Kaynakta eksik parametreler: ' + ', '.join(sorted(missing)))
            for key, options in (('exit_family', self.exit_vars), ('er', self.er_vars), ('winrate_state', self.winrate_vars)):
                if seed['categorical'][key] not in options:
                    raise ValueError('Desteklenmeyen davranış: ' + seed['categorical'][key])
            _, evidence = load_feed_manifest(seed['context'].get('gate_feed_manifest', ''))
            if 'gate_feed_snapshot' in seed['context'] and evidence != seed['context']['gate_feed_snapshot']:
                raise ValueError('Kaynak testin gate verisi değişmiş; aynı başlangıç olarak aktarılamıyor.')
        except Exception as exc:
            messagebox.showerror('Sonuç aktarılamadı', str(exc), parent=self)
            return
        self.config_data = seed['config']
        self.seed_context = seed['context']
        self.seed_lineage = {**seed['lineage'], 'result_folder': str(self.result_folder)}
        self.universe_snapshot = None
        self.pine_candidate = None
        for key, fields in self.grid_vars.items():
            value = seed['values'][key]
            fields['single'].set(str(value).lower() if isinstance(value, bool) else str(value))
            for field in ('start', 'end', 'step'):
                fields[field].set('')
            self.grid_modes[key].set('Tek değer / liste')
        for key, options in (('exit_family', self.exit_vars), ('er', self.er_vars), ('winrate_state', self.winrate_vars)):
            for name, variable in options.items():
                variable.set(name == seed['categorical'][key])
        self.symbol_vars.setdefault(seed['symbol'], tk.BooleanVar(value=False))
        if seed['symbol'] not in self.extra_symbols:
            self.extra_symbols.append(seed['symbol'])
        for name, variable in self.symbol_vars.items():
            variable.set(name == seed['symbol'])
        self.symbol_search_var.set('')
        self._render_symbols()
        self.start_var.set(seed['start'])
        self.end_var.set(seed['end'])
        self.job_id_var.set(datetime.now().strftime('DEVAM-%Y%m%d-%H%M%S-%f'))
        self.gate_feed_manifest_var.set(seed['context'].get('gate_feed_manifest', ''))
        self.save_gate_trace_var.set(seed['context'].get('save_gate_trace', False))
        self.model_label.configure(text=f"Başlangıç: {seed['lineage']['job_id']} / {seed['symbol']} / {seed['lineage']['parameter_id']}. Ayarlar yüklendi; liste veya aralıkla yeni varyasyonlar oluşturabilirsiniz. Aynı dönemde yeni aramalar bağımsız OOS sayılmaz.")
        self._update_count()
        self.notebook.select(self.setup_tab)

    def _build_code(self) -> None:
        toolbar = ttk.Frame(self.code_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        self.component_var = tk.StringVar(value="Master Strategy")
        self.version_label_var = tk.StringVar(value=datetime.now().strftime("ADAY-%Y%m%d-%H%M"))
        ttk.Label(toolbar, text="Bileşen").pack(side="left")
        component = ttk.Combobox(toolbar, textvariable=self.component_var, values=list(COMPONENT_FILES), state="readonly", width=22)
        component.pack(side="left", padx=(6, 16))
        ttk.Label(toolbar, text="Sürüm etiketi").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.version_label_var, width=24).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Mevcut Kaynağı Yükle", command=self.load_source_code).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Adayı Denetle", command=self.inspect_candidate).pack(side="left")
        ttk.Button(toolbar, text="Aday Sürümü Kaydet", style="Accent.TButton", command=self.save_candidate).pack(side="right")

        warning = ttk.Frame(self.code_tab, style="Card.TFrame", padding=10)
        warning.pack(fill="x", pady=(0, 8))
        ttk.Label(
            warning,
            text="Pine kodu burada saklanır; Python'a otomatik çevrilmez. Seçili Pine için yürütücü yoksa test engellenir. Statik kontrol derleme veya davranış doğrulaması değildir.",
            style="Card.TLabel",
            wraplength=1000,
        ).pack(anchor="w")

        editor_frame = ttk.Frame(self.code_tab)
        editor_frame.pack(fill="both", expand=True)
        editor_frame.columnconfigure(0, weight=3)
        editor_frame.columnconfigure(1, weight=2)
        editor_frame.rowconfigure(0, weight=1)
        self.code_text = tk.Text(editor_frame, bg="#0b1020", fg="#d1d5db", insertbackground="white", font=("Cascadia Mono", 9), wrap="none", undo=True)
        self.code_text.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        side = ttk.Frame(editor_frame, style="Card.TFrame", padding=10)
        side.grid(row=0, column=1, sticky="nsew")
        ttk.Label(side, text="Denetim Sonucu", style="Card.TLabel", font=("Segoe UI Semibold", 11)).pack(anchor="w")
        self.code_audit_text = tk.Text(side, height=14, bg="#172033", fg="#e5e7eb", font=("Segoe UI", 9), wrap="word", state="disabled")
        self.code_audit_text.pack(fill="both", expand=True, pady=(8, 10))
        ttk.Label(side, text="Kaydedilmiş Adaylar", style="Card.TLabel", font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0, 5))
        self.version_tree = ttk.Treeview(side, columns=("component", "label", "status"), show="headings", height=6)
        for key, title, width in (("component", "Bileşen", 115), ("label", "Etiket", 140), ("status", "Durum", 120)):
            self.version_tree.heading(key, text=title)
            self.version_tree.column(key, width=width, anchor="w")
        self.version_tree.pack(fill="x", pady=(0, 8))
        ttk.Button(side, text="Aday Sürümler Klasörünü Aç", command=self.open_candidates_folder).pack(fill="x")
        ttk.Button(side, text="Seçili Pine'ı test kaynağı yap", command=self.select_candidate).pack(fill="x", pady=4)
        ttk.Button(side, text="Yerleşik Python modeline dön", command=self.use_builtin_model).pack(fill="x")
        ttk.Button(side, text="Arayüzü Yeniden Başlat", command=self.restart_interface).pack(fill="x", pady=(6, 0))
        self.refresh_candidate_versions()

    def _set_audit_text(self, text: str) -> None:
        self.code_audit_text.configure(state="normal")
        self.code_audit_text.delete("1.0", "end")
        self.code_audit_text.insert("1.0", text)
        self.code_audit_text.configure(state="disabled")

    def load_source_code(self) -> None:
        path = PROJECT_ROOT / "sources" / COMPONENT_FILES[self.component_var.get()]
        try:
            code = path.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Kaynak okunamadı", str(exc), parent=self)
            return
        self.code_text.delete("1.0", "end")
        self.code_text.insert("1.0", code)
        self._set_audit_text(f"Salt-okunur kaynak editöre kopyalandı:\n{path}\n\nHenüz hiçbir dosya değiştirilmedi.")

    def inspect_candidate(self) -> dict[str, Any] | None:
        try:
            source_path = PROJECT_ROOT / "sources" / COMPONENT_FILES[self.component_var.get()]
            source = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
            audit = audit_pine_candidate(self.code_text.get("1.0", "end"), source)
        except Exception as exc:
            messagebox.showerror("Kod denetlenemedi", str(exc), parent=self)
            return None
        issues = "\n".join(f"• {item}" for item in audit["issues"]) or "• Yalnız temel metin kontrollerinde işaret bulunmadı; kod derlenmedi veya doğrulanmadı"
        self._set_audit_text(
            f"SHA-256: {audit['sha256']}\n"
            f"Satır: {audit['line_count']}\n"
            f"Kaynağa göre eklenen/silinen: {audit['added_lines']}/{audit['deleted_lines']}\n"
            f"request.security çağrısı: {audit['request_security_calls']}\n"
            f"barstate.isconfirmed: {'VAR' if audit['has_barstate_confirmation'] else 'YOK'}\n\n"
            f"Bulgular:\n{issues}\n\n"
            "Durum: PARITY_REQUIRED\nPython motoruna otomatik bağlanmadı."
        )
        return audit

    def save_candidate(self) -> None:
        if self.inspect_candidate() is None:
            return
        try:
            target, record = save_pine_candidate(
                self.component_var.get(), self.version_label_var.get(),
                self.code_text.get("1.0", "end"),
            )
        except Exception as exc:
            messagebox.showerror("Aday kaydedilemedi", str(exc), parent=self)
            return
        self._log(f"Pine aday sürümü kaydedildi: {record['id']}")
        self._set_audit_text(
            self.code_audit_text.get("1.0", "end").strip()
            + f"\n\nKaydedildi:\n{target}\n\nSource-of-truth değiştirilmedi."
        )
        messagebox.showinfo("Aday sürüm kaydedildi", f"Kod güvenli aday klasörüne kaydedildi.\n\n{target}\n\nPython parity kurulmadan bu sürüm test motoruna bağlanmayacak.", parent=self)
        self.version_label_var.set(datetime.now().strftime("ADAY-%Y%m%d-%H%M%S"))
        self.refresh_candidate_versions()

    def select_candidate(self):
        selected = self.version_tree.selection()
        if not selected:
            messagebox.showinfo("Sürüm seçin", "Önce kaydedilmiş bir aday seçin.", parent=self)
            return
        index = self.version_tree.index(selected[0])
        self.pine_candidate = self.visible_candidates[index]
        self.model_label.configure(text=f"Seçili Pine: {self.pine_candidate['label']} — TEST ENGELLİ: Python yürütücüsü doğrulanmadı.")
        self.notebook.select(self.setup_tab)

    def use_builtin_model(self):
        self.pine_candidate = None
        self.model_label.configure(text="Test edilen: yerleşik Python Master modeli. Yapıştırılan Pine kodu çalıştırılmıyor.")

    def refresh_candidate_versions(self) -> None:
        for item in self.version_tree.get_children():
            self.version_tree.delete(item)
        registry = read_json(CANDIDATE_REGISTRY, {"versions": []})
        self.visible_candidates = list(reversed(registry.get("versions", [])[-30:]))
        for record in self.visible_candidates:
            self.version_tree.insert("", "end", values=(record.get("component", ""), record.get("label", ""), record.get("status", "")))

    def open_candidates_folder(self) -> None:
        CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(CANDIDATES_ROOT)  # type: ignore[attr-defined]

    def restart_interface(self) -> None:
        if (self.process and self.process.poll() is None) or (self.supervisor_process and self.supervisor_process.poll() is None):
            messagebox.showwarning("Test çalışıyor", "Test sürerken arayüz yeniden başlatılmaz. Önce işin tamamlanmasını bekleyin.", parent=self)
            return
        pythonw = bundled_python()
        executable = str(pythonw.with_name("pythonw.exe") if pythonw else Path(sys.executable))
        subprocess.Popen([executable, str(Path(__file__).resolve())], cwd=str(LAB_ROOT))
        self._closing = True
        self.stop_supervisor()
        self.destroy()

    def _set_vars(self, variables: dict[str, tk.BooleanVar], value: bool) -> None:
        for variable in variables.values():
            variable.set(value)
        self._update_count()

    def _all_config_symbols(self) -> list[str]:
        # Kalıcı piyasa kataloğu "Test Listeleri" ekranında yaşar. Yeni Test
        # ekranı başlangıçta tamamen boş kalır ve yalnız kullanıcının buraya
        # aktardığı/elle eklediği pariteleri gösterir.
        return list(dict.fromkeys(self.extra_symbols))

    def _symbol_category(self, symbol: str) -> str:
        if symbol in self.config_data.get("references", []):
            return "Referans"
        if symbol in self.config_data.get("calibration_only", []):
            return "Kalibrasyon"
        return "Aktif"

    def _render_symbols(self) -> None:
        if not hasattr(self, "symbol_grid"):
            return
        for child in self.symbol_grid.winfo_children():
            child.destroy()
        search = self.symbol_search_var.get().strip().upper() if hasattr(self, "symbol_search_var") else ""
        shown = [symbol for symbol in self._all_config_symbols() if search in symbol]
        for index, symbol in enumerate(shown):
            if symbol not in self.symbol_vars:
                # The catalog remains available, but every new test starts with
                # an intentionally empty symbol selection.
                self.symbol_vars[symbol] = tk.BooleanVar(value=False)
            text = symbol
            ttk.Checkbutton(self.symbol_grid, text=text, variable=self.symbol_vars[symbol], command=self._update_count).grid(
                row=index // 4, column=index % 4, sticky="w", padx=10, pady=3
            )

    def add_symbol(self) -> None:
        try:
            symbol = normalize_symbol(self.new_symbol_var.get())
            category_key = "symbols"
            self.config_data = register_symbol(CONFIG_PATH, symbol, category_key)
        except Exception as exc:
            messagebox.showerror("Coin eklenemedi", str(exc), parent=self)
            return
        if symbol not in self.extra_symbols:
            self.extra_symbols.append(symbol)
        if symbol not in self.symbol_vars:
            self.symbol_vars[symbol] = tk.BooleanVar()
        self.symbol_vars[symbol].set(category_key == "symbols")
        self.new_symbol_var.set("")
        self.symbol_search_var.set("")
        self._render_symbols()
        self._update_count()
        self._log(f"Test paritesi kaydedildi: {symbol}. Borsada veri bulunabilirliği henüz doğrulanmadı.")

    def open_settings_scanner(self) -> None:
        from screen_settings import SettingsScanner
        groups = {name: list(dict.fromkeys(key for _, key in specs if key in self.grid_vars))
                  for name, (_, specs) in GATE_PARAMETER_TABS.items()}
        SettingsScanner(self, groups)

    def _choose_tradingview_workbook(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="TradingView Strategy Tester Excel seçin",
            filetypes=[("Excel dosyası", "*.xlsx")],
        )
        if selected:
            self.tv_workbook_path_var.set(selected)

    def import_tradingview_workbook(self) -> None:
        """Load a Strategy Tester export as a pinned configuration/reference.

        The local engine currently operates on 30-minute Binance archives;
        settings can be loaded from any TV export but PnL is compared only
        when its symbol, timeframe and a single test configuration match.
        """
        try:
            profile = import_strategy_workbook(self.tv_workbook_path_var.get().strip())
            for key, value in profile["settings"].items():
                if key in self.grid_vars:
                    self.grid_modes[key].set("Tek değer / liste")
                    self.grid_vars[key]["single"].set(str(value).lower() if isinstance(value, bool) else str(value))
            for option in self.exit_vars.values():
                option.set(False)
            selected_exit = "MOST" if profile["settings"].get("exit_use_most") else "RSI_SMA" if profile["settings"].get("exit_use_rsi") else "TILLSON" if profile["settings"].get("exit_use_t3") else "NONE"
            self.exit_vars[selected_exit].set(True)
            for options, selected in ((self.er_vars, profile["categorical"]["er"]), (self.winrate_vars, profile["categorical"]["winrate_state"])):
                for option, variable in options.items():
                    variable.set(option == selected)
            symbol = profile.get("symbol")
            if symbol:
                if symbol not in self.extra_symbols:
                    self.extra_symbols.append(symbol)
                self.symbol_vars.setdefault(symbol, tk.BooleanVar()).set(True)
                self._render_symbols()
            period = profile["reference"].get("period", {})
            if period.get("start") and period.get("end_inclusive"):
                self.start_var.set(period["start"][:10])
                self.end_var.set((datetime.fromisoformat(period["end_inclusive"]).date() + timedelta(days=1)).isoformat())
            TRADINGVIEW_PROFILES_ROOT.mkdir(parents=True, exist_ok=True)
            digest = profile["reference"]["sha256"][:12]
            target = TRADINGVIEW_PROFILES_ROOT / f"{safe_job_id(Path(self.tv_workbook_path_var.get()).stem)[:55]}-{digest}.json"
            atomic_write_json(target, profile)
            self.tradingview_reference = {**profile["reference"], "profile_path": str(target)}
            chart = self.tradingview_reference["chart"]
            current_tf = self.decision_interval_var.get()
            match = chart.get("timeframe_minutes") == int(str(current_tf).removesuffix("m")) if str(current_tf).endswith("m") else False
            comparison = "Kâr kıyası mümkün" if match else (
                f"Kâr kıyası ENGELLENDİ: Excel {chart.get('timeframe')}, motor {current_tf}. "
                "Aynı TF motoru henüz kalibre/parity-denetimli değil; önce bunu ayrı doğrulamalıyız."
            )
            self.tv_import_status.configure(text=f"İçe aktarıldı: {chart.get('symbol')} · {chart.get('timeframe')} · profil sabitlendi. {comparison}")
            self._log(f"TradingView Excel içe aktarıldı: {Path(self.tv_workbook_path_var.get()).name}. {comparison}")
            self._update_count()
        except Exception as exc:
            messagebox.showerror("Excel içe aktarılamadı", str(exc), parent=self)

    def download_selected_symbols(self) -> None:
        symbols = self._selected(self.symbol_vars)
        if not symbols:
            messagebox.showinfo("Coin seçin", "Verisini indirmek için en az bir coin seçin.", parent=self)
            return
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Motor meşgul", "Bir test veya veri indirme işi zaten çalışıyor.", parent=self)
            return
        python = str(bundled_python() or Path(sys.executable))
        try:
            start, end = date.fromisoformat(self.start_var.get()), date.fromisoformat(self.end_var.get())
            if start >= end:
                raise ValueError("Başlangıç bitişten önce olmalı")
        except ValueError as exc:
            messagebox.showerror("Tarih hatası", str(exc), parent=self)
            return
        interval = self.download_interval_var.get()
        command = [python, "-u", str(LAB_ROOT / "quant_lab.py"), "download", "--interval", interval,
                   "--start", start.isoformat(), "--end", end.isoformat(), "--symbols", *symbols]
        process_env = os.environ.copy()
        process_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        self.notebook.select(self.log_tab)
        self._log(f"Veri indirme başlatılıyor ({interval}): {', '.join(symbols)}")
        self.engine_label.configure(text="Motor: Veri indiriyor", foreground="#fbbf24")
        try:
            self.process = subprocess.Popen(
                command, cwd=str(LAB_ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env=process_env,
            )
        except Exception as exc:
            self.engine_label.configure(text="Motor: Başlatma hatası", foreground="#f87171")
            messagebox.showerror("Veri indirme başlatılamadı", str(exc), parent=self)
            return
        threading.Thread(target=self._read_process, args=(self.process, "VERİ-İNDİRME"), daemon=True).start()

    def _selected(self, variables: dict[str, tk.BooleanVar]) -> list[str]:
        return [name for name, variable in variables.items() if variable.get()]

    def _grid(self, fields: set[str] | None = None) -> dict[str, list[Any]]:
        result: dict[str, list[Any]] = {}
        for key, variables in self.grid_vars.items():
            if fields is not None and key not in fields:
                continue
            use_range = self.grid_modes[key].get() == "Aralık"
            values = [v.strip() for v in variables["single"].get().split(";")] if key in ROUTING_CHOICES or key in TEXT_FIELDS else parameter_values(
                "" if use_range else variables["single"].get(), variables["start"].get() if use_range else "",
                variables["end"].get() if use_range else "", variables["step"].get() if use_range else "",
            )
            if use_range and not values:
                raise ValueError(f"{key}: aralığın başlangıç, bitiş ve adımını doldurun")
            if not values:
                continue
            if key in INTEGER_PARAMETERS or key in GATE_INTEGER_PARAMETERS:
                if any(isinstance(value, bool) or not float(value).is_integer() for value in values):
                    raise ValueError(f"{key} yalnız tam sayı değerleri kabul eder")
                values = [int(value) for value in values]
            if key in BOOLEAN_PARAMETERS | GATE_BOOLEAN_PARAMETERS and any(not isinstance(value, bool) for value in values):
                raise ValueError(f"{key} yalnız true/false kabul eder")
            # Avoid duplicate runs from repeated list values.
            values = list(dict.fromkeys(values))
            for value in values:
                validate_component(key,value)
                if key in TEXT_FIELDS:
                    if not re.fullmatch(r"[A-Z0-9_]+:[A-Z0-9_.]+",value):
                        raise ValueError(f"{key}: EXCHANGE:SYMBOL gerekli")
                elif key in FLOATS:
                    if isinstance(value,bool) or not math.isfinite(value):
                        raise ValueError(f"{key}: sonlu sayı gerekli")
                elif key in ROUTING_CHOICES:
                    if value not in ROUTING_CHOICES[key]:
                        raise ValueError(f"{key}: geçersiz seçim {value!r}")
                elif key in {item for _, item in PARAMETERS}:
                    validate_parameters({key: value})
                elif key in GATE_INTEGER_PARAMETERS and int(value) < (0 if key in {"vwap_band_bps","pmax_tf_minutes"} else 1):
                    raise ValueError(f"{key} {'sıfır veya daha büyük' if key == 'vwap_band_bps' else 'en az 1'} olmalı")
                elif key == "most_percent" and not 0 <= float(value) < 100:
                    raise ValueError("MOST yüzde 0 ile 100 arasında olmalı")
                elif key == "tillson_factor" and not 0.1 <= float(value) <= 5:
                    raise ValueError("T3 faktörü kaynak aralığı 0.1 ile 5")
            result[key] = values
        return result

    def _update_count(self) -> None:
        if not hasattr(self, "count_label"):
            return
        try:
            count = planned_runs(self._selected(self.symbol_vars), self._selected(self.exit_vars), self._selected(self.er_vars), self._selected(self.winrate_vars), self._grid())
            self.count_label.configure(text=f"Planlanan koşu: {count}", foreground="#fbbf24" if count > int(self.max_runs_var.get()) else "#34d399")
        except ValueError:
            self.count_label.configure(text="Planlanan koşu: parametre hatası", foreground="#f87171")
        if hasattr(self, "selection_label"):
            selected = self._selected(self.symbol_vars)
            self.selection_label.configure(text=f"Seçili {len(selected)} parite (aramada gizlenenler dahil): " + ", ".join(selected))

    def _build_job(self, audit_mode: bool = False) -> dict[str, Any]:
        identity = engine_identity(LAB_ROOT)
        validate_engine({"pine_candidate": self.pine_candidate}, identity)
        symbols = self._selected(self.symbol_vars)
        exits = self._selected(self.exit_vars)
        ers = self._selected(self.er_vars)
        states = self._selected(self.winrate_vars)
        if not symbols or not exits or not ers or not states:
            raise ValueError("Coin, çıkış, ER ve Shadow/Winrate alanlarının her birinden en az bir seçim yapın")
        start = date.fromisoformat(self.start_var.get().strip())
        end = date.fromisoformat(self.end_var.get().strip())
        if start >= end:
            raise ValueError("Bitiş tarihi başlangıç tarihinden sonra olmalıdır")
        grid = self._grid({item for _, item in PARAMETERS})
        gate_grid = self._grid(set(GATE_DEFAULTS))
        # HL's source gate explicitly requires Chart < MTF < HTF.  The local
        # executable decision chart is currently 30m, independently from a
        # separately downloaded archive timeframe.
        hl_is_consumed = any(
            "HL" in values for key, values in gate_grid.items()
            if key in {"g6_source", "ext1_source", "ext2_source"}
        )
        if hl_is_consumed:
            mtf = gate_grid.get("hl_mtf_minutes", [60])
            htf = gate_grid.get("hl_htf_minutes", [240])
            chart_minutes = int(self.decision_interval_var.get().removesuffix("m"))
            invalid = [(m, h) for m in mtf for h in htf if not (chart_minutes < int(m) < int(h))]
            if invalid and not audit_mode:
                raise ValueError(
                    f"HL zaman hiyerarşisi geçersiz. {self.decision_interval_var.get()} karar grafiği için "
                    f"MTF > {chart_minutes}dk ve HTF > MTF olmalı "
                    f"(örnek: {'MTF=15, HTF=60' if chart_minutes == 5 else 'MTF=60, HTF=240'})."
                )
        # A normal user job must be rejected before an accidental Cartesian
        # explosion.  The system-audit planner deliberately receives the raw
        # axes and turns each one into a separate, bounded (max. 3 samples)
        # one-factor job below, so its base job must not be rejected here.
        count = 1 if audit_mode else planned_runs(symbols, exits, ers, states, {**grid, **gate_grid})
        cap = int(self.max_runs_var.get())
        if cap < 1:
            raise ValueError("Güvenlik sınırı en az 1 olmalı")
        if not audit_mode and count > cap:
            raise ValueError(f"Planlanan {count} koşu güvenlik üst sınırı {cap} değerini aşıyor")
        snapshot = {**(self.universe_snapshot or selection_snapshot(symbols, "Elle test seçimi", [])),
                    "symbols": list(symbols), "finalized_at": datetime.now().astimezone().isoformat(),
                    "edited_after_list_selection": bool(self.universe_snapshot and set(self.universe_snapshot["symbols"]) != set(symbols))}
        _,feed_snapshot=load_feed_manifest(self.gate_feed_manifest_var.get().strip())
        return {
            **{k: v for k, v in getattr(self, 'seed_context', {}).items() if k in {'execution_data_policy'}},
            "parent_result": getattr(self, 'seed_lineage', None),
            "id": safe_job_id(self.job_id_var.get()),
            "description": "Local Quant Lab masaüstü arayüzünden oluşturuldu",
            "status": "READY",
            "symbols": symbols,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "categorical": {"exit_family": exits, "er": ers, "winrate_state": states},
            "parameter_grid": grid,
            "gate_parameter_grid": gate_grid,
            "gate_feed_manifest": self.gate_feed_manifest_var.get().strip(),
            "gate_feed_snapshot":feed_snapshot,
            "save_gate_trace":self.save_gate_trace_var.get(),
            "max_runs": cap,
            "planned_runs": count,
            "created_at_local": datetime.now().astimezone().isoformat(),
            "engine_status": "RESEARCH_APPROXIMATION",
            "production_eligible": False,
            "engine_snapshot": identity,
            "config_snapshot": {**json.loads(json.dumps(self.config_data)), "interval": self.decision_interval_var.get()},
            "tradingview_reference": json.loads(json.dumps(self.tradingview_reference)) if self.tradingview_reference else None,
            "universe_snapshot": snapshot,
            "historical_universe": snapshot.get("historical_filter") or getattr(self, 'seed_context', {}).get('historical_universe'),
            "auto_download_missing": bool(self.auto_download_var.get()),
        }

    def add_job(self, run: bool = False) -> None:
        try:
            job = self._build_job()
            append_ready_job(QUEUE_PATH, job)
        except Exception as exc:
            messagebox.showerror("İş eklenemedi", str(exc), parent=self)
            return
        self._log(f"Kuyruğa eklendi: {job['id']} ({job['planned_runs']} koşu)")
        self.refresh_jobs(select_id=job["id"])
        self.notebook.select(self.queue_tab)
        self.job_id_var.set(datetime.now().strftime("TEST-%Y%m%d-%H%M%S"))
        if run:
            self.run_job(job["id"])

    def refresh_jobs(self, select_id: str | None = None) -> None:
        selected_ids = {select_id} if select_id else {str(self.job_tree.item(i, "values")[0]) for i in self.job_tree.selection()}
        category_data = load_categories(CATEGORIES_PATH)
        self.category_entry.configure(values=category_data['categories'])
        self.category_filter.configure(values=['Tümü', 'Kategorisiz', *category_data['categories']])
        for item in self.job_tree.get_children():
            self.job_tree.delete(item)
        queue = read_json(QUEUE_PATH, {"jobs": []})
        target_items = []
        for job in reversed(queue.get("jobs", [])):
            category = category_data['assignments'].get(job.get('id'), 'Kategorisiz')
            if self.category_filter_var.get() not in ('Tümü', category):
                continue
            status_label = {"READY": "Bekliyor", "RUNNING": "Çalışıyor", "COMPLETE": "Tamamlandı", "FAILED": "Başarısız", "INTERRUPTED": "Kesildi"}.get(job.get("status"), job.get("status", ""))
            if job.get("status") == "COMPLETE" and job.get("skipped_runs"):
                status_label = "Uyarılı tamamlandı" if job.get("completed_runs") else "Test edilebilir veri yok"
            item = self.job_tree.insert("", "end", values=(
                job.get("id", ""), category, status_label,
                f"{job.get('completed_runs', 0)}/{job.get('planned_runs', '?')} · Atlanan: {job.get('skipped_runs', 0)}",
                len(job.get("symbols", [])), f"{job.get('start', '')} → {job.get('end', '')}",
                job.get("error") or job.get("excel_report_error") or job.get("report", ""),
            ))
            if job.get("id") in selected_ids:
                target_items.append(item)
        if target_items:
            self.job_tree.selection_set(target_items)
            if select_id:
                self.job_tree.see(target_items[0])
        status = read_json(STATUS_PATH, {})
        state = status.get("state", "Gözetmen henüz çalışmadı")
        detail = f"Yerel gözetmen: {state}"
        if status.get("job_id"):
            detail += f" · {status['job_id']}"
        if status.get("error"):
            detail += f" · Hata: {status['error']}"
        self.status_detail.configure(text=detail)

    def _selected_job_id(self) -> str | None:
        selection = self.job_tree.selection() if hasattr(self, "job_tree") else ()
        if not selection:
            return None
        return str(self.job_tree.item(selection[0], "values")[0])

    def edit_category(self, assign=False, clear=False):
        ids = [str(self.job_tree.item(i, 'values')[0]) for i in self.job_tree.selection()] if assign else []
        if assign and not ids:
            messagebox.showinfo('Test seçin', 'Önce alttaki listeden bir veya daha fazla test seçin.', parent=self)
            return
        try:
            save_category(CATEGORIES_PATH, self.category_name_var.get(), ids, clear)
        except Exception as exc:
            messagebox.showerror('Kategori kaydedilemedi', str(exc), parent=self)
            return
        self.refresh_jobs()

    def recover_jobs(self):
        try:
            count = recover_interrupted(QUEUE_PATH, LAB_ROOT / "worker.lock")
            self._log(f"Kesilen {count} iş yeniden denenebilir duruma getirildi. Önceki çıktılar korundu.")
            self.refresh_jobs()
        except RuntimeError as exc:
            messagebox.showinfo("Motor çalışıyor", str(exc), parent=self)

    def run_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            messagebox.showinfo("İş seçin", "Önce READY durumundaki bir işi seçin.", parent=self)
            return
        self.run_job(job_id)

    def run_job(self, job_id: str) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Motor meşgul", "Bir test işi zaten çalışıyor.", parent=self)
            return
        queue = read_json(QUEUE_PATH, {"jobs": []})
        match = next((job for job in queue.get("jobs", []) if job.get("id") == job_id), None)
        if not match or match.get("status") not in {"READY", "FAILED", "INTERRUPTED"}:
            messagebox.showwarning("Başlatılamadı", "Yalnız bekleyen, başarısız veya kesilmiş işler başlatılabilir.", parent=self)
            return
        python = str(bundled_python() or Path(sys.executable))
        command = [python, "-u", str(LAB_ROOT / "quant_lab.py"), "job", "--job-id", job_id]
        self.notebook.select(self.log_tab)
        self._log(f"Başlatılıyor: {job_id}")
        self.engine_label.configure(text=f"Motor: Çalışıyor · {job_id}", foreground="#fbbf24")
        process_env = os.environ.copy()
        process_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        try:
            self.process = subprocess.Popen(
                command, cwd=str(LAB_ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env=process_env,
            )
        except Exception as exc:
            self.engine_label.configure(text="Motor: Başlatma hatası", foreground="#f87171")
            messagebox.showerror("Motor başlatılamadı", str(exc), parent=self)
            return
        threading.Thread(target=self._read_process, args=(self.process, job_id), daemon=True).start()

    def _read_process(self, process: subprocess.Popen[str], job_id: str) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if not self._closing:
                self.after(0, self._log, line.rstrip())
        return_code = process.wait()
        if not self._closing:
            self.after(0, self._process_finished, job_id, return_code)

    def _process_finished(self, job_id: str, return_code: int) -> None:
        if return_code == 0:
            self._log(f"Tamamlandı: {job_id}")
            self.engine_label.configure(text="Motor: Hazır", foreground="#34d399")
        else:
            self._log(f"HATA: {job_id}, çıkış kodu {return_code}")
            self.engine_label.configure(text="Motor: Hata", foreground="#f87171")
        self.refresh_jobs(select_id=job_id)

    def start_supervisor(self) -> None:
        if self.supervisor_process and self.supervisor_process.poll() is None:
            messagebox.showinfo("Gözetmen", "Gözetmen zaten çalışıyor.", parent=self)
            return
        python = str(bundled_python() or Path(sys.executable))
        command = [python, "-u", str(LAB_ROOT / "supervisor.py"), "--poll-seconds", "60"]
        process_env = os.environ.copy()
        process_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        self.supervisor_process = subprocess.Popen(
            command, cwd=str(LAB_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=process_env,
        )
        threading.Thread(target=self._read_supervisor, daemon=True).start()
        self._log("Yerel gözetmen başlatıldı; READY işleri her 60 saniyede kontrol edecek.")

    def _read_supervisor(self) -> None:
        process = self.supervisor_process
        if not process or process.stdout is None:
            return
        for line in process.stdout:
            if not self._closing:
                self.after(0, self._log, "[Gözetmen] " + line.rstrip())
        code = process.wait()
        if not self._closing:
            self.after(0, self._log, f"Gözetmen kapandı (kod {code}).")

    def stop_supervisor(self) -> None:
        if self.supervisor_process and self.supervisor_process.poll() is None:
            atomic_write_json(LAB_ROOT / "supervisor_stop.json", {"stop": True})
            self._log("Gözetmen mevcut işi bitirince duracak; yeni iş almayacak.")
        else:
            self._log("Çalışan arayüz gözetmeni yok.")

    def open_selected_report(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            messagebox.showinfo("İş seçin", "Önce bir iş seçin.", parent=self)
            return
        job = next((row for row in read_json(QUEUE_PATH, {"jobs": []})["jobs"] if row["id"] == job_id), {})
        folder = Path(job.get("output_dir") or (Path(job["report"]).parent if job.get("report") else REPORTS_ROOT / job_id))
        if not folder.exists():
            messagebox.showinfo("Rapor yok", "Bu iş için henüz rapor klasörü oluşmamış.", parent=self)
            return
        if os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")

    def _periodic_refresh(self) -> None:
        if self._closing:
            return
        self.refresh_jobs()
        self.after(1500, self._periodic_refresh)

    def _on_close(self) -> None:
        running = self.process and self.process.poll() is None
        supervisor_running = self.supervisor_process and self.supervisor_process.poll() is None
        if running or supervisor_running:
            if supervisor_running:
                self.stop_supervisor()
            messagebox.showinfo("İşlem devam ediyor", "Çalışan işlemin bozulmaması için pencere açık kalacak. Küçültebilirsiniz. Gözetmen yeni iş almayacak; mevcut işlem bitince pencereyi kapatabilirsiniz.", parent=self)
            return
        self._closing = True
        self.stop_supervisor()
        self.destroy()


def main() -> None:
    QuantLabApp().mainloop()


if __name__ == "__main__":
    main()
