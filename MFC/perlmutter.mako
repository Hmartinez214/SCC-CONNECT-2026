#!/usr/bin/env bash
<%!
from mako.exceptions import RuntimeException
%>
<%namespace name="helpers" file="helpers.mako"/>
<%
if engine == 'batch' and not account:
    raise RuntimeException("Perlmutter requires an account, e.g. -a m1234.")
%>
% if engine == 'batch':
#SBATCH --nodes=${nodes}
#SBATCH --ntasks-per-node=${tasks_per_node}
#SBATCH --cpus-per-task=1
#SBATCH --job-name="${name}"
#SBATCH --time=${walltime}
#SBATCH --account="${account}"
#SBATCH --qos=${quality_of_service or 'regular'}
% if gpu_enabled:
#SBATCH --constraint=gpu
#SBATCH --gpus-per-node=${tasks_per_node}
% else:
#SBATCH --constraint=cpu
% endif
#SBATCH --output="${name}.out"
#SBATCH --error="${name}.err"
#SBATCH --export=ALL
% if email:
#SBATCH --mail-user=${email}
#SBATCH --mail-type="BEGIN, END, FAIL"
% endif
% endif
${helpers.template_prologue()}
ok ":) Loading modules:\n"
cd "${MFC_ROOT_DIR}"
. ./mfc.sh load -c pm -m ${'g' if gpu_enabled else 'c'}
cd - > /dev/null
echo

# Lustre ($SCRATCH) can fail HDF5/Silo's file-locking check under multi-rank
# writes — see Hummingbird post_process debugging. Cheap to set proactively.
export HDF5_USE_FILE_LOCKING=FALSE

% for target in targets:
    ${helpers.run_prologue(target)}
    % if not mpi:
        (set -x; ${profiler} "${target.get_install_binpath(case)}")
    % else:
        (set -x; ${profiler}                                   \
            srun --ntasks ${nodes*tasks_per_node}                 \
                   "${target.get_install_binpath(case)}")
    % endif
    ${helpers.run_epilogue(target)}
    echo
% endfor
${helpers.template_epilogue()}
