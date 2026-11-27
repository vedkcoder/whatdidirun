# recall: capture every interactive bash command (with context) into a JSONL spool.
# Add to ~/.bashrc (see `recall init bash`):   source "<path>/recall.bash"
#
# The hot path is pure bash (no subprocess beyond bash-preexec's own), so prompts stay fast.
# Commands typed with a leading space are not recorded (and are dropped from history).

[[ $- == *i* ]] || return 0          # interactive shells only
[[ -n ${__recall_loaded-} ]] && return 0
__recall_loaded=1

__recall_dir="${BASH_SOURCE[0]%/*}"
__recall_histcontrol_orig="${HISTCONTROL-}"   # bash-preexec strips ignorespace; remember the user's intent
# shellcheck source=/dev/null
[[ -n ${bash_preexec_imported-}${__bp_imported-} ]] || source "$__recall_dir/bash-preexec.sh"

__recall_data="${RECALL_DATA_DIR:-$HOME/.local/share/recall}"
__recall_spool="$__recall_data/spool.jsonl"
__recall_count=0
__recall_bin=$(command -v recall 2>/dev/null)
__recall_session="${HOSTNAME:-host}-$$-${EPOCHSECONDS:-0}"
[[ -d $__recall_data ]] || mkdir -p "$__recall_data"

__recall_json() {   # JSON-escape $1 into REPLY
    local s=$1
    s=${s//\\/\\\\}; s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}; s=${s//$'\t'/\\t}; s=${s//$'\r'/\\r}
    REPLY=$s
}

__recall_preexec() {
    __recall_cmd=$1
    __recall_start=${EPOCHREALTIME:-$(date +%s)}
    __recall_cwd=$PWD
}

__recall_precmd() {
    local ec=$?
    [[ -n ${__recall_cmd+x} ]] || return 0
    local end=${EPOCHREALTIME:-$(date +%s)} cmd=$__recall_cmd
    unset __recall_cmd
    if [[ $cmd == " "* ]]; then
        # Honour ignorespace/ignoreboth that bash-preexec disabled.
        [[ $__recall_histcontrol_orig == *ignorespace* || $__recall_histcontrol_orig == *ignoreboth* ]] \
            && builtin history -d -1 2>/dev/null
        return 0
    fi
    local c d
    __recall_json "$cmd"; c=$REPLY
    __recall_json "$__recall_cwd"; d=$REPLY
    printf '{"cmd":"%s","cwd":"%s","exit":%d,"start":"%s","end":"%s","host":"%s","session":"%s","shell":"bash"}\n' \
        "$c" "$d" "$ec" "${__recall_start/,/.}" "${end/,/.}" "${HOSTNAME:-}" "$__recall_session" >> "$__recall_spool"
    # Every N commands, index in the background (describe + embed) so long-lived shells stay searchable.
    if (( ++__recall_count % ${RECALL_INGEST_EVERY:-25} == 0 )) && [[ ${RECALL_AUTO_INGEST:-1} == 1 ]] && [[ -n ${__recall_bin-} ]]; then
        ( "$__recall_bin" ingest --quiet --no-wait >/dev/null 2>&1 & )
    fi
}

preexec_functions+=(__recall_preexec)
precmd_functions=(__recall_precmd "${precmd_functions[@]}")   # first, so $? is the user's exit code

# Ctrl-G: open the natural-language picker; the chosen command replaces the current line.
__recall_pick() {
    local out
    out=$(recall tui --query "$READLINE_LINE") || return
    [[ -n $out ]] && READLINE_LINE=$out && READLINE_POINT=${#out}
}
if command -v recall >/dev/null 2>&1; then
    bind -x "\"${RECALL_KEY:-\\C-g}\": __recall_pick"
    # Drain the spool + embed new commands in the background on shell start.
    [[ ${RECALL_AUTO_INGEST:-1} == 1 ]] && ( recall ingest --quiet --no-wait >/dev/null 2>&1 & )
fi
