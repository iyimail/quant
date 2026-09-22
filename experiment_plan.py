"""Controlled baseline/ablation experiment planning and evidence comparison.

This module creates research jobs only.  It does not change Pine, place orders,
or turn one-market evidence into a promotion decision.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


# A field is a causal intervention only when it is explicitly active in the
# baseline.  Producer sources behind G6/EXT are intentionally not guessed.
ABLATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("core_enabled", "CORE"),
    ("g3_enabled", "G3_BOLLINGER"),
    ("g4_enabled", "G4_MOMENTUM"),
    ("g5_enabled", "G5_MOST2"),
    ("g6_enabled", "G6_EXTERNAL"),
    ("g7_enabled", "G7_PRICE_LATCH"),
    ("pmax_enabled", "G8_PMAX"),
    ("ext1_enabled", "EXT1_SOURCE"),
    ("ext2_enabled", "EXT2_SOURCE"),
    # These are local-model switches, not a claim that every Pine input has
    # parity.  Keeping them here prevents a hidden enabled producer from
    # escaping a component ablation.
    ("vwap_enabled", "VWAP_PRODUCER"),
    ("most_enabled", "G1_MOST"),
    ("tillson_enabled", "G2_TILLSON"),
)


@dataclass(frozen=True)
class ExperimentPlan:
    experiment_id: str
    jobs: list[dict[str, Any]]
    interventions: list[dict[str, str]]


@dataclass(frozen=True)
class ProfitLearningLadder:
    """Pre-registered, small-batch research ladder.

    The ladder deliberately does *not* optimize all gate settings at once.
    It gives the queue a causal sequence: establish a frozen baseline, remove
    one active component, compare a declared routing alternative, then vary
    one numeric setting at a time.  The results are still local-model evidence
    until they survive Pine golden-export parity and external validation.
    """

    experiment_id: str
    jobs: list[dict[str, Any]]
    stages: list[dict[str, Any]]


def _single_values(job: dict[str, Any]) -> dict[str, Any]:
    """Flatten a controlled baseline; sweeps/categorical alternatives are invalid."""
    result: dict[str, Any] = {}
    for grid_name in ("parameter_grid", "gate_parameter_grid"):
        for key, values in job.get(grid_name, {}).items():
            values = values if isinstance(values, list) else [values]
            if len(values) != 1:
                raise ValueError(f"{key}: baseline için tek değer seçin; aralık/listeyi sonraki hassasiyet aşamasına bırakın")
            result[key] = values[0]
    categories = job.get("categorical", {})
    for key, values in categories.items():
        values = values if isinstance(values, list) else [values]
        if len(values) != 1:
            raise ValueError(f"{key}: baseline için tek seçenek seçin")
    if len(job.get("symbols", [])) != 1:
        raise ValueError("Baseline/ablation ilk aşaması yalnız bir coin için oluşturulur")
    return result


def active_interventions(values: dict[str, Any]) -> list[dict[str, str]]:
    return [{"field": field, "label": label} for field, label in ABLATION_FIELDS if values.get(field) is True]


def build_baseline_ablation_plan(base_job: dict[str, Any], *, experiment_id: str) -> ExperimentPlan:
    values = _single_values(base_job)
    interventions = active_interventions(values)
    if not interventions:
        raise ValueError("Ablation için aktif en az bir Master/EXT bileşeni seçin")

    base = copy.deepcopy(base_job)
    base["id"] = f"{experiment_id}-BASELINE"
    base["description"] = "Kontrollü baseline; ablation karşılaştırmasının referansı"
    base["experiment"] = {
        "id": experiment_id, "stage": "BASELINE", "baseline_job_id": base["id"],
        "intervention": None, "purpose": "ONE_COIN_COMPONENT_ABLATION",
    }
    base["planned_runs"] = 1
    jobs = [base]
    for item in interventions:
        ablated = copy.deepcopy(base)
        ablated["id"] = f"{experiment_id}-ABLATE-{item['field'].upper()}"
        ablated["description"] = f"Ablation: yalnız {item['label']} kapalı; diğer baseline ayarları sabit"
        grid = ablated.setdefault("gate_parameter_grid", {})
        grid[item["field"]] = [False]
        ablated["experiment"] = {
            "id": experiment_id, "stage": "ABLATION", "baseline_job_id": base["id"],
            "intervention": item, "purpose": "ONE_COIN_COMPONENT_ABLATION",
        }
        jobs.append(ablated)
    return ExperimentPlan(experiment_id=experiment_id, jobs=jobs, interventions=interventions)


def _single_base_for_ladder(base_job: dict[str, Any]) -> dict[str, Any]:
    """Freeze the user-selected baseline and enforce the stated study scope."""
    _single_values(base_job)
    base = copy.deepcopy(base_job)
    categories = base.setdefault("categorical", {})
    # Winrate is intentionally held OFF: it is a separate, path-dependent
    # hypothesis and cannot be allowed to explain a gate's apparent value.
    categories["winrate_state"] = ["OFF"]
    grid = base.setdefault("parameter_grid", {})
    # ER version remains the user's chosen single version.  The requested
    # persistence intervention is exactly one confirmed decision bar.
    grid["er_persist_bars"] = [1]
    return base


def _job(base: dict[str, Any], job_id: str, description: str, stage: str,
         hypothesis: str, *, changes: dict[str, Any] | None = None,
         risk: str = "") -> dict[str, Any]:
    candidate = copy.deepcopy(base)
    candidate["id"] = job_id
    candidate["description"] = description
    candidate["status"] = "READY"
    candidate["planned_runs"] = 1
    protected = {"winrate_state", "er_persist_bars"}
    attempted = protected.intersection((changes or {}))
    if attempted:
        raise ValueError("Winrate OFF ve ER persistence=1 bu araştırma merdiveninde sabittir: " + ", ".join(sorted(attempted)))
    for key, value in (changes or {}).items():
        destination = "gate_parameter_grid" if key in {name for name, _ in ABLATION_FIELDS} or key.startswith(("g", "vwap_", "hl_", "bias_", "macro_", "pmax_", "most", "tillson", "ext", "routing_")) else "parameter_grid"
        candidate.setdefault(destination, {})[key] = [value]
    candidate["experiment"] = {
        "id": job_id.rsplit("-", 1)[0], "stage": stage,
        "purpose": "CAUSAL_PROFIT_LEARNING_LADDER",
        "hypothesis": hypothesis,
        "risk": risk,
        "winrate_state": "OFF",
        "er_persist_bars": 1,
    }
    return candidate


def build_profit_learning_ladder(
    base_job: dict[str, Any], *, experiment_id: str,
    topology_candidates: list[dict[str, Any]] | None = None,
    sensitivity_candidates: dict[str, list[Any]] | None = None,
) -> ProfitLearningLadder:
    """Create a controlled research queue for gate learning, not optimisation.

    ``topology_candidates`` is an explicit list of routing overrides such as
    ``{"ext1_source": "VWAP", "g6_enabled": False}``.  Sources are never
    guessed.  ``sensitivity_candidates`` maps *one* declared parameter to at
    most three values; every value becomes a separate one-factor job.
    """
    if not experiment_id:
        raise ValueError("experiment_id zorunludur")
    base = _single_base_for_ladder(base_job)
    base["id"] = f"{experiment_id}-S0-BASELINE"
    base["description"] = "S0 frozen baseline: Winrate OFF, ER persist=1"
    base["status"] = "READY"
    base["planned_runs"] = 1
    base["experiment"] = {
        "id": experiment_id, "stage": "S0_BASELINE",
        "purpose": "CAUSAL_PROFIT_LEARNING_LADDER",
        "hypothesis": "Önce mekanik olarak tanımlı tek baseline oluşturulur.",
        "risk": "RESEARCH_APPROXIMATION; Pine parity ve OOS yok.",
        "winrate_state": "OFF", "er_persist_bars": 1,
    }
    jobs = [base]
    stages: list[dict[str, Any]] = [{
        "stage": "S0_BASELINE", "job_ids": [base["id"]],
        "question": "Winrate kapalı ve ER persistence=1 iken referans davranış nedir?",
        "rejection": "<30 closed trade, negatif expectancy veya veri/parity hatası: yalnız tanımlayıcı sonuç.",
    }]

    active = active_interventions(_single_values(base))
    ablation_ids: list[str] = []
    for item in active:
        job_id = f"{experiment_id}-S1-ABLATE-{item['field'].upper()}"
        jobs.append(_job(
            base, job_id, f"S1 ablation: yalnız {item['label']} kapalı", "S1_ABLATION",
            f"{item['label']} kaldırılınca risk-ayarlı sonuç kötüleşirse bileşen değer katıyor olabilir.",
            changes={item["field"]: False},
            risk="Bir bileşen kaldırmak topology/state değiştirir; tek coin sonucu genellenmez.",
        ))
        ablation_ids.append(job_id)
    stages.append({
        "stage": "S1_ABLATION", "job_ids": ablation_ids,
        "question": "Aktif her bileşenin marjinal katkısı var mı?",
        "rejection": "Baseline'a göre expectancy iyileşiyor ve DD kötüleşmiyorsa bileşen zorunlu değildir; çoklu coin/OOS olmadan silinmez.",
    })

    topology_ids: list[str] = []
    for index, candidate in enumerate(topology_candidates or [], start=1):
        if not isinstance(candidate, dict) or not candidate:
            raise ValueError("Her topology alternatifi boş olmayan bir ayar sözlüğü olmalıdır")
        job_id = f"{experiment_id}-S2-TOPOLOGY-{index:02d}"
        jobs.append(_job(
            base, job_id, f"S2 topology {index}: açıkça tanımlı routing alternatifi", "S2_TOPOLOGY",
            "Aynı üretici bilgisi farklı G6/EXT yolunda gecikme ve latch etkisi yaratır.",
            changes=candidate,
            risk="G6→Master→EXT iki tüketici gecikmesi doğurabilir; TradingView bar-parity gerektirir.",
        ))
        topology_ids.append(job_id)
    stages.append({
        "stage": "S2_TOPOLOGY", "job_ids": topology_ids,
        "question": "Aynı gate doğrudan EXT mi, G6 üzerinden mi daha dayanıklı?",
        "rejection": "Doğrulanmamış kaynak, causal olmayan timing veya trade sayısı çöküşü: routing adayı reddedilir.",
    })

    sensitivity_ids: list[str] = []
    for key, values in (sensitivity_candidates or {}).items():
        if key in {"winrate_state", "er_persist_bars"}:
            raise ValueError(f"{key}: bu merdivende sabit tutulur")
        if not isinstance(values, list) or not 1 <= len(values) <= 3:
            raise ValueError(f"{key}: hassasiyet için en fazla üç, en az bir değer verin")
        for index, value in enumerate(values, start=1):
            job_id = f"{experiment_id}-S3-{key.upper()}-{index:02d}"
            jobs.append(_job(
                base, job_id, f"S3 sensitivity: {key}={value}", "S3_ONE_FACTOR_SENSITIVITY",
                f"{key} çevresinde geniş bir stabil plato var mı?",
                changes={key: value},
                risk="Bu değerler aynı dönemde seçilirse in-sample optimizasyon riski taşır.",
            ))
            sensitivity_ids.append(job_id)
    stages.append({
        "stage": "S3_ONE_FACTOR_SENSITIVITY", "job_ids": sensitivity_ids,
        "question": "Tek parametre değiştiğinde sonuç stabil mi, yoksa tek-nokta mı?",
        "rejection": "Yalnız tek değer çalışıyor, komşular bozuluyor veya sonuç büyük birkaç trade'e bağlıysa aday reddedilir.",
    })
    stages.append({
        "stage": "S4_VALIDATION_GATE", "job_ids": [],
        "question": "S0-S3 kazananı bağımsız pencere/coin, maliyet stresi, leave-one-symbol-out ve Pine golden export'ta kalıyor mu?",
        "rejection": "OOS, maliyet, tail/DD veya Pine parity başarısızsa production'a ilerlemez.",
    })
    return ProfitLearningLadder(experiment_id=experiment_id, jobs=jobs, stages=stages)


def _read_complete(job: dict[str, Any]) -> pd.DataFrame | None:
    output = job.get("output_dir")
    if not output:
        return None
    path = Path(output) / "job_results.csv"
    if not path.exists():
        return None
    data = pd.read_csv(path)
    data = data.loc[data.get("status", pd.Series(index=data.index, dtype=str)).eq("COMPLETE")].copy()
    return data if len(data) else None


def _aggregate(rows: pd.DataFrame) -> dict[str, float | int]:
    # A controlled plan has one row per job. Grouping still keeps this valid if
    # the runner later emits a deterministic split row.
    trades = int(rows["closed_trades"].sum())
    total = float(rows["total_pnl_usdt"].sum())
    dd = float(rows["max_drawdown_usdt"].max())
    return {
        "closed_trades": trades,
        "total_pnl_usdt": total,
        "max_drawdown_usdt": dd,
        "expectancy_usdt": total / trades if trades else 0.0,
        "profit_factor": float(rows["profit_factor"].replace([float("inf")], pd.NA).dropna().mean()) if rows["profit_factor"].notna().any() else float("nan"),
    }


def compare_plan(queue_path: Path, experiment_id: str, *, minimum_trades: int = 10) -> dict[str, Any]:
    queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {"jobs": []}
    jobs = [job for job in queue.get("jobs", []) if job.get("experiment", {}).get("id") == experiment_id]
    baseline = next((job for job in jobs if job.get("experiment", {}).get("stage") == "BASELINE"), None)
    if baseline is None:
        raise ValueError("Experiment baseline job bulunamadı")
    base_rows = _read_complete(baseline)
    if base_rows is None:
        return {"experiment_id": experiment_id, "status": "WAITING_BASELINE", "rows": []}
    base = _aggregate(base_rows)
    results = []
    for job in jobs:
        if job.get("experiment", {}).get("stage") != "ABLATION":
            continue
        rows = _read_complete(job)
        if rows is None:
            results.append({"job_id": job["id"], "label": job["experiment"]["intervention"]["label"], "status": "WAITING"})
            continue
        value = _aggregate(rows)
        delta_pnl = value["total_pnl_usdt"] - base["total_pnl_usdt"]
        delta_dd = value["max_drawdown_usdt"] - base["max_drawdown_usdt"]
        if base["closed_trades"] < minimum_trades or value["closed_trades"] < minimum_trades:
            verdict = "INSUFFICIENT_TRADES"
        elif delta_pnl < 0 and delta_dd > 0:
            verdict = "REMOVAL_WORSENS"
        elif delta_pnl > 0 and delta_dd <= 0:
            verdict = "REMOVAL_IMPROVES"
        else:
            verdict = "MIXED"
        results.append({
            "job_id": job["id"], "label": job["experiment"]["intervention"]["label"], "status": "COMPLETE",
            **value, "delta_total_pnl_usdt": delta_pnl, "delta_max_drawdown_usdt": delta_dd,
            "verdict": verdict,
        })
    return {
        "experiment_id": experiment_id,
        "status": "COMPLETE" if results and all(row["status"] == "COMPLETE" for row in results) else "RUNNING",
        "baseline_job_id": baseline["id"], "baseline": base, "minimum_trades": minimum_trades,
        "rows": results,
        "warning": "Tek coin ablation sonucu production/geneleme kararı değildir; OOS ve çoklu-coin doğrulaması gerekir.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
