"""VWAP gate economic screen with direct and nested G6 wiring delays."""
from pathlib import Path
import numpy as np
import pandas as pd

import quant_lab as lab
from gate_models import VwapGateConfig, strategy_gate_open, vwap_ext_score
from lab_safety import atomic_write_json, engine_identity, file_lock
from run_wct_categorical_combo_screen import Combo, simulate

SYMBOLS = 'WCTUSDT INITUSDT DUSKUSDT MYXUSDT CETUSUSDT TAOUSDT DEXEUSDT TAKEUSDT BANANAS31USDT SIRENUSDT BROCCOLI714USDT FHEUSDT BTRUSDT'.split()
WINDOWS = [('2025-09-01', '2026-03-01'), ('2026-03-01', '2026-06-01'), ('2026-06-01', '2026-09-01')]
VARIANTS = [('CONTROL', None, None, True)] + [
    (f'{mode}_{wiring}_{authority}', mode, wiring, authority == 'PINE_EXIT')
    for mode in ('Daily Only', 'Weekly Only', 'D+W Confirm')
    for wiring in ('DIRECT', 'MASTER_GATE_G6')
    for authority in ('PINE_EXIT', 'ENTRY_ONLY')]
OUT = lab.LAB_ROOT / 'reports/vwap_gate_screen_v2'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = lab.load_config(lab.LAB_ROOT / 'config.json')
    rules = lab.load_execution_rules()
    manifest = {'symbols': SYMBOLS, 'windows': WINDOWS, 'variants': [row[0] for row in VARIANTS],
                'vwap_defaults': {'band_bps': 0, 'persist_bars': 3}, 'profile': 'NONE + ER v2 + Shadow HOLD',
                'planned_runs': 507, 'engine': engine_identity(lab.LAB_ROOT),
                'status': 'RESEARCH_APPROXIMATION',
                'limitations': ['VWAP Python model has not passed TradingView golden-export parity',
                                'MASTER_GATE_G6 represents G6-only nested wiring; other Master Gate components are absent']}
    atomic_write_json(OUT / 'manifest.json', manifest)
    data_root = lab.PROJECT_ROOT / 'research/data/raw'
    paths = lab.discover_market_roots(data_root, SYMBOLS, config['interval'])
    combo = Combo('NONE', 'v2', True, 'SHADOW', 'WEIGHTED', False)
    rows = []
    with file_lock(lab.LAB_ROOT / 'worker.lock', timeout=0):
        for symbol in SYMBOLS:
            full_bars = lab.load_binance_archives(paths[symbol])
            scores = {mode: vwap_ext_score(full_bars, VwapGateConfig(mode=mode)).ext_score
                      for mode in ('Daily Only', 'Weekly Only', 'D+W Confirm')}
            cfg, metadata = lab.symbol_strategy_config(config, symbol, None, rules)
            for window, (start_text, end_text) in enumerate(WINDOWS, 1):
                bars, check = lab.checked_bars(full_bars, pd.Timestamp(start_text, tz='UTC'),
                                               pd.Timestamp(end_text, tz='UTC'), config['interval'])
                if check['status'] != 'PASS':
                    raise ValueError(f'{symbol} W{window}: {check}')
                for variant, mode, wiring, closes_position in VARIANTS:
                    gate = None if mode is None else strategy_gate_open(scores[mode], wiring).reindex(bars.index).fillna(False)
                    _, summary = simulate(bars, None, combo, base_config=cfg, external_gate_open=gate,
                                          external_gate_closes_position=closes_position)
                    rows.append({**summary, 'variant': variant, 'symbol': symbol, 'window': window,
                                 'mode': mode or 'CONTROL', 'wiring': wiring or 'CONTROL',
                                 'authority': 'PINE_EXIT' if closes_position else 'ENTRY_ONLY',
                                 'gate_open_share': 1.0 if gate is None else float(gate.mean()),
                                 'tick_size': metadata['tick_size'], 'coverage': check['coverage']})
            pd.DataFrame(rows).to_csv(OUT / 'results_checkpoint.csv', index=False)
            atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': len(rows), 'planned_runs': 507,
                                                         'last_symbol': symbol, 'complete': False})
            print(f'VWAP checkpoint: {len(rows)}/507', flush=True)
    data = pd.DataFrame(rows)
    data.to_csv(OUT / 'results.csv', index=False, encoding='utf-8-sig')
    analyze(data)
    atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': 507, 'planned_runs': 507, 'complete': True})


def analyze(data):
    data['expectancy'] = data.closed_net_profit_usdt / data.closed_trades.replace(0, np.nan)
    control = data[data.variant == 'CONTROL'][['symbol', 'window', 'expectancy', 'total_pnl_usdt',
                                               'max_drawdown_pct_initial', 'closed_trades']]
    candidates = data[data.variant != 'CONTROL'].merge(control, on=['symbol', 'window'], suffixes=('', '_control'))
    for metric in ('expectancy', 'total_pnl_usdt', 'max_drawdown_pct_initial', 'closed_trades'):
        candidates['delta_' + metric] = candidates[metric] - candidates[metric + '_control']
    summary = candidates.groupby(['variant', 'mode', 'wiring', 'authority'], as_index=False).agg(
        blocks=('symbol', 'size'), gate_open_share=('gate_open_share', 'mean'),
        mean_expectancy=('expectancy', 'mean'), median_expectancy=('expectancy', 'median'),
        mean_delta_expectancy=('delta_expectancy', 'mean'),
        favorable_share=('delta_expectancy', lambda value: (value > 0).mean()),
        mean_delta_pnl=('delta_total_pnl_usdt', 'mean'),
        mean_delta_drawdown=('delta_max_drawdown_pct_initial', 'mean'),
        mean_delta_trades=('delta_closed_trades', 'mean'),
        external_gate_exits=('external_gate_exit_count', 'sum'))
    windows = candidates.groupby(['variant', 'window']).delta_expectancy.mean().unstack()
    summary = summary.merge((windows > 0).sum(axis=1).rename('positive_windows').reset_index(), on='variant')
    symbols = candidates.groupby(['variant', 'symbol']).delta_expectancy.mean().unstack()
    summary = summary.merge((symbols > 0).sum(axis=1).rename('positive_symbols').reset_index(), on='variant')
    rng = np.random.default_rng(20260916)
    intervals = []
    for variant, group in candidates.groupby('variant'):
        # Resample symbols, not 39 symbol-windows: three windows from one coin
        # share market history and are not independent evidence.
        values = group.groupby('symbol').delta_expectancy.mean().dropna().to_numpy()
        boot = rng.choice(values, size=(10000, len(values)), replace=True).mean(axis=1)
        intervals.append({'variant': variant, 'ci95_low': np.quantile(boot, .025),
                          'ci95_high': np.quantile(boot, .975)})
    summary = summary.merge(pd.DataFrame(intervals), on='variant')
    summary.sort_values('mean_delta_expectancy', ascending=False).to_csv(OUT / 'paired_summary.csv', index=False, encoding='utf-8-sig')
    candidates.to_csv(OUT / 'paired_blocks.csv', index=False, encoding='utf-8-sig')
    authority = candidates.pivot(index=['symbol', 'window', 'mode', 'wiring'], columns='authority',
                                 values='expectancy').dropna().reset_index()
    authority['entry_only_minus_pine_exit'] = authority.ENTRY_ONLY - authority.PINE_EXIT
    authority.to_csv(OUT / 'authority_paired_blocks.csv', index=False, encoding='utf-8-sig')
    authority_summary = authority.groupby(['mode', 'wiring'], as_index=False).agg(
        blocks=('symbol', 'size'), mean_effect=('entry_only_minus_pine_exit', 'mean'),
        favorable_share=('entry_only_minus_pine_exit', lambda value: (value > 0).mean()))
    authority_summary.to_csv(OUT / 'authority_summary.csv', index=False, encoding='utf-8-sig')
    ordered = summary.sort_values('mean_delta_expectancy', ascending=False)
    lines = ['# VWAP Gate Screen v2', '',
             'The control is ER v2 + Shadow HOLD with the external gate continuously open.', '',
             '| Variant | Open share | Δ expectancy | 95% symbol-cluster CI | Favorable blocks | Symbols | Windows | Δ drawdown | Gate exits |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in ordered.itertuples():
        lines.append(f'| {row.variant} | {row.gate_open_share:.1%} | {row.mean_delta_expectancy:.4f} | [{row.ci95_low:.4f}, {row.ci95_high:.4f}] | {row.favorable_share:.1%} | {row.positive_symbols}/13 | {row.positive_windows}/3 | {row.mean_delta_drawdown:.2f} pp | {row.external_gate_exits} |')
    lines += ['', '## Forced-exit authority effect', '',
              'Positive means ENTRY_ONLY outperformed the current Pine-style forced close.', '',
              '| Mode | Wiring | ENTRY_ONLY - PINE_EXIT expectancy | Favorable blocks |',
              '|---|---|---:|---:|']
    for row in authority_summary.sort_values('mean_effect', ascending=False).itertuples():
        lines.append(f'| {row.mode} | {row.wiring} | {row.mean_effect:.4f} | {row.favorable_share:.1%} |')
    lines += ['', 'The interval resamples 13 symbols, not 39 dependent symbol-windows. Twelve variants were screened, so a positive point estimate or interval alone would still require a separate untouched validation sample. This screen measures retrospective economics; Pine parity is still required before accepting a gate.', '']
    (OUT / 'analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    print(ordered[['variant', 'gate_open_share', 'mean_delta_expectancy', 'favorable_share',
                   'positive_windows', 'mean_delta_drawdown', 'external_gate_exits']].to_string(index=False))


if __name__ == '__main__':
    main()
