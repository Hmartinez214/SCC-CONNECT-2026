#!/usr/bin/env python3
"""NVIDIA HPC Benchmarks (NGC) STREAM for the Launchpad SMAC3 autotuner (see
../autotune_local.py).

Each evaluation runs NVIDIA-STREAM (``stream-gpu-test.sh`` from the
nvcr.io/nvidia/hpc-benchmarks container, as hpl/ngc_hpl.sh does for HPL) on
one of the node's two H100 NVL GPUs. The x86 container has GPU STREAM only.
The objective is Triad bandwidth (MB/s). NVIDIA-STREAM prints no validation
line, so a run counts if the container exits 0 and all four kernels report.

The space: array size (0 = the container's default, which fills most of the
GPU's memory), element type, which GPU, and two GPU-wide settings, set per
job with sudo nvidia-smi and restored after: a locked SM clock and the board
power limit. Memory clock has a single supported value (2619 MHz), so it is
not a knob.

Usage (see ngc_stream_smac3.sh):

    python scripts/Launchpad/stream/ngc_stream_autotune.py --config scripts/Launchpad/stream/ngc_stream_smac3.yaml
    python scripts/Launchpad/stream/ngc_stream_autotune.py --config ... --check-replay
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from hpc_autotuner.applications.base import Application
from hpc_autotuner.core.parameter import Parameter
from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autotune_local  # noqa: E402

KERNELS = ("Copy", "Scale", "Add", "Triad")


class NGCStreamApplication(Application):
    """NVIDIA-STREAM from the NGC container, tuned on Triad MB/s."""

    objective_metric = "triad_mb_s"
    objective_direction = "maximize"

    #: name -> (choices, default).
    PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        # Elements per array (stream_test -n); 0 = the container's default
        # (1,308,622,848 for fp64). The footprint is 8 arrays' worth
        # (N x element size x 8), so 2^31 fits the 94 GB GPU in fp32 only.
        "ARRAY_SIZE": ([0, 16_777_216, 67_108_864, 268_435_456, 536_870_912,
                        1_073_741_824, 2_147_483_648], 0),
        "DTYPE": (["fp64", "fp32"], "fp64"),
        "DEVICE": ([0, 1], 0),
        # Locked SM clock (nvidia-smi -lgc); 0 = unlocked (boosts to 1785).
        "SM_CLOCK_MHZ": ([0, 1785, 1650, 1530, 1410, 1260, 1110, 990], 0),
        # Board power limit (nvidia-smi -pl), 200-400 W; 310 is the default.
        "POWER_LIMIT_W": ([400, 350, 310, 250, 200], 400),
    }

    #: GPU memory per device, for the footprint check.
    GPU_MEM_BYTES = 95_830 * 1024**2
    _ELEMENT_BYTES = {"fp64": 8, "fp32": 4}

    def __init__(self, image: str, gpus: str = "management.nvidia.com/gpu=all",
                 tunable: list[str] | None = None, fixed: dict[str, Any] | None = None) -> None:
        self.image = image
        self.gpus = gpus
        tunable = list(tunable or self.PARAMETERS)
        fixed = dict(fixed or {})
        unknown = [n for n in tunable + list(fixed) if n not in self.PARAMETERS]
        if unknown:
            raise ValueError(f"Unknown NGC STREAM parameter(s): {unknown}")
        self.parameters = []
        for name, (choices, default) in self.PARAMETERS.items():
            if name in tunable:
                self.parameters.append(Parameter(name, "categorical", choices=choices))
            else:
                self.parameters.append(Parameter(name, "categorical", choices=choices,
                                                 fixed_value=fixed.get(name, default)))

    def _fits(self, n: int, dtype: str) -> bool:
        return n * self._ELEMENT_BYTES[dtype] * 8 <= self.GPU_MEM_BYTES

    def resolve_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(configuration)
        for p in self.parameters:
            if p.fixed_value is not None:
                resolved.setdefault(p.name, p.fixed_value)
        n = int(resolved["ARRAY_SIZE"])
        assert self._fits(n, resolved["DTYPE"]), f"{n} {resolved['DTYPE']} elements x 8 arrays exceed GPU memory"
        return resolved

    def forbidden_clauses(self, cs: Any) -> list[Any]:
        """Array sizes that do not fit the GPU in that element type."""
        from ConfigSpace import ForbiddenAndConjunction, ForbiddenEqualsClause, ForbiddenInClause

        if not {"ARRAY_SIZE", "DTYPE"} <= set(cs.keys()):
            return []  # one is fixed; resolve_configuration checks the pair
        clauses = []
        for dtype in self.PARAMETERS["DTYPE"][0]:
            over = [n for n in self.PARAMETERS["ARRAY_SIZE"][0] if not self._fits(n, dtype)]
            if over:
                clauses.append(ForbiddenAndConjunction(ForbiddenEqualsClause(cs["DTYPE"], dtype),
                                                       ForbiddenInClause(cs["ARRAY_SIZE"], over)))
        return clauses

    def command(self, configuration: dict[str, Any]) -> str:
        c = configuration
        device = int(c["DEVICE"])
        args = ["/workspace/stream-gpu-test.sh", "--d", str(device)]
        if int(c["ARRAY_SIZE"]):
            args += ["--n", str(int(c["ARRAY_SIZE"]))]
        if c["DTYPE"] == "fp32":
            args += ["--dt", "fp32"]
        docker = ["docker", "run", "--rm", "--device", self.gpus, "--ipc=host", self.image] + args
        return "\n".join([
            *autotune_local.cleanup_header(),
            # The scheduler kills the job's session, which stops the docker
            # client but not the container; remove that by name.
            '_ngc="ngc_stream_${SLURM_JOB_ID:-local}"',
            '_ngc_rm() { docker rm -f "$_ngc" >/dev/null 2>&1 || true; }',
            "_cleanup+=(_ngc_rm)",
            *autotune_local.gpu_lines(device, int(c["SM_CLOCK_MHZ"]), int(c["POWER_LIMIT_W"])),
            shlex.join(docker[:3] + ["--name", "_NGC_"] + docker[3:]).replace("_NGC_", '"$_ngc"'),
        ])

    def parse_result(self, output: str) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for kernel in KERNELS:
            m = re.search(rf"(?m)^\s*{kernel}:\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)",
                          output)
            if m:
                metrics[f"{kernel.lower()}_mb_s"] = float(m.group(1))
        ok = all(f"{k.lower()}_mb_s" in metrics for k in KERNELS)
        return {"metrics": metrics, "objective": metrics.get("triad_mb_s") if ok else None, "success": ok}

    @classmethod
    def from_config(cls, config: ExperimentConfig) -> "NGCStreamApplication":
        image = os.path.expandvars(config.executable or "nvcr.io/nvidia/hpc-benchmarks:26.02.01")
        return cls(image=image, gpus=os.environ.get("GPUS", "management.nvidia.com/gpu=all"),
                   tunable=config.tunable, fixed=config.fixed)


common.register_application("stream_ngc", NGCStreamApplication.from_config)


if __name__ == "__main__":
    sys.exit(autotune_local.main(description="SMAC3 autotuning of NGC NVIDIA-STREAM on Launchpad"))
