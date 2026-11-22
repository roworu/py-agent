#!/bin/sh
# install the latest py-agent release: curl -fsSL https://raw.githubusercontent.com/roworu/py-agent/main/install.sh | sh
set -eu

repo="roworu/py-agent"
dir="${PY_AGENT_INSTALL_DIR:-$HOME/.local/bin}"
os="$(uname -s)"
arch="$(uname -m)"

# release asset per os/arch; only linux x86_64 is built for now
case "$os/$arch" in
    Linux/x86_64 | Linux/amd64) asset="py-agent" ;;
    *) echo "no py-agent binary for $os/$arch yet, run it from source instead" >&2; exit 1 ;;
esac

mkdir -p "$dir"
echo "downloading py-agent for $os/$arch to $dir"
# download next to the target, then move, so a failed download never leaves a broken binary
curl -fL "https://github.com/$repo/releases/latest/download/$asset" -o "$dir/.py-agent.new"
chmod +x "$dir/.py-agent.new"
mv "$dir/.py-agent.new" "$dir/py-agent"

case ":$PATH:" in
    *":$dir:"*) echo "installed, run: py-agent" ;;
    *) echo "installed to $dir/py-agent; add $dir to your PATH to run it as py-agent" ;;
esac
