#!/usr/bin/env bash

# Must be sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Error: this script must be sourced:"
    echo "  source $0"
    exit 1
fi

# From this point onward, NEVER use `exit`.
# Use `return` on errors.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCC_DIR="$SCRIPT_DIR"

echo "Setting up Spack"

export SPACK_ROOT="$SCC_DIR/libs/spack"

mkdir -p "$SCC_DIR/libs" || {
    echo "Error: cannot create $SCC_DIR/libs"
    return 1
}

if [[ ! -f "$SPACK_ROOT/share/spack/setup-env.sh" ]]; then
    echo "Installing Spack..."

    git clone https://github.com/spack/spack.git "$SPACK_ROOT" || {
        echo "Error: failed to clone Spack"
        return 1
    }
fi

source "$SPACK_ROOT/share/spack/setup-env.sh" || {
    echo "Error: failed to initialize Spack"
    return 1
}

command -v spack >/dev/null 2>&1 || {
    echo "Error: spack command unavailable"
    return 1
}

if [[ "$(spack config get config | awk '/install_tree:/{f=1;next} f && /root:/{print $2; exit}')" != "$SPACK_INSTALL_ROOT" ]]; then
    spack config add "config:install_tree:root:$SPACK_INSTALL_ROOT" || {
        echo "Error: failed to configure Spack install tree"
        return 1
    }
fi

echo "Spack: $(spack --version)"

if [[ ! -f "$SCC_DIR/spack.yaml" ]]; then
    echo "Creating SCC-CONNECT Spack environment..."

    spack env create --dir "$SCC_DIR" || {
        echo "Error: failed to create Spack environment"
        return 1
    }
fi

spack env activate "$SCC_DIR" || {
    echo "Error: failed to activate Spack environment"
    return 1
}

echo "Spack environment active."
