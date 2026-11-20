#!/usr/bin/env bash
# Shared reader for factory_core.effective_config's run-config.env (#180), sourced by
# scheduler.sh (read_config) and entrypoint.sh (post-clone). The file is parsed line by
# line and NEVER sourced: values are raw (unquoted), and sourcing would clobber operator
# env overrides and skip the override log.
#
# Line format: `VAR=value` (value = everything after the first "="), a bare `VAR` for a
# null/absent config value, `#` comments (`# source: <what was resolved>`).
#
# apply_run_config_env <file> <scheduler|entrypoint>
#   scheduler  - scheduler.sh's former _set_cfg: a variable already set, even to "", wins
#                and a differing value logs `[config] VAR=<env> (env override; config has
#                '<cfg>')`; otherwise the config value is exported (a null one as "").
#   entrypoint - entrypoint.sh's former _epcfg: a null config value is skipped; a set
#                variable (the bootstrap defaults set all five) is kept and a differing
#                value logs `[entrypoint-config] VAR=<val> (env/bootstrap override; config
#                has '<cfg>')`; an equal or unset one is exported.
# Returns 1 when <file> is missing or has no entries (callers pick fail-closed or
# fail-open), 2 on an unknown mode.
apply_run_config_env() {
  local file="$1" mode="$2" prefix line var val is_null n=0 src
  case "$mode" in
    scheduler) prefix="[config]" ;;
    entrypoint) prefix="[entrypoint-config]" ;;
    *) echo "apply_run_config_env: unknown mode '${mode}'" >&2; return 2 ;;
  esac
  [ -s "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    if [[ "$line" == *=* ]]; then
      var="${line%%=*}"; val="${line#*=}"; is_null=0
    else
      var="$line"; val=""; is_null=1
    fi
    if ! [[ "$var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "${prefix} skipping malformed run-config.env line: ${line}" >&2
      continue
    fi
    n=$((n + 1))
    if [ "$mode" = entrypoint ]; then
      [ "$is_null" = 1 ] && continue
      if [ -n "${!var+x}" ] && [ "${!var}" != "$val" ]; then
        echo "${prefix} ${var}=${!var} (env/bootstrap override; config has '${val}')" >&2
      else
        export "${var}=${val}"
      fi
    elif [ -n "${!var+x}" ]; then
      if [ "${!var}" != "$val" ]; then
        echo "${prefix} ${var}=${!var} (env override; config has '${val}')" >&2
      fi
    else
      export "${var}=${val}"
    fi
  done < "$file"
  [ "$n" -gt 0 ] || return 1
  src=$(sed -n 's/^# source: //p' "$file" | head -n 1)
  echo "${prefix} loaded from ${src:-$file}"
}
