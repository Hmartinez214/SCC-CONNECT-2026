<%doc>
MFC job template for the Launchpad node, passed by runmfc.sh as
`./mfc.sh run ... -c scripts/Launchpad/mfc/launchpad.mako`.

Like MFC's default template, but always launches with mpirun (HPC-X Open MPI
from the NVIDIA HPC SDK) with Open MPI's own binding off, and runs every rank
through launchpad_bind.sh, which pins it according to $MFC_BIND.
$MFC_MPIRUN_ARGS (word-split) is appended to the mpirun options.

Bash variables are written without braces here: ${...} is Mako syntax.
</%doc>
#!/usr/bin/env bash

<%namespace name="helpers" file="helpers.mako"/>

${helpers.template_prologue()}

% if mpi:
    ok ":) mpirun: $MAGENTA$(command -v mpirun)$COLOR_RESET, bind: $MAGENTA$MFC_BIND$COLOR_RESET"
% endif

% for target in targets:
    ${helpers.run_prologue(target)}

    % if not mpi:
        (set -x; ${profiler} "${target.get_install_binpath(case)}")
    % else:
        (set -x; ${profiler}                                    \
            mpirun -np ${nodes*tasks_per_node} --bind-to none   \
                   $MFC_MPIRUN_ARGS                             \
                   "$MFC_LAUNCHPAD_BIND"                        \
                   "${target.get_install_binpath(case)}")
    % endif

    ${helpers.run_epilogue(target)}

    echo
% endfor

${helpers.template_epilogue()}
