"""Queue an effectively always-open EXT control for gate-value attribution."""
from __future__ import annotations

import copy

from enqueue_vwap_topology_pair import topology_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


def main() -> None:
    job = copy.deepcopy(topology_job("DIRECT"))
    job["id"] = "AUTO-XMKT-S8-OPEN-GATE-CONTROL"
    job["symbols"] = ["WCTUSDT", "DUSKUSDT", "TAOUSDT"]
    job["planned_runs"] = 3
    job["max_runs"] = 3
    job["description"] = "Üç aktif coin nötr kontrol: pozitif fiyat nedeniyle pratikte sürekli açık EXT1"
    job["gate_parameter_grid"].update({
        "vwap_enabled": [False],
        "ext1_source": ["close"],
        "external_gate_closes_position": [False],
        "g6_enabled": [False],
    })
    job["experiment"].update({
        "id": "AUTO-XMKT-S8-OPEN-GATE-CONTROL",
        "stage": "S8_GATE_VALUE_CONTROL",
        "variant": "ALWAYS_OPEN_EXT_CONTROL",
        "risk": "Raw close is not a production gate; this is an attribution control only.",
    })

    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        stored[:] = [row for row in stored if row.get("id") != job["id"]]
        stored.append(job)

    update_queue(LAB_ROOT / "jobs.json", add)
    print(job["id"])


if __name__ == "__main__":
    main()
