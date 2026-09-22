"""Single-change follow-up: isolated source G2 with macro reset switched on."""

import numpy as np
import pandas as pd

import quant_lab as lab
from gate_models import confirmed_macro_reset, confirmed_tillson_gate2
from lab_safety import atomic_write_json, engine_identity, file_lock
from run_tillson_gate_screen import OUT as PRIOR, SYMBOLS, WINDOWS
from run_wct_categorical_combo_screen import Combo, simulate

OUT = lab.LAB_ROOT / "reports/tillson_macro_wiring_v1"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    prior = pd.read_csv(PRIOR / "results.csv")
    expected = prior[prior.variant == "Price vs T3_PINE_EXIT"]
    if len(expected) != 39 or expected[["symbol", "window"]].duplicated().any():
        raise ValueError("Frozen isolated G2 baseline is incomplete or duplicated")
    config = lab.load_config(lab.LAB_ROOT / "config.json")
    rules = lab.load_execution_rules()
    paths = lab.discover_market_roots(lab.PROJECT_ROOT / "research/data/raw", SYMBOLS, config["interval"])
    combo = Combo("NONE", "v2", True, "SHADOW", "WEIGHTED", False)
    atomic_write_json(OUT / "manifest.json", {
        "symbols": SYMBOLS, "windows": WINDOWS, "planned_runs": 39,
        "single_change": "activate source 4h EMA200 macro reset; G2 Price vs T3 Confirmed HTF retained",
        "other_master_components": "disabled", "strategy_external_delay": "one chart bar",
        "external_gate_exit_authority": True, "status": "RESEARCH_APPROXIMATION",
        "engine": engine_identity(lab.LAB_ROOT)})
    rows = []
    with file_lock(lab.LAB_ROOT / "worker.lock", timeout=0):
        for symbol in SYMBOLS:
            full = lab.load_binance_archives(paths[symbol])
            g2 = confirmed_tillson_gate2(full)
            macro = confirmed_macro_reset(full)
            master_open = g2.gate_pass & ~macro.macro_resets
            strategy_open = master_open.shift(1).fillna(False).astype(bool)
            cfg, metadata = lab.symbol_strategy_config(config, symbol, None, rules)
            for window, (start, end) in enumerate(WINDOWS, 1):
                bars, check = lab.checked_bars(full, pd.Timestamp(start, tz="UTC"),
                                               pd.Timestamp(end, tz="UTC"), config["interval"])
                if check["status"] != "PASS":
                    raise ValueError(f"{symbol} W{window}: {check}")
                gate = strategy_open.reindex(bars.index).fillna(False)
                _, result = simulate(bars, None, combo, base_config=cfg,
                                     external_gate_open=gate, external_gate_closes_position=True)
                rows.append({**result, "symbol": symbol, "window": window,
                             "coverage": check["coverage"], "tick_size": metadata["tick_size"],
                             "gate_open_share": float(gate.mean()),
                             "macro_reset_share": float(macro.macro_resets.reindex(bars.index).mean())})
            pd.DataFrame(rows).to_csv(OUT / "results_checkpoint.csv", index=False)
            atomic_write_json(OUT / "checkpoint.json", {"finished_runs": len(rows),
                                                        "planned_runs": 39, "last_symbol": symbol,
                                                        "complete": False})
            print(f"G2 macro checkpoint: {len(rows)}/39", flush=True)

    result = pd.DataFrame(rows)
    result.to_csv(OUT / "results.csv", index=False, encoding="utf-8-sig")
    result["expectancy"] = result.closed_net_profit_usdt / result.closed_trades.replace(0, np.nan)
    prior = prior[prior.variant.isin(["CONTROL", "Price vs T3_PINE_EXIT"])].copy()
    prior["expectancy"] = prior.closed_net_profit_usdt / prior.closed_trades.replace(0, np.nan)
    isolated = prior[prior.variant == "Price vs T3_PINE_EXIT"]
    control = prior[prior.variant == "CONTROL"]
    comparison = result.merge(isolated, on=["symbol", "window"], suffixes=("", "_isolated"))
    comparison = comparison.merge(control[["symbol", "window", "expectancy", "total_pnl_usdt",
                                           "closed_trades"]], on=["symbol", "window"],
                                  suffixes=("", "_control"))
    for metric in ("expectancy", "total_pnl_usdt", "closed_trades"):
        comparison["delta_vs_isolated_" + metric] = comparison[metric] - comparison[metric + "_isolated"]
        comparison["delta_vs_control_" + metric] = comparison[metric] - comparison[metric + "_control"]
    comparison.to_csv(OUT / "paired_blocks.csv", index=False, encoding="utf-8-sig")
    rng = np.random.default_rng(20260916)
    lines = ["# G2 default macro-reset wiring, fixed follow-up", "",
             "One source-default connection was added to the prior isolated Price-vs-T3/Pine-exit arm: confirmed 4h EMA200 macro reset. CORE and other gates remain disabled; this is still not the complete source-default Master.", ""]
    for label in ("isolated", "control"):
        column = "delta_vs_" + label + "_expectancy"
        by_symbol = comparison.groupby("symbol")[column].mean()
        boot = rng.choice(by_symbol.to_numpy(), size=(10000, len(by_symbol)), replace=True).mean(axis=1)
        lines.append(f"- Versus {label}: mean expectancy delta {comparison[column].mean():+.4f} USDT/trade; 95% symbol-cluster CI [{np.quantile(boot, .025):+.4f}, {np.quantile(boot, .975):+.4f}]; mean total-PnL delta {comparison['delta_vs_' + label + '_total_pnl_usdt'].mean():+.2f} USDT/block; mean trade-count delta {comparison['delta_vs_' + label + '_closed_trades'].mean():+.2f}.")
    lines += ["", f"Average macro-reset share: {comparison.macro_reset_share.mean():.3f}; average Strategy gate-open share: {comparison.gate_open_share.mean():.3f}.",
              "Prior windows were already inspected; this is not untouched OOS. Python macro/EMA and fills are not TradingView-validated. No Pine, paper or live promotion."]
    (OUT / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    atomic_write_json(OUT / "checkpoint.json", {"finished_runs": 39,
                                                "planned_runs": 39, "complete": True})
    print("\n".join(lines))


if __name__ == "__main__":
    main()
