"""Queue BTC control, Bias and HL direct/G6 entry-only comparisons."""
from __future__ import annotations

import copy

from enqueue_dual_component_ablation import make_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


FEED_MANIFEST = str(LAB_ROOT / "gate_feeds" / "btc_spot_perp_1m_manifest.json")


def base_job(variant: str) -> dict:
    job = copy.deepcopy(make_job("BASE", pmax=False, core=False))
    job["id"] = f"AUTO-BTC-S9-{variant}"
    job["description"] = f"BTC entry-only gate attribution: {variant}"
    job["gate_feed_manifest"] = FEED_MANIFEST
    job["save_gate_trace"] = True
    job["gate_parameter_grid"].update({
        "strategy_master_enabled": [True],
        "external_gate_closes_position": [False],
        "ext1_enabled": [True],
        "ext2_enabled": [False],
        "ext_mode": ["AND"],
        "g6_enabled": [False],
        "vwap_enabled": [False],
    })
    job["experiment"].update({
        "id": "AUTO-BTC-S9-BIAS-HL",
        "stage": "S9_GATE_ATTRIBUTION",
        "variant": variant,
        "risk": "Local causal approximation using pinned BTC 1m feeds; Pine parity pending.",
    })
    return job


def main() -> None:
    control = base_job("OPEN-CONTROL")
    control["gate_parameter_grid"]["ext1_source"] = ["close"]

    jobs = [control]
    for source in ("BIAS", "HL"):
        for route in ("DIRECT", "VIA-G6"):
            job = base_job(f"{source}-{route}")
            via = route == "VIA-G6"
            job["gate_parameter_grid"].update({
                "ext1_source": ["MASTER_GATE" if via else source],
                "g6_enabled": [via],
                "g6_source": [source],
            })
            if source == "BIAS":
                job["gate_parameter_grid"].update({
                    "bias_market": ["Spot"], "bias_main": ["BINANCE"],
                    "bias_ex2": ["NONE"], "bias_ex3": ["NONE"], "bias_ex4": ["NONE"],
                    "bias_liquidity": [True], "bias_min_liquidity": [5_000_000.0],
                    "bias_valid_ex": [False], "bias_participation": [False],
                    "bias_today": [True], "bias_min_today": [1.6],
                    "bias_hold_high": [False], "bias_breakout": [False],
                    "bias_day": [True], "bias_min_day": [0.1],
                    "bias_week": [False], "bias_month": [False],
                    "bias_btc_zone": [False], "bias_pair": [False],
                })
            else:
                job["gate_parameter_grid"].update({
                    "hl_left": [5], "hl_right": [5],
                    "hl_mtf_minutes": [60], "hl_htf_minutes": [240],
                    "hl_activation": ["HTF HL"], "hl_confirmation": ["Moderate"],
                    "hl_close_source": ["HTF"], "hl_close_mode": ["Any Next Signal"],
                })
            jobs.append(job)

    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        ids = {job["id"] for job in jobs}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        stored.extend(jobs)

    update_queue(LAB_ROOT / "jobs.json", add)
    print("\n".join(job["id"] for job in jobs))


if __name__ == "__main__":
    main()
