"""Read-only WCT PMAX clock probe; no Strategy economics or Pine parity claim."""
from pathlib import Path
import json
import sys

import pandas as pd

import quant_lab as lab

sys.path.insert(0, str(lab.PROJECT_ROOT / 'research/scripts'))
from market_data import load_binance_archives, quality_report  # noqa: E402
from parity_pmax import map_security, pmax, resample_ohlcv  # noqa: E402

OUT = lab.LAB_ROOT / 'reports/pmax_gate_audit_v1'
ONE_MIN = lab.PROJECT_ROOT / 'research/data/raw/binance_um/master_ref00/WCTUSDT/1m'
THIRTY_MIN = lab.PROJECT_ROOT / 'research/data/raw/binance_um/pilot13/WCTUSDT/30m'
MONTHS = ('2026-05', '2026-06', '2026-07', '2026-08')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    one = load_binance_archives([ONE_MIN / f'WCTUSDT-1m-{month}.csv' for month in MONTHS])
    native_paths = sorted(path for month in MONTHS for path in THIRTY_MIN.glob(f'WCTUSDT-30m-{month}*.csv'))
    native = load_binance_archives(native_paths)
    one_q = quality_report(one, '1m').to_dict()
    native_q = quality_report(native, '30m').to_dict()
    if any(one_q[key] for key in ('conflicting_duplicate_timestamps', 'missing_intervals', 'invalid_ohlc_rows', 'nonpositive_prices')):
        raise ValueError(f'1m data quality failure: {one_q}')
    if any(native_q[key] for key in ('conflicting_duplicate_timestamps', 'missing_intervals', 'invalid_ohlc_rows', 'nonpositive_prices')):
        raise ValueError(f'30m data quality failure: {native_q}')
    chart = resample_ohlcv(one, '30min')
    matched = chart[['open', 'high', 'low', 'close']].join(
        native[['open', 'high', 'low', 'close']], how='inner', lsuffix='_1m', rsuffix='_native')
    price_disagreements = {
        field: int(((matched[field + '_1m'] - matched[field + '_native']).abs() > 1e-10).sum())
        for field in ('open', 'high', 'low', 'close')}
    max_ohlc_abs_error = {
        field: float((matched[field + '_1m'] - matched[field + '_native']).abs().max())
        for field in ('open', 'high', 'low', 'close')}
    if len(matched) != len(native):
        raise ValueError(f'1m-to-30m bar-count mismatch: rows {len(matched)}/{len(native)}')
    streams = {}
    for label, rule in (('SOURCE_24M', '24min'), ('CHART_30M', '30min')):
        requested = resample_ohlcv(one, rule)
        raw = pmax(requested, ma_len=9, atr_len=11, atr_mult=1.9, pmax_height=1.0)
        # Prior requested-TF state from Master Gate, then [1] chart bar in Strategy.
        streams[label] = map_security(raw, chart.index, rule)['state'].shift(1).fillna(0).astype(int)
    compare = pd.DataFrame(streams, index=chart.index)
    compare = compare.loc['2026-08-01':'2026-08-31'].copy()
    compare['different'] = compare.SOURCE_24M.ne(compare.CHART_30M).astype(int)
    compare.to_csv(OUT / 'wct_august_source24m_vs_chart30m.csv', encoding='utf-8-sig')
    result = {
        'test_id': 'CLOCK-PMAX-WCT-24M-001',
        'status': 'RESEARCH_APPROXIMATION_NOT_PINE_PARITY',
        'symbol': 'WCTUSDT', 'market': 'Binance USD-M perpetual',
        'source_months': MONTHS, 'evaluation_month': '2026-08',
        'data_quality_1m': one_q, 'data_quality_30m': native_q,
        'one_minute_to_native_30m_ohlc_checked_rows': len(matched),
        'one_minute_to_native_30m_ohlc_disagreements': price_disagreements,
        'one_minute_to_native_30m_max_abs_error': max_ohlc_abs_error,
        'august_chart_bars': len(compare),
        'source24m_open_share': float(compare.SOURCE_24M.mean()),
        'chart30m_open_share': float(compare.CHART_30M.mean()),
        'decision_disagreement_bars': int(compare.different.sum()),
        'decision_disagreement_share': float(compare.different.mean()),
        'source24m_rising_edges': int(((compare.SOURCE_24M == 1) & (compare.SOURCE_24M.shift(fill_value=0) == 0)).sum()),
        'chart30m_rising_edges': int(((compare.CHART_30M == 1) & (compare.CHART_30M.shift(fill_value=0) == 0)).sum()),
        'limitations': ['1m-resampled versus native 30m OHLC differs in some bars; investigate archive revisions/venue candle construction',
                        'Source default cfg_tf=24m; screenshot preset can differ',
                        'Python PMAX has not passed bar-by-bar TradingView line/state parity',
                        'This is a timing/state probe, not Strategy PnL or a gate-value verdict']}
    (OUT / 'wct_clock_probe.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('august_chart_bars', 'decision_disagreement_bars',
                                                   'decision_disagreement_share', 'source24m_rising_edges',
                                                   'chart30m_rising_edges')}, indent=2))


if __name__ == '__main__':
    main()
