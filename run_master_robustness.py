"""Trade-level robustness screen for the shortlisted Master reference profiles."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

import quant_lab as lab
from lab_safety import atomic_write_json, engine_identity, file_lock
from run_wct_categorical_combo_screen import Combo, simulate

SYMBOLS = 'WCTUSDT INITUSDT DUSKUSDT MYXUSDT CETUSUSDT TAOUSDT DEXEUSDT TAKEUSDT BANANAS31USDT SIRENUSDT BROCCOLI714USDT FHEUSDT BTRUSDT'.split()
WINDOWS = [('2025-09-01', '2026-03-01'), ('2026-03-01', '2026-06-01'), ('2026-06-01', '2026-09-01')]
PROFILES = {
    'P0_BASE': Combo('NONE', 'OFF', False, 'SHADOW', 'WEIGHTED', False),
    'P1_ER_V1': Combo('NONE', 'v1', False, 'SHADOW', 'WEIGHTED', False),
    'P2_ER_V2': Combo('NONE', 'v2', False, 'SHADOW', 'WEIGHTED', False),
    'P3_ER_V2_HOLD': Combo('NONE', 'v2', True, 'SHADOW', 'WEIGHTED', False),
    'P4_ER_V2_CLOSE': Combo('NONE', 'v2', True, 'SHADOW', 'WEIGHTED', True),
}
COSTS = [0.08, 0.12, 0.16]
OUT = lab.LAB_ROOT / 'reports/master_robustness_v1'


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    config = lab.load_config(lab.LAB_ROOT / 'config.json')
    rules = lab.load_execution_rules()
    manifest = {'symbols': SYMBOLS, 'windows': WINDOWS, 'profiles': list(PROFILES), 'costs': COSTS,
                'planned_runs': len(SYMBOLS) * len(WINDOWS) * len(PROFILES) * len(COSTS),
                'engine': engine_identity(lab.LAB_ROOT), 'status': 'RESEARCH_APPROXIMATION',
                'purpose': 'Trade-level concentration and cost stress for predeclared Master shortlist.'}
    atomic_write_json(OUT / 'manifest.json', manifest)
    summaries, ledgers = [], []
    data_root = lab.PROJECT_ROOT / 'research/data/raw'
    discovered = lab.discover_market_roots(data_root, SYMBOLS, config['interval'])
    total = manifest['planned_runs']
    with file_lock(lab.LAB_ROOT / 'worker.lock', timeout=0):
        for window, (start_text, end_text) in enumerate(WINDOWS, 1):
            start, end = pd.Timestamp(start_text, tz='UTC'), pd.Timestamp(end_text, tz='UTC')
            for symbol in SYMBOLS:
                bars, check = lab.checked_bars(lab.load_binance_archives(discovered[symbol]), start, end, config['interval'])
                if check['status'] != 'PASS':
                    raise ValueError(f'{symbol} W{window}: {check}')
                for cost in COSTS:
                    cfg, metadata = lab.symbol_strategy_config(config, symbol, {'commission_pct_per_order': cost}, rules)
                    for profile, combo in PROFILES.items():
                        ledger, summary = simulate(bars, None, combo, base_config=cfg)
                        summaries.append({**summary, 'profile': profile, 'symbol': symbol, 'window': window,
                                          'cost_pct_per_order': cost, 'tested_start': check['tested_start'],
                                          'tested_end_exclusive': check['tested_end_exclusive'],
                                          'tick_size': metadata['tick_size'], 'quantity_step': metadata['quantity_step']})
                        if cost == 0.08 and len(ledger):
                            ledger = ledger.assign(profile=profile, symbol=symbol, window=window,
                                                   cost_pct_per_order=cost)
                            ledgers.append(ledger)
                pd.DataFrame(summaries).to_csv(OUT / 'summary_checkpoint.csv', index=False)
                atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': len(summaries), 'planned_runs': total,
                                                             'last_block': f'W{window}_{symbol}', 'complete': False})
                print(f'Robustness checkpoint: {len(summaries)}/{total}', flush=True)
    summary = pd.DataFrame(summaries)
    trades = pd.concat(ledgers, ignore_index=True)
    summary.to_csv(OUT / 'summary.csv', index=False, encoding='utf-8-sig')
    trades.to_csv(OUT / 'baseline_cost_trades.csv', index=False, encoding='utf-8-sig')
    analyze(summary, trades)
    atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': total, 'planned_runs': total, 'complete': True})


def analyze(summary, trades):
    summary['expectancy'] = summary.closed_net_profit_usdt / summary.closed_trades.replace(0, np.nan)
    costs = summary.groupby(['profile', 'cost_pct_per_order'], as_index=False).agg(
        total_closed_pnl=('closed_net_profit_usdt', 'sum'), total_trades=('closed_trades', 'sum'),
        mean_block_expectancy=('expectancy', 'mean'), median_block_expectancy=('expectancy', 'median'),
        median_drawdown_pct=('max_drawdown_pct_initial', 'median'), max_drawdown_pct=('max_drawdown_pct_initial', 'max'))
    costs['pooled_expectancy'] = costs.total_closed_pnl / costs.total_trades
    costs.to_csv(OUT / 'cost_stress.csv', index=False, encoding='utf-8-sig')

    baseline = summary[summary.cost_pct_per_order == 0.08]
    control = baseline[baseline.profile == 'P0_BASE'][['symbol', 'window', 'expectancy']].rename(columns={'expectancy': 'control_expectancy'})
    paired = baseline.merge(control, on=['symbol', 'window'])
    paired['delta_expectancy'] = paired.expectancy - paired.control_expectancy
    loo = []
    for profile, rows in paired[paired.profile != 'P0_BASE'].groupby('profile'):
        for omitted in SYMBOLS:
            kept = rows[rows.symbol != omitted]
            loo.append({'profile': profile, 'omitted_symbol': omitted,
                        'mean_delta_expectancy': kept.delta_expectancy.mean(),
                        'favorable_share': (kept.delta_expectancy > 0).mean()})
    pd.DataFrame(loo).to_csv(OUT / 'leave_one_symbol_out.csv', index=False, encoding='utf-8-sig')

    concentration = []
    for profile, rows in trades.groupby('profile'):
        ordered = rows.net_pnl.sort_values(ascending=False)
        total = rows.net_pnl.sum()
        adjusted = ordered.iloc[5:].sum()
        concentration.append({'profile': profile, 'trades': len(rows), 'net_pnl': total,
                              'five_largest_wins': ordered.iloc[:5].sum(),
                              'net_pnl_without_top5': adjusted,
                              'expectancy_without_top5': adjusted / max(len(rows) - 5, 1),
                              'top5_share_of_positive_pnl': ordered.iloc[:5].sum() / rows.loc[rows.net_pnl > 0, 'net_pnl'].sum()})
    concentration = pd.DataFrame(concentration)
    concentration.to_csv(OUT / 'winner_concentration.csv', index=False, encoding='utf-8-sig')

    loo_summary = pd.DataFrame(loo).groupby('profile').agg(
        worst_loo_delta=('mean_delta_expectancy', 'min'), best_loo_delta=('mean_delta_expectancy', 'max'),
        worst_loo_favorable_share=('favorable_share', 'min')).reset_index()
    base_cost = costs[costs.cost_pct_per_order == 0.08]
    high_cost = costs[costs.cost_pct_per_order == 0.16][['profile', 'pooled_expectancy']].rename(columns={'pooled_expectancy': 'pooled_expectancy_cost_016'})
    final = base_cost.merge(high_cost, on='profile').merge(concentration, on='profile').merge(loo_summary, on='profile', how='left')
    final.to_csv(OUT / 'robustness_summary.csv', index=False, encoding='utf-8-sig')
    lines = ['# Master Robustness v1', '',
             'Trade-level screen on the five predeclared profiles. Results remain a research approximation.', '',
             '| Profile | Pooled expectancy | At 0.16% cost | Without five largest wins | Worst leave-one-symbol-out delta |',
             '|---|---:|---:|---:|---:|']
    for row in final.itertuples():
        loo_value = getattr(row, 'worst_loo_delta', np.nan)
        lines.append(f'| {row.profile} | {row.pooled_expectancy:.4f} | {row.pooled_expectancy_cost_016:.4f} | {row.expectancy_without_top5:.4f} | {loo_value:.4f} |')
    lines += ['', 'Open risk: the OHLC path approximation and zero-distance trailing have not passed Pine high-detail parity.', '']
    (OUT / 'analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    print(final[['profile', 'pooled_expectancy', 'pooled_expectancy_cost_016', 'expectancy_without_top5', 'worst_loo_delta']].to_string(index=False))


if __name__ == '__main__':
    run()
