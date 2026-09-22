"""Queue two-period descriptive robustness checks for the S2 ablation set."""
from __future__ import annotations

import copy

from enqueue_dual_component_ablation import make_job
from lab_safety import update_queue
from quant_lab import LAB_ROOT


PERIODS = {
    "P1": ("2026-01-01", "2026-05-01"),
    "P2": ("2026-05-01", "2026-09-01"),
}
VARIANTS = {
    "BASE": (True, True),
    "NO-PMAX": (False, True),
    "NO-CORE": (True, False),
}


def main() -> None:
    jobs: list[dict] = []
    for period, (start, end) in PERIODS.items():
        for variant, (pmax, core) in VARIANTS.items():
            job = copy.deepcopy(make_job(variant, pmax=pmax, core=core))
            job["id"] = f"AUTO-BTC-S3-{period}-{variant}"
            job["start"] = start
            job["end"] = end
            job["description"] = f"BTC 30m dönem sağlamlığı {period} {start}..{end}: {variant}"
            job["experiment"].update({
                "id": "AUTO-BTC-S3-SEGMENTS",
                "stage": "S3_TIME_SEGMENT_DIAGNOSTIC",
                "period": period,
                "variant": variant,
                "risk": "Post-hoc dönem karşılaştırması; gerçek OOS değildir, Pine parity beklemede.",
            })
            jobs.append(job)

    def add(queue: dict) -> None:
        stored = queue.setdefault("jobs", [])
        ids = {job["id"] for job in jobs}
        stored[:] = [row for row in stored if row.get("id") not in ids]
        stored.extend(jobs)

    update_queue(LAB_ROOT / "jobs.json", add)
    print("\n".join(job["id"] for job in jobs))


if __name__ == "__main__":
    main()
