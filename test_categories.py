"""User organization metadata, stored separately from execution and results."""
import json
from lab_safety import atomic_write_json, file_lock


def load_categories(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'categories': [], 'assignments': {}}


def save_category(path, name, job_ids=(), clear=False):
    name = name.strip()
    if not clear and (not name or len(name) > 80 or name in ('Tümü', 'Kategorisiz')):
        raise ValueError('1–80 karakterlik bir kategori adı girin. Tümü ve Kategorisiz ayrılmış adlardır.')
    with file_lock(path.with_suffix('.lock')):
        data = load_categories(path)
        if not clear and name not in data['categories']:
            data['categories'].append(name)
        for job_id in job_ids:
            if clear:
                data['assignments'].pop(job_id, None)
            else:
                data['assignments'][job_id] = name
        atomic_write_json(path, data)
    return data
