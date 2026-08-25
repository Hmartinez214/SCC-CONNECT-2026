'''
mfcsweep.py -- Sweep through MFC parameters
designed for perlmutter usage
8/25/2026
Holden Martinez
'''

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass
from pathlib import Path
import subprocess
import json

#config

MFC_ROOT = Path.home() / 'MFC'
SWEEP_ROOT = Path.home() / 'mfc-sweep'

COMPILERS = ['pm', 'pmcce', 'pmintel']
RANKS_PER_NODE = [128, 64, 32]
BINDINGS = {
    'cores':"--cpu-bind=cores",
    'threads':'--cpu-bind=threads',
}

NODES = 1
MEM_PER_RANK_GB = 2
WALLTIME = '00:30:00'
QOS = 'regular'

@dataclass(frozen=True)
class Config:
    compiler: str
    ranks: int 
    binding: str

    @property 
    def tag(self) -> str:
        #used for job names, dirs, and result rows
        return f'{self.compiler}_n{self.ranks}_{self.binding}'

    @property
    def srun_extra(self) -> str:
        return BINDINGS[self.binding]
    
    @property 
    def tree(self) -> Path:
        return SWEEP_ROOT / "trees" / self.compiler

def build_matrix(compilers, ranks, bindings) -> list[Config]:
    #cartesian product of sweep axes
    return[
        Config(compiler=c, ranks=r, binding=b)
        for c, r, b in itertools.product(compilers, ranks, bindings)
    ]

SBATCH_TEMPLATE='''\
#!/usr/bin/env bash
#SBATCH --job-name=mfcsweep_{compiler}
#SBATCH --account={account}
#SBATCH --constraint=cpu
#SBATCH --qos={qos}
#SBATCH --nodes={nodes}
#SBATCH --time={walltime}
#SBATCH --output={outdir}/slurm.out
#SBATCH --error={outdir}/slurm.err
set -euo pipefail

cd "{workdir}"

export MFC_LOAD_SLUG='{compiler}'
export MFC_SRUN_EXTRA='{srun_extra}'
export HDF5_USE_FILE_LOCKING=FALSE
ulimit -s unlimited

{{
    echo 'tag: {tag}'
    echo "host: $(hostname)"
    echo "date: $(date -Is)"
    echo "git: $(git -C '{tree}' rev-parse HEAD 2>/dev/null || echo unknown)"
    module list 2>&1 || true
    numactl -H 2>&1 || true
}} >{outdir}/environment.txt

./mfc.sh bench \\
    --mem {mem} \\
    -o '{outdir}/bench.yaml' \\
    -- -c {compiler} -n {ranks} --no-gpu
'''

def render(cfg: Config, account: str, stamp: str) -> str:
    #return sbatch script for a config
    outdir = SWEEP_ROOT / 'results' /stamp /cfg.tag
    return SBATCH_TEMPLATE.format(
        compiler=cfg.compiler,
        ranks=cfg.ranks,
        tag=cfg.tag,
        srun_extra=cfg.srun_extra,
        tree=cfg.tree,
        workdir=cfg.tree, #changes later
        outdir=outdir,
        account=account,
        qos=QOS,
        nodes=NODES,
        walltime=WALLTIME,
        mem=MEM_PER_RANK_GB
    )

def submit(script_text:str, script_path: Path, dry_run:bool) ->str | None:
    #verify dir exists
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_text)


    #dry run writes the script, does not submit. User can manually submit after inspecting if desired.
    if dry_run:
        print(f'[dry-run] wrote {script_path}')
        return None
    result = subprocess.run(
        ['sbatch', '--parsable', str(script_path)],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def prepare_workdir(cfg: Config, stamp: str, dry_run: bool) -> Path:
    workdir = SWEEP_ROOT / 'runs' / stamp / cfg.tag
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(CASE_FILE, workdir / 'case.py') #copies case file to new dir, output files will be in that
    return workdir

def write_manifest(rows: list[dict], path: Path) -> None:
    


def main() -> int:



    try:
        return args.func(args)
    except OSError as e:
        print(f'fatal: {e}', file=sys.stderr)
        return 1