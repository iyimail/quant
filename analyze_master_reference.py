"""Paired component analysis for master_reference_v1."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent / "reports/master_reference_v1"
SOURCE = ROOT / "results.csv"
SEED = 20260908


def bootstrap_ci(values, repetitions=5000):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(SEED)
    means = rng.choice(values, size=(repetitions, len(values)), replace=True).mean(axis=1)
    return np.quantile(means, [0.025, 0.975]).tolist()


def paired_effect(data, name, field, candidate, control, context):
    index = ["symbol", "window", *context]
    metrics = ["expectancy", "total_pnl_usdt", "profit_factor", "max_drawdown_pct_initial",
               "closed_trades", "win_rate_pct"]
    left = data[data[field] == candidate][index + metrics].copy()
    right = data[data[field] == control][index + metrics].copy()
    paired = left.merge(right, on=index, suffixes=("_candidate", "_control"), validate="one_to_one")
    for metric in metrics:
        paired["delta_" + metric] = paired[metric + "_candidate"] - paired[metric + "_control"]
    # Average alternate contexts first, leaving 13 symbols × 3 windows as the analysis blocks.
    block = paired.groupby(["symbol", "window"], as_index=False).agg(
        delta_expectancy=("delta_expectancy", "mean"),
        delta_total_pnl_usdt=("delta_total_pnl_usdt", "mean"),
        delta_profit_factor=("delta_profit_factor", "mean"),
        delta_max_drawdown_pct_initial=("delta_max_drawdown_pct_initial", "mean"),
        delta_closed_trades=("delta_closed_trades", "mean"),
        delta_win_rate_pct=("delta_win_rate_pct", "mean"),
        candidate_trades=("closed_trades_candidate", "sum"),
        control_trades=("closed_trades_control", "sum"),
    )
    lo, hi = bootstrap_ci(block.delta_expectancy)
    window_means = block.groupby("window").delta_expectancy.mean()
    symbol_gain = block.groupby("symbol").delta_expectancy.sum().clip(lower=0)
    concentration = float(symbol_gain.max() / symbol_gain.sum()) if symbol_gain.sum() > 0 else np.nan
    favorable = float((block.delta_expectancy > 0).mean())
    if hi < 0 and block.delta_max_drawdown_pct_initial.mean() >= 0:
        verdict = "REJECT"
    elif lo > 0 and favorable >= .70 and (window_means > 0).all() and concentration <= .25:
        verdict = "SCREEN_CANDIDATE"
    elif block.delta_expectancy.mean() > 0 or block.delta_max_drawdown_pct_initial.mean() < 0:
        verdict = "RESEARCH_MORE"
    else:
        verdict = "REJECT"
    summary = {
        "comparison": name, "candidate": candidate, "control": control,
        "independent_blocks": len(block), "raw_pairs": len(paired),
        "mean_delta_expectancy_usdt_per_trade": block.delta_expectancy.mean(),
        "median_delta_expectancy_usdt_per_trade": block.delta_expectancy.median(),
        "bootstrap_95_low": lo, "bootstrap_95_high": hi,
        "favorable_block_share": favorable,
        "positive_windows": int((window_means > 0).sum()),
        "mean_delta_total_pnl_usdt": block.delta_total_pnl_usdt.mean(),
        "mean_delta_profit_factor": block.delta_profit_factor.mean(),
        "mean_delta_drawdown_pct_initial": block.delta_max_drawdown_pct_initial.mean(),
        "mean_delta_closed_trades": block.delta_closed_trades.mean(),
        "mean_delta_win_rate_pct": block.delta_win_rate_pct.mean(),
        "largest_positive_symbol_share": concentration,
        "candidate_trades_across_pairs": int(paired.closed_trades_candidate.sum()),
        "control_trades_across_pairs": int(paired.closed_trades_control.sum()),
        "verdict": verdict,
    }
    return summary, block.assign(comparison=name)


def main():
    data = pd.read_csv(SOURCE)
    if len(data) != 1404 or set(data.status) != {"COMPLETE"}:
        raise ValueError("Frozen screen is incomplete")
    data["expectancy"] = data.closed_net_profit_usdt / data.closed_trades.replace(0, np.nan)
    specifications = [
        ("ER v1 - OFF", "er", "v1", "OFF", ["exit_family", "winrate_enabled", "close_on_block"]),
        ("ER v2 - OFF", "er", "v2", "OFF", ["exit_family", "winrate_enabled", "close_on_block"]),
        ("WR HOLD - OFF", "wr_state", "HOLD", "OFF", ["exit_family", "er"]),
        ("WR CLOSE - OFF", "wr_state", "CLOSE", "OFF", ["exit_family", "er"]),
        ("WR CLOSE - HOLD", "wr_state", "CLOSE", "HOLD", ["exit_family", "er"]),
        ("MOST - NONE", "exit_family", "MOST", "NONE", ["er", "winrate_enabled", "close_on_block"]),
        ("RSI_SMA - NONE", "exit_family", "RSI_SMA", "NONE", ["er", "winrate_enabled", "close_on_block"]),
        ("TILLSON - NONE", "exit_family", "TILLSON", "NONE", ["er", "winrate_enabled", "close_on_block"]),
    ]
    data["wr_state"] = np.where(~data.winrate_enabled, "OFF", np.where(data.close_on_block, "CLOSE", "HOLD"))
    summaries, blocks = [], []
    for spec in specifications:
        summary, block = paired_effect(data, *spec)
        summaries.append(summary)
        blocks.append(block)
    effects = pd.DataFrame(summaries).sort_values("mean_delta_expectancy_usdt_per_trade", ascending=False)
    effects.to_csv(ROOT / "paired_component_effects.csv", index=False, encoding="utf-8-sig")
    pd.concat(blocks).to_csv(ROOT / "paired_effect_blocks.csv", index=False, encoding="utf-8-sig")

    group = data.groupby(["exit_family", "er", "wr_state"], as_index=False).agg(
        blocks=("symbol", "size"), total_closed_pnl=("closed_net_profit_usdt", "sum"),
        total_trades=("closed_trades", "sum"), median_block_expectancy=("expectancy", "median"),
        mean_block_expectancy=("expectancy", "mean"), median_drawdown_pct=("max_drawdown_pct_initial", "median"),
        mean_profit_factor=("profit_factor", "mean"))
    group["pooled_expectancy"] = group.total_closed_pnl / group.total_trades
    windows = data.groupby(["exit_family", "er", "wr_state", "window"]).expectancy.mean().unstack(fill_value=np.nan)
    windows.columns = [f"window_{column}_mean_expectancy" for column in windows.columns]
    ranking = group.merge(windows.reset_index(), on=["exit_family", "er", "wr_state"])
    ranking["positive_windows"] = (ranking.filter(like="window_") > 0).sum(axis=1)
    ranking.sort_values(["positive_windows", "median_block_expectancy", "pooled_expectancy"], ascending=False).to_csv(
        ROOT / "configuration_summary.csv", index=False, encoding="utf-8-sig")

    lines = ["# Master Reference v1 — Paired Screen", "", "All 1,404 planned cells completed; no data skips.", "",
             "These are research approximations on previously seen history. They do not establish Pine parity or production readiness.", "",
             "## Component effects", "",
             "| Comparison | Δ expectancy/trade | 95% bootstrap interval | Favorable blocks | Positive windows | Δ drawdown | Verdict |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for row in effects.itertuples():
        lines.append(f"| {row.comparison} | {row.mean_delta_expectancy_usdt_per_trade:.4f} | [{row.bootstrap_95_low:.4f}, {row.bootstrap_95_high:.4f}] | {row.favorable_block_share:.1%} | {row.positive_windows}/3 | {row.mean_delta_drawdown_pct_initial:.2f} pp | {row.verdict} |")
    lines += ["", "## Interpretation constraints", "",
              "- Alternate contexts are averaged before inference, leaving 39 symbol-window blocks.",
              "- Summary results cannot run top-five-trade removal or trade-sequence Monte Carlo; those require trade-level logs.",
              "- Zero-distance trailing and per-symbol Pine parity remain unresolved.",
              "- Configuration rankings are descriptive; component paired effects are the primary output.", ""]
    (ROOT / "analysis.md").write_text("\n".join(lines), encoding="utf-8")
    print(effects[["comparison", "mean_delta_expectancy_usdt_per_trade", "bootstrap_95_low", "bootstrap_95_high", "favorable_block_share", "positive_windows", "mean_delta_drawdown_pct_initial", "verdict"]].to_string(index=False))


if __name__ == "__main__":
    main()
