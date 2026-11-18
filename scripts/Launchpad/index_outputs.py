#!/usr/bin/env python3
"""One table of every benchmark run on Launchpad, and the files of any one run.

    python3 scripts/Launchpad/index_outputs.py                  # write outputs/index.csv
    python3 scripts/Launchpad/index_outputs.py --find stream_smac3_v5 3716
    python3 scripts/Launchpad/index_outputs.py --find smac3_intel_hpl_fast 20261026073227

outputs/index.csv has one row per finished tuner run (every run group under
outputs/<bench>/autotuning/; runs a group imported from an earlier group are
marked ``imported``) and per runbest run (outputs/<bench>/runbest/results.tsv):
bench, kind, run_group, attempt, job_id, status, metric, value, finished_at,
imported, log, job_script, configuration (JSON). Paths are relative to the
repo. The endless loops keep their own tables (outputs/stream/cont/results.tsv,
outputs/hpl/bign/results.tsv).

--find prints where a tuner run's files are: its record, application log, job
script and the script's stdout/stderr (by attempt or job id).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from paths import BENCHES, OUTPUTS, REPO, bench_of, log_dir, run_groups, slurm_dir, tuning_dir
from plot_style import is_imported, load_runs

COLUMNS = ["bench", "kind", "run_group", "attempt", "job_id", "status", "metric", "value", "finished_at",
           "imported", "log", "job_script", "configuration"]


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO)) if path.exists() else ""


def _run_files(group: str, job_id: str) -> dict[str, Path]:
    bench = bench_of(group)
    return {"log": log_dir(group) / f"{group}_{job_id}.log", "job_script": slurm_dir(bench) / f"job_{job_id}.sh",
            "stdout": slurm_dir(bench) / f"job_{job_id}.out", "stderr": slurm_dir(bench) / f"job_{job_id}.err"}


def tuner_rows() -> list[dict]:
    rows = []
    for group in run_groups():
        try:
            metric = json.loads((tuning_dir(group) / "experiment.json").read_text())["objective"]["metric"]
        except (OSError, ValueError, KeyError):
            metric = ""
        for rec in load_runs(group):
            job = str(rec.get("job_id") or "")
            files = _run_files(group, job) if job else {}
            rows.append({"bench": bench_of(group), "kind": "tuner", "run_group": group, "attempt": rec.get("attempt"),
                         "job_id": job, "status": rec.get("status"), "metric": metric, "value": rec.get("objective"),
                         "finished_at": rec.get("finished_at") or "", "imported": int(is_imported(rec)),
                         "log": _rel(files["log"]) if files else "", "job_script": _rel(files["job_script"]) if files else "",
                         "configuration": json.dumps(rec.get("configuration"), sort_keys=True)})
    return rows


def runbest_rows() -> list[dict]:
    rows = []
    for bench in BENCHES:
        tsv = OUTPUTS / bench / "runbest" / "results.tsv"
        if not tsv.exists():
            continue
        for r in csv.DictReader(tsv.open(), delimiter="\t"):
            rows.append({"bench": bench, "kind": "runbest", "run_group": r["source"], "attempt": "", "job_id": "",
                         "status": "COMPLETED" if r["valid"] == "True" else "FAILED", "metric": r["metric"],
                         "value": r["value"], "finished_at": r["time"], "imported": 0, "log": r["run"],
                         "job_script": r["script"], "configuration": json.dumps({"overrides": r["overrides"]})})
    return rows


def find(group: str, key: str) -> int:
    records = load_runs(group)
    hits = [r for r in records if str(r.get("attempt")) == key or str(r.get("job_id")) == key]
    if not hits:
        print(f"no attempt or job id {key} in {group} ({len(records)} finished runs)")
        return 1
    for rec in hits:
        print(f"{group} attempt {rec['attempt']}, job {rec.get('job_id')}: {rec.get('status')}, objective {rec.get('objective')}")
        print(f"  record      {tuning_dir(group).relative_to(REPO)}/evaluations.jsonl (attempt {rec['attempt']})")
        for name, path in _run_files(group, str(rec.get("job_id"))).items():
            print(f"  {name:<11} {path.relative_to(REPO)}{'' if path.exists() else '  (missing)'}")
        print(f"  config      {json.dumps(rec.get('configuration'), sort_keys=True)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--find", nargs=2, metavar=("RUN_GROUP", "ATTEMPT_OR_JOB_ID"))
    p.add_argument("--out", default=str(OUTPUTS / "index.csv"))
    a = p.parse_args()
    if a.find:
        return find(*a.find)
    rows = tuner_rows() + runbest_rows()
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} runs -> {Path(a.out).relative_to(REPO) if Path(a.out).is_relative_to(REPO) else a.out}")
    for (bench, kind), n in sorted(Counter((r["bench"], r["kind"]) for r in rows).items()):
        print(f"  {bench:<7} {kind:<8} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
