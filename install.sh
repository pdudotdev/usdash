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

fail() {
    echo "usdash: $*" >&2
    exit 1
}

case "$(uname -s)" in
    Darwin | Linux) ;;
    *) fail "this installer is for macOS and Linux (try: pipx install usdash@git+https://github.com/pdudotdev/usdash)" ;;
esac

if ! command -v uv > /dev/null 2>&1; then
    echo "Installing uv..."
    # Downloaded first, then run: a failed download stops here and says so.
    installer=$(mktemp)
    trap 'rm -f "$installer"' EXIT
    if command -v curl > /dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh -o "$installer" || fail "couldn't download uv's installer (see above)"
    elif command -v wget > /dev/null 2>&1; then
        wget -qO "$installer" https://astral.sh/uv/install.sh || fail "couldn't download uv's installer (see above)"
    else
        fail "curl or wget is needed to install uv"
    fi
    sh "$installer" || fail "uv's installer failed (see above)"
    PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$HOME/.local/bin:$PATH"  # where uv's installer puts it
    export PATH
    command -v uv > /dev/null 2>&1 || fail "uv isn't on the PATH after installing it; install it from https://docs.astral.sh/uv/ and run this again"
fi

uv tool install --force --reinstall-package usdash "${USDASH_SOURCE:-usdash @ https://github.com/pdudotdev/usdash/archive/refs/heads/master.tar.gz}"
uv tool update-shell > /dev/null 2>&1 || true  # puts uv's tool folder on the PATH of new shells
echo "usdash installed: open a new terminal and run usdash"
