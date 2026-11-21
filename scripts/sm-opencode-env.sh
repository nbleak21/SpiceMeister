# Give SpiceMeister's opencode front-end its own private home, so it never
# shares config / sessions / background service with REA or pcb-stack opencode.
# (Sourced, not executed.)

export SPICEMEISTER_OPENCODE_HOME="${SPICEMEISTER_OPENCODE_HOME:-$HOME/.local/share/spicemeister/opencode-home}"
export XDG_CONFIG_HOME="$SPICEMEISTER_OPENCODE_HOME/config"
export XDG_DATA_HOME="$SPICEMEISTER_OPENCODE_HOME/data"
export XDG_STATE_HOME="$SPICEMEISTER_OPENCODE_HOME/state"
export XDG_CACHE_HOME="$SPICEMEISTER_OPENCODE_HOME/cache"
export OPENCODE_DISABLE_MODELS_FETCH=1
