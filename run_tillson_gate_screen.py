"""Frozen isolated G2 Confirmed-HTF categorical screen, research only."""
import numpy as np
import pandas as pd

import quant_lab as lab
from gate_models import confirmed_tillson_gate2
from lab_safety import atomic_write_json, engine_identity, file_lock
from run_wct_categorical_combo_screen import Combo, simulate

SYMBOLS = 'WCTUSDT INITUSDT DUSKUSDT MYXUSDT CETUSUSDT TAOUSDT DEXEUSDT TAKEUSDT BANANAS31USDT SIRENUSDT BROCCOLI714USDT FHEUSDT BTRUSDT'.split()
WINDOWS = [('2025-09-01', '2026-03-01'), ('2026-03-01', '2026-06-01'), ('2026-06-01', '2026-09-01')]
VARIANTS = [('CONTROL', None, True)] + [
    (f'{mode}_{authority}', mode, authority == 'PINE_EXIT')
    for mode in ('Price vs T3', 'T3 Slope') for authority in ('ENTRY_ONLY', 'PINE_EXIT')]
OUT = lab.LAB_ROOT / 'reports/tillson_gate_screen_v1'


def analyze(data):
    data['expectancy'] = data.closed_net_profit_usdt / data.closed_trades.replace(0, np.nan)
    control = data[data.variant == 'CONTROL'][['symbol', 'window', 'expectancy', 'total_pnl_usdt',
                                               'max_drawdown_pct_initial', 'closed_trades']]
    compared = data[data.variant != 'CONTROL'].merge(control, on=['symbol', 'window'], suffixes=('', '_control'))
    for metric in ('expectancy', 'total_pnl_usdt', 'max_drawdown_pct_initial', 'closed_trades'):
        compared['delta_' + metric] = compared[metric] - compared[metric + '_control']
    rng = np.random.default_rng(20260916)
    rows = []
    for variant, group in compared.groupby('variant'):
        symbols = group.groupby('symbol').delta_expectancy.mean().dropna()
        boot = rng.choice(symbols.to_numpy(), size=(10000, len(symbols)), replace=True).mean(axis=1)
        rows.append({'variant': variant, 'blocks': len(group),
                     'mean_delta_expectancy': group.delta_expectancy.mean(),
                     'ci95_symbol_low': float(np.quantile(boot, .025)),
                     'ci95_symbol_high': float(np.quantile(boot, .975)),
                     'favorable_blocks': int(group.delta_expectancy.gt(0).sum()),
                     'favorable_symbols': int(symbols.gt(0).sum()),
                     'positive_windows': int(group.groupby('window').delta_expectancy.mean().gt(0).sum()),
                     'mean_delta_pnl': group.delta_total_pnl_usdt.mean(),
                     'mean_delta_drawdown_pp': group.delta_max_drawdown_pct_initial.mean(),
                     'mean_delta_closed_trades': group.delta_closed_trades.mean(),
                     'mean_gate_open_share': group.gate_open_share.mean(),
                     'gate_forced_exits': int(group.external_gate_exit_count.sum())})
    summary = pd.DataFrame(rows).sort_values('variant')
    summary.to_csv(OUT / 'paired_summary.csv', index=False, encoding='utf-8-sig')
    compared.to_csv(OUT / 'paired_blocks.csv', index=False, encoding='utf-8-sig')
    lines = ['# Isolated G2/Tillson Confirmed HTF screen v1', '',
             'Frozen source defaults: 60-minute T3, length 8, factor 0.7, execution mode Confirmed HTF. Two categorical decision modes were compared separately.',
             'Control: no external gate; ER-v2 + Shadow-HOLD; fixed risk and execution rules. Other Master components and macro armor are off.', '',
             '| Variant | Δ expectancy USDT/trade | 95% symbol-cluster CI | Favorable blocks | Symbols | Windows | Δ total PnL USDT | Δ drawdown pp | Forced exits |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in summary.itertuples():
        lines.append(f'| {row.variant} | {row.mean_delta_expectancy:.4f} | [{row.ci95_symbol_low:.4f}, {row.ci95_symbol_high:.4f}] | {row.favorable_blocks}/{row.blocks} | {row.favorable_symbols}/13 | {row.positive_windows}/3 | {row.mean_delta_pnl:.2f} | {row.mean_delta_drawdown_pp:.2f} | {row.gate_forced_exits} |')
    lines += ['', 'These windows were seen in earlier exploratory work, so they are not untouched OOS. The Python T3, fill and trailing engines have not passed full Pine parity. Four alternatives were examined; no selected point estimate is sufficient for promotion.']
    (OUT / 'analysis.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(summary.to_string(index=False))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = lab.load_config(lab.LAB_ROOT / 'config.json')
    rules = lab.load_execution_rules()
    atomic_write_json(OUT / 'manifest.json', {
        'symbols': SYMBOLS, 'windows': WINDOWS, 'variants': [v for v, _, _ in VARIANTS],
        'planned_runs': 195, 'g2': {'tf': '60', 't3_length': 8, 't3_factor': 0.7,
                                    'execution_mode': 'Confirmed HTF'},
        'control': 'NONE + ER v2 + Shadow HOLD',
        'excluded_source_mode': 'Live Cross uses unshifted HTF T3 line under lookahead_on',
        'isolated_master': 'only G2 active, macro armor off; Strategy external [1] delay retained',
        'status': 'RESEARCH_APPROXIMATION', 'engine': engine_identity(lab.LAB_ROOT)})
    paths = lab.discover_market_roots(lab.PROJECT_ROOT / 'research/data/raw', SYMBOLS, config['interval'])
    combo = Combo('NONE', 'v2', True, 'SHADOW', 'WEIGHTED', False)
    rows = []
    with file_lock(lab.LAB_ROOT / 'worker.lock', timeout=0):
        for symbol in SYMBOLS:
            full = lab.load_binance_archives(paths[symbol])
            gates = {mode: confirmed_tillson_gate2(full, mode=mode).strategy_gate_open
                     for mode in ('Price vs T3', 'T3 Slope')}
            cfg, metadata = lab.symbol_strategy_config(config, symbol, None, rules)
            for window, (start, end) in enumerate(WINDOWS, 1):
                bars, check = lab.checked_bars(full, pd.Timestamp(start, tz='UTC'), pd.Timestamp(end, tz='UTC'), config['interval'])
                if check['status'] != 'PASS':
                    raise ValueError(f'{symbol} W{window}: {check}')
                for variant, mode, closes_position in VARIANTS:
                    gate = None if mode is None else gates[mode].reindex(bars.index).fillna(False)
                    _, summary = simulate(bars, None, combo, base_config=cfg, external_gate_open=gate,
                                          external_gate_closes_position=closes_position)
                    rows.append({**summary, 'symbol': symbol, 'window': window, 'variant': variant,
                                 'mode': mode or 'CONTROL',
                                 'gate_open_share': 1.0 if gate is None else float(gate.mean()),
                                 'tick_size': metadata['tick_size'], 'coverage': check['coverage']})
            pd.DataFrame(rows).to_csv(OUT / 'results_checkpoint.csv', index=False)
            atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': len(rows), 'planned_runs': 195,
                                                        'last_symbol': symbol, 'complete': False})
            print(f'G2 checkpoint: {len(rows)}/195', flush=True)
    data = pd.DataFrame(rows)
    data.to_csv(OUT / 'results.csv', index=False, encoding='utf-8-sig')
    analyze(data)
    atomic_write_json(OUT / 'checkpoint.json', {'finished_runs': 195, 'planned_runs': 195, 'complete': True})


if __name__ == '__main__':
    main()
