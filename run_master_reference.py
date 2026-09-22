"""Resumable fixed-setting Master reference screen. No model API calls."""
import json
from pathlib import Path
import pandas as pd
import quant_lab as lab
from lab_safety import atomic_write_json, engine_identity, file_lock

SYMBOLS = 'WCTUSDT INITUSDT DUSKUSDT MYXUSDT CETUSUSDT TAOUSDT DEXEUSDT TAKEUSDT BANANAS31USDT SIRENUSDT BROCCOLI714USDT FHEUSDT BTRUSDT'.split()
WINDOWS = [('2025-09-01', '2026-03-01'), ('2026-03-01', '2026-06-01'), ('2026-06-01', '2026-09-01')]
OUT = lab.LAB_ROOT / 'reports/master_reference_v1'

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = lab.load_config(lab.LAB_ROOT / 'config.json')
    identity = engine_identity(lab.LAB_ROOT)
    manifest = {'symbols': SYMBOLS, 'windows': WINDOWS, 'config': config, 'engine': identity,
                'status': 'RESEARCH_APPROXIMATION', 'planned_runs': 1404,
                'limitations': ['Previously seen periods, not untouched OOS',
                    'Zero-distance trailing fill parity unresolved',
                    'Indicators and Shadow restart at window start',
                    'External gates absent; no production conclusions'],
                'purpose': 'Fixed-setting component screen; ER v1 added to prior 24-config protocol (36 configs).'}
    path = OUT / 'manifest.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) != manifest:
        raise ValueError('Frozen study identity changed; create a new study version')
    atomic_write_json(path, manifest)
    with file_lock(lab.LAB_ROOT / 'worker.lock', timeout=0):
        all_rows = []
        for number, (start, end) in enumerate(WINDOWS, 1):
            for symbol in SYMBOLS:
                folder = OUT / f'W{number}_{symbol}'
                done = folder / 'complete.json'
                if done.exists():
                    rows = pd.read_csv(folder / 'job_results.csv').to_dict('records')
                else:
                    job = {'id': f'MREF-W{number}-{symbol}', 'symbols': [symbol],
                           'start': start, 'end': end, 'max_runs': 36,
                           'categorical': {'exit_family': ['NONE', 'MOST', 'RSI_SMA', 'TILLSON'],
                               'er': ['OFF', 'v1', 'v2'],
                               'winrate_state': ['OFF', 'SH_WEIGHTED_HOLD', 'SH_WEIGHTED_CLOSE']}}
                    rows = lab.run_job(config, job, lab.PROJECT_ROOT / 'research/data/raw', folder)
                    if len(rows) != 36:
                        raise ValueError('Incomplete block')
                    atomic_write_json(done, {'rows': 36})
                all_rows.extend(dict(row, window=number) for row in rows)
                pd.DataFrame(all_rows).to_csv(OUT / 'results.csv', index=False)
                atomic_write_json(OUT / 'checkpoint.json', {'finished_cells': len(all_rows),
                    'planned_cells': 1404, 'last_block': folder.name,
                    'complete': len(all_rows) == 1404})
                print(f'Checkpoint: {len(all_rows)}/1404', flush=True)
    print('Reference screen complete', flush=True)

if __name__ == '__main__':
    main()
