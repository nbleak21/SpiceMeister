#!/usr/bin/env bash
# The `spicemeister-opencode` command: opencode with SpiceMeister's own home and
# the `spicemeister` MCP server wired in. Always --standalone (no lingering service).
set -euo pipefail

source "$(dirname "$(readlink -f "$0")")/sm-opencode-env.sh"
export PWD="$(pwd)"   # opencode picks its project dir from $PWD
exec opencode "$@" --standalone
