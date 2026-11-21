# SpiceMeister tool paths. Source this before install / MCP registration so the
# locations of the external tools SpiceMeister shells out to are known.
# (Sourced, not executed: no `set` flags, so it never kills the parent shell.)

export SPICEMEISTER_HOME="${SPICEMEISTER_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)}"

# ngspice is the simulation backend used by `smeister validate`.
export NGSPICE_BIN="${NGSPICE_BIN:-$(command -v ngspice || echo /opt/homebrew/bin/ngspice)}"

# REA (optional) powers `smeister scan --rea` for binary model containers.
export REA_BIN="${REA_BIN:-$(command -v rea || true)}"
