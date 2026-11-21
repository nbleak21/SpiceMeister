#!/usr/bin/env bash
# Install SpiceMeister locally. Idempotent and safe to re-run.
#   - symlinks the `smeister` CLI and `spicemeister-opencode` launcher into ~/.local/bin
#   - symlinks the opencode config into SpiceMeister's own opencode home
#   - registers the `spicemeister` MCP server with Claude Code (if `claude` is present)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"

source "$REPO/scripts/sm-opencode-env.sh"

link() {   # link <src> <dst>
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
    echo "  ok     $dst"
    return 0
  fi
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    local bak="$dst.bak.$(date +%Y%m%d%H%M%S).$$"
    mv "$dst" "$bak"
    echo "  backup $dst -> $bak"
  fi
  ln -s "$src" "$dst"
  echo "  linked $dst"
}

echo "SpiceMeister install (repo: $REPO)"
chmod +x "$REPO/bin/smeister" "$REPO/scripts/sm-opencode.sh"

link "$REPO/bin/smeister"            "$BIN_DIR/smeister"
link "$REPO/scripts/sm-opencode.sh"  "$BIN_DIR/spicemeister-opencode"
link "$REPO/opencode/opencode.json"  "$XDG_CONFIG_HOME/opencode/opencode.json"

# Register the MCP server with Claude Code, if the CLI is available.
if command -v claude >/dev/null 2>&1; then
  if claude mcp list 2>/dev/null | grep -q '^spicemeister\b'; then
    echo "  ok     claude mcp: spicemeister already registered"
  else
    if claude mcp add --scope user spicemeister -- "$BIN_DIR/smeister" mcp >/dev/null 2>&1; then
      echo "  linked claude mcp: spicemeister"
    else
      echo "  note   could not auto-register MCP; run:"
      echo "         claude mcp add --scope user spicemeister -- $BIN_DIR/smeister mcp"
    fi
  fi
else
  echo "  note   'claude' CLI not found; to use the MCP server with Claude Code run:"
  echo "         claude mcp add --scope user spicemeister -- $BIN_DIR/smeister mcp"
fi

echo
echo "Done. Ensure $BIN_DIR is on PATH, then: smeister --help"
