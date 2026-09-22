"""Run five predeclared WCT research configurations without touching user queue."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import quant_lab as lab
from lab_safety import atomic_write_json, engine_identity, file_digest, file_lock


CASES = [
    ("C01_BASE", "NONE", "OFF", "OFF"),
    ("C02_ER", "NONE", "v2", "OFF"),
    ("C03_ER_WR_HOLD", "NONE", "v2", "SH_WEIGHTED_HOLD"),
    ("C04_ER_WR_CLOSE", "NONE", "v2", "SH_WEIGHTED_CLOSE"),
    ("C05_ER_MOST", "MOST", "v2", "OFF"),
]


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = lab.LAB_ROOT / "reports" / "five_config_demo" / stamp
    output.mkdir(parents=True, exist_ok=False)
    config = lab.load_config(lab.LAB_ROOT / "config.json")
    config_snapshot = output / "config_snapshot.json"
    atomic_write_json(config_snapshot, config)
    rows = []
    with file_lock(lab.LAB_ROOT / "worker.lock", timeout=0):
        for case_id, exit_family, er, wr in CASES:
            case_output = output / case_id
            job = {
                "id": f"WCT-5CFG-{case_id}-{stamp}",
                "symbols": ["WCTUSDT"],
                "start": "2025-09-01",
                "end": "2026-09-01",
                "categorical": {
                    "exit_family": [exit_family],
                    "er": [er],
                    "winrate_state": [wr],
                },
                "max_runs": 1,
            }
            result = lab.run_job(config, job, lab.PROJECT_ROOT / "research" / "data" / "raw", case_output)
            if len(result) != 1:
                raise RuntimeError(f"{case_id}: exactly one result expected")
            rows.append({"case_id": case_id, **result[0]})
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "five_config_results.csv", index=False, encoding="utf-8-sig")
    manifest = {
        "status": "COMPLETE" if all(row.get("status") == "COMPLETE" for row in rows) else "COMPLETE_WITH_WARNINGS",
        "purpose": "Execution-chain demonstration and bounded research screen; not strategy validation",
        "engine": engine_identity(lab.LAB_ROOT),
        "config_sha256": file_digest(config_snapshot),
        "symbol": "WCTUSDT",
        "requested_start": "2025-09-01",
        "requested_end_exclusive": "2026-09-01",
        "cases": [case[0] for case in CASES],
        "results_file": str(output / "five_config_results.csv"),
        "limitations": [
            "RESEARCH_APPROXIMATION; not Pine or production equivalence.",
            "WCT REF-00 has unresolved high-detail trailing fill differences.",
            "This already-observed period is not out-of-sample.",
            "No parameter was selected after seeing these results.",
        ],
    }
    atomic_write_json(output / "manifest.json", manifest)
    print(json.dumps({"output": str(output), "status": manifest["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
