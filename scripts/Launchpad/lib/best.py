"""Best configurations from outputs/best/*.json as shell variables, for the
runbest scripts:

    eval "$(python3 lib/best.py outputs/best/stream.json "${OVERRIDES[@]}")"

prints BEST_<KEY>=<value> for every key of the top entry's configuration, with
KEY=VALUE overrides applied, plus BEST_OBJECTIVE and BEST_SOURCE (run group and
attempt, or the log the result came from, followed by the overrides). An
unknown key, or a file that cannot be read, prints a shell `exit 1` instead.

Scripts that derive more variables (hpl/runbest.sh) import load() and emit().
"""

from __future__ import annotations

import json
import shlex
import sys
from typing import Any


def fail(message: str) -> None:
    print(f"echo {shlex.quote(message)} >&2; exit 1")
    sys.exit()


def load(path: str, overrides: list[str], entries: str = "top", where: dict[str, Any] | None = None) -> dict[str, Any]:
    """The first entry of ``entries`` (matching ``where`` on configuration keys),
    flattened: configuration keys with overrides, OBJECTIVE, SOURCE."""
    try:
        rows = json.load(open(path))[entries]
    except (OSError, ValueError, KeyError) as e:
        fail(f"could not read {path}: {e}")
    rows = [r for r in rows if all(r["configuration"].get(k) == v for k, v in (where or {}).items())]
    if not rows:
        fail(f"no entry with {' '.join(f'{k}={v}' for k, v in where.items())} in {path}")
    top = rows[0]
    keys = {k.upper(): k for k in top["configuration"]}             # overrides match keys in any case
    over = {keys.get(k.upper(), k): v for k, v in (arg.split("=", 1) for arg in overrides)}
    unknown = sorted(set(over) - set(top["configuration"]))
    if unknown:
        fail(f"not a configuration key: {' '.join(unknown)} (keys: {' '.join(top['configuration'])})")
    source = f"{top['run_group']} attempt {top['attempt']}" if top.get("run_group") else top.get("log", path)
    return {**top["configuration"], **over, "OBJECTIVE": top["objective"],
            "SOURCE": source + "".join(f" {k}={v}" for k, v in over.items())}


def emit(values: dict[str, Any]) -> None:
    for key, value in values.items():
        print(f"BEST_{key.upper()}={shlex.quote(str(value))}")


if __name__ == "__main__":
    emit(load(sys.argv[1], sys.argv[2:]))
