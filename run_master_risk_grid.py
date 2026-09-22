"""Coarse risk sensitivity grid for ER v2 with Shadow OFF versus HOLD."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import quant_lab as lab
from lab_safety import atomic_write_json, engine_identity, file_lock

SYMBOLS = 'WCTUSDT INITUSDT DUSKUSDT MYXUSDT CETUSUSDT TAOUSDT DEXEUSDT TAKEUSDT BANANAS31USDT SIRENUSDT BROCCOLI714USDT FHEUSDT BTRUSDT'.split()
WINDOWS = [('2025-09-01', '2026-03-01'), ('2026-03-01', '2026-06-01'), ('2026-06-01', '2026-09-01')]
GRID = {'stop_loss_pct': [2.0, 2.3, 3.0], 'trailing_activation_pct': [1.4, 2.2, 3.0],
        'trailing_distance_pct': [0.0, 0.4, 0.8]}
OUT = lab.LAB_ROOT / 'reports/master_risk_grid_v1'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = lab.load_config(lab.LAB_ROOT / 'config.json')
    manifest = {'symbols': SYMBOLS, 'windows': WINDOWS, 'parameter_grid': GRID,
                'profiles': ['ER_V2_WR_OFF', 'ER_V2_WR_HOLD'], 'planned_runs': 2106,
                'engine': engine_identity(lab.LAB_ROOT), 'status': 'RESEARCH_APPROXIMATION',
                'purpose': 'Coarse plateau and Shadow interaction screen; not optimum selection.'}
    manifest_path = OUT / 'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise ValueError('Frozen study identity changed')
    atomic_write_json(manifest_path, manifest)
    all_rows = []
    with file_lock(lab.LAB_ROOT / 'worker.lock', timeout=0):
        for window, (start, end) in enumerate(WINDOWS, 1):
            for symbol in SYMBOLS:
                folder = OUT / f'W{window}_{symbol}'
                complete = folder / 'complete.json'
                if complete.exists():
                    rows = pd.read_csv(folder / 'job_results.csv').to_dict('records')
                else:
                    job = {'id': f'MRISK-W{window}-{symbol}', 'symbols': [symbol], 'start': start, 'end': end,
                           'categorical': {'exit_family': ['NONE'], 'er': ['v2'],
                                           'winrate_state': ['OFF', 'SH_WEIGHTED_HOLD']},
                           'parameter_grid': GRID, 'max_runs': 54}
                    rows = lab.run_job(config, job, lab.PROJECT_ROOT / 'research/data/raw', folder)
                    if len(rows) != 54:
                        raise ValueError(f'{folder.name}: expected 54 rows, got {len(rows)}')
                    atomic_write_json(complete, {'rows': 54})
                all_rows.extend(dict(row, window=window) for row in rows)
                pd.DataFrame(all_rows).to_csv(OUT / 'results.csv', index=False)
                atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': len(all_rows), 'planned_runs': 2106,
                                                             'last_block': folder.name, 'complete': False})
                print(f'Risk grid checkpoint: {len(all_rows)}/2106', flush=True)
    analyze(pd.DataFrame(all_rows))
    atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': 2106, 'planned_runs': 2106, 'complete': True})


def analyze(data):
    data['wr_state'] = np.where(data.winrate_enabled, 'HOLD', 'OFF')
    data['expectancy'] = data.closed_net_profit_usdt / data.closed_trades.replace(0, np.nan)
    parameters = data.parameter_overrides.map(json.loads).apply(pd.Series)
    for column in GRID:
        data[column] = parameters[column]
    keys = list(GRID)
    grouped = data.groupby([*keys, 'wr_state'], as_index=False).agg(
        blocks=('symbol', 'size'), total_pnl=('closed_net_profit_usdt', 'sum'), total_trades=('closed_trades', 'sum'),
        mean_expectancy=('expectancy', 'mean'), median_expectancy=('expectancy', 'median'),
        median_drawdown=('max_drawdown_pct_initial', 'median'), max_drawdown=('max_drawdown_pct_initial', 'max'))
    grouped['pooled_expectancy'] = grouped.total_pnl / grouped.total_trades
    grouped.to_csv(OUT / 'setting_summary.csv', index=False, encoding='utf-8-sig')

    metrics = ['expectancy', 'max_drawdown_pct_initial', 'closed_trades', 'total_pnl_usdt']
    off = data[data.wr_state == 'OFF'][['symbol', 'window', *keys, *metrics]]
    hold = data[data.wr_state == 'HOLD'][['symbol', 'window', *keys, *metrics]]
    paired = hold.merge(off, on=['symbol', 'window', *keys], suffixes=('_hold', '_off'), validate='one_to_one')
    for metric in metrics:
        paired['delta_' + metric] = paired[metric + '_hold'] - paired[metric + '_off']
    effect = paired.groupby(keys, as_index=False).agg(
        mean_delta_expectancy=('delta_expectancy', 'mean'), median_delta_expectancy=('delta_expectancy', 'median'),
        favorable_share=('delta_expectancy', lambda value: (value > 0).mean()),
        mean_delta_drawdown=('delta_max_drawdown_pct_initial', 'mean'),
        mean_delta_trades=('delta_closed_trades', 'mean'))
    window = paired.groupby([*keys, 'window']).delta_expectancy.mean().unstack()
    effect = effect.merge((window > 0).sum(axis=1).rename('positive_windows').reset_index(), on=keys)
    effect.to_csv(OUT / 'shadow_effect_by_setting.csv', index=False, encoding='utf-8-sig')

    hold_summary = grouped[grouped.wr_state == 'HOLD'].copy()
    exp_floor = hold_summary.median_expectancy.median()
    dd_ceiling = hold_summary.median_drawdown.median()
    hold_summary['plateau_candidate'] = ((hold_summary.median_expectancy >= exp_floor) &
                                         (hold_summary.median_drawdown <= dd_ceiling))
    hold_summary.merge(effect, on=keys).sort_values(
        ['plateau_candidate', 'median_expectancy', 'median_drawdown'], ascending=[False, False, True]
    ).to_csv(OUT / 'plateau_candidates.csv', index=False, encoding='utf-8-sig')
    top = hold_summary.merge(effect, on=keys).sort_values(
        ['plateau_candidate', 'median_expectancy', 'median_drawdown'], ascending=[False, False, True]).head(10)
    lines = ['# Master Risk Grid v1', '', 'Coarse sensitivity screen; rows are descriptive and are not an optimized production setting.', '',
             '| SL | Trail activation | Trail distance | Median expectancy | Median DD | Shadow Δ expectancy | Favorable | Windows |',
             '|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in top.itertuples():
        lines.append(f'| {row.stop_loss_pct:.1f} | {row.trailing_activation_pct:.1f} | {row.trailing_distance_pct:.1f} | {row.median_expectancy:.4f} | {row.median_drawdown:.2f}% | {row.mean_delta_expectancy:.4f} | {row.favorable_share:.1%} | {row.positive_windows}/3 |')
    lines += ['', 'Zero trailing distance means Pine minimum one-tick offset in this emulator; exact high-detail parity remains open.', '']
    (OUT / 'analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    print(top[[*keys, 'median_expectancy', 'median_drawdown', 'mean_delta_expectancy', 'favorable_share', 'positive_windows']].to_string(index=False))


if __name__ == '__main__':
    main()
