"""Recover one concrete run, never its parent's entire search grid."""
import copy
import json


def result_seed(row, parent):
    if row.get('status') != 'COMPLETE':
        raise ValueError('Yalnız tamamlanan koşular başlangıç olarak kullanılabilir.')
    if row.get('job_id') != parent.get('id'):
        raise ValueError('Sonuç ve kaynak iş eşleşmiyor.')
    config = copy.deepcopy(parent.get('config_snapshot'))
    if not config:
        raise ValueError('Kaynak ayar kaydı eksik; otomatik aktarım yapılamıyor.')
    values = dict(config.get('strategy', {}))
    for field in ('strategy_parameter_overrides', 'gate_parameter_overrides'):
        raw = row.get(field)
        if not raw:
            raise ValueError(f'Koşunun tam ayar kaydı eksik: {field}')
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            raise ValueError(f'Geçersiz ayar kaydı: {field}')
        values.update(parsed)
    enabled = str(row.get('winrate_enabled')).lower() == 'true'
    if enabled and (row.get('winrate_source') != 'SHADOW' or row.get('winrate_mode') != 'WEIGHTED'):
        raise ValueError('Bu Winrate modeli arayüzde desteklenmiyor.')
    state = 'OFF' if not enabled else ('SH_WEIGHTED_CLOSE' if str(row.get('close_on_block')).lower() == 'true' else 'SH_WEIGHTED_HOLD')
    return dict(config=config, values=values, symbol=row['symbol'],
                start=parent['start'], end=parent['end'],
                categorical=dict(exit_family=row['exit_family'], er=row['er'], winrate_state=state),
                context={key: copy.deepcopy(parent[key]) for key in (
                    'gate_feed_manifest', 'gate_feed_snapshot', 'execution_data_policy',
                    'historical_universe', 'save_gate_trace') if key in parent},
                lineage=dict(job_id=parent['id'], parameter_id=row.get('parameter_id'),
                             combo_id=row.get('combo_id'), symbol=row['symbol'],
                             engine_snapshot=copy.deepcopy(parent.get('engine_snapshot'))))
