# Perlmutter (NERSC) convenience. SOURCE this from an interactive shell:
#   cd nvccl && source scripts/pm.sh
# Loads modules, then sources env.sh.

module load cudatoolkit nccl 2>/dev/null \
  || echo "note: 'module load cudatoolkit nccl' failed - run 'module avail nccl' and adjust"

# Perlmutter has direct outbound network on both login and compute nodes, so we
# do NOT set an http(s)_proxy. If your site needs one, export it yourself before
# running scripts/setup.sh. A stale proxy var breaks git ("Could not resolve
# proxy") - clear it with:  unset http_proxy https_proxy
[ -n "${http_proxy:-}${https_proxy:-}" ] && echo "note: http(s)_proxy is set ($http_proxy $https_proxy) - 'unset http_proxy https_proxy' if git fails"

# let env.sh pick these up
export NCCL_HOME="${NCCL_HOME:-${NCCL_DIR:-${CRAY_NCCL_DIR:-}}}"

_pm_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
# shellcheck disable=SC1091
source "$_pm_dir/env.sh"
unset _pm_dir
