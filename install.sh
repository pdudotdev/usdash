#!/bin/sh
# Installs usdash on macOS or Linux:
#
#   curl -LsSf https://raw.githubusercontent.com/pdudotdev/usdash/master/install.sh | sh
#
# It installs uv (https://docs.astral.sh/uv/) if it's missing, then usdash as a uv
# tool: in an environment of its own, with a Python 3.11+ that uv fetches if the
# machine has none, and `usdash` on the PATH. Git isn't needed. Run it again to
# update usdash. USDASH_SOURCE installs from elsewhere (a folder, another URL).
set -eu

case "$(uname -s)" in
    Darwin | Linux) ;;
    *)
        echo "usdash: this installer is for macOS and Linux (try: pipx install usdash@git+https://github.com/pdudotdev/usdash)" >&2
        exit 1
        ;;
esac

if ! command -v uv > /dev/null 2>&1; then
    echo "Installing uv..."
    if command -v curl > /dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget > /dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "usdash: curl or wget is needed to install uv" >&2
        exit 1
    fi
    PATH="$HOME/.local/bin:$PATH"  # where uv's installer puts it
    export PATH
fi

uv tool install --force --reinstall-package usdash "${USDASH_SOURCE:-usdash @ https://github.com/pdudotdev/usdash/archive/refs/heads/master.tar.gz}"
uv tool update-shell > /dev/null 2>&1 || true  # puts uv's tool folder on the PATH of new shells
echo "usdash installed: open a new terminal and run usdash"
