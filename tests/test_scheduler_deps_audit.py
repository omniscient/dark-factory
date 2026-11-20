"""Dry-run tests for scheduler.sh::deps_audit_check (#484).

Sources scheduler.sh with SCHEDULER_SOURCE_ONLY=1 exactly like
tests/test_scheduler_comment_verdict.py (same python3 override for the source-time
providers preflight, same SCHEDULER_STATE_DIR redirect), then replaces `docker`
(running-container dedupe) and `dispatch` (the real one runs `docker compose run`)
with shell stubs so each call is a pure dry run.
"""
import os
import re
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = REPO_ROOT / "scheduler.sh"

_HARNESS = '''
set -uo pipefail
python3() {
  case "$*" in
    *providers/cli.py*find-change*) echo "${FAKE_OPEN_PR:-}"; return 0 ;;
    *providers/cli.py*) return 0 ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
SCHEDULER_SOURCE_ONLY=1 source "$1"
docker() { echo "${FAKE_DOCKER_PS:-}"; }
dispatch() { echo "DISPATCH:$1"; return 0; }
DISPATCHED=""
deps_audit_check
echo "DISPATCHED=${DISPATCHED}"
'''


def _run(state_dir: Path, **env_over) -> str:
    env = dict(os.environ)
    env["SCHEDULER_STATE_DIR"] = str(state_dir)
    env["STATE_FILE"] = str(state_dir / "scheduler-state.json")
    env["FACTORY_RUN_PREFIX"] = "t484-run-"
    env.pop("DEPS_AUDIT_ENABLED", None)
    env.pop("DEPS_AUDIT_INTERVAL_HOURS", None)
    env.update(env_over)
    r = subprocess.run(["bash", "-c", _HARNESS, "_", str(SCHEDULER)],
                       cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    return r.stdout


def _stamp(state_dir: Path) -> Path:
    return state_dir / "deps-audit-last-run"


def test_disabled_by_default_never_dispatches(tmp_path):
    out = _run(tmp_path)
    assert "DISPATCH:" not in out and "deps_audit=" not in out
    assert not _stamp(tmp_path).exists()


def test_dispatches_once_per_interval(tmp_path):
    first = _run(tmp_path, DEPS_AUDIT_ENABLED="true")
    assert "DISPATCH:Audit deps" in first
    assert re.search(r"deps_audit=dispatched interval=24h", first)
    assert "DISPATCHED=Audit deps" in first
    assert _stamp(tmp_path).exists()
    second = _run(tmp_path, DEPS_AUDIT_ENABLED="true")    # stamp is fresh → throttled
    assert "DISPATCH:" not in second


def test_dispatches_again_after_interval_elapses(tmp_path):
    _stamp(tmp_path).touch()
    old = time.time() - 25 * 3600
    os.utime(_stamp(tmp_path), (old, old))
    assert "DISPATCH:Audit deps" in _run(tmp_path, DEPS_AUDIT_ENABLED="true")


def test_interval_env_override(tmp_path):
    _stamp(tmp_path).touch()
    old = time.time() - 2 * 3600
    os.utime(_stamp(tmp_path), (old, old))
    assert "DISPATCH:" not in _run(tmp_path, DEPS_AUDIT_ENABLED="true")
    out = _run(tmp_path, DEPS_AUDIT_ENABLED="true", DEPS_AUDIT_INTERVAL_HOURS="1")
    assert "DISPATCH:Audit deps" in out and "interval=1h" in out


def test_skip_open_deps_pr(tmp_path):
    out = _run(tmp_path, DEPS_AUDIT_ENABLED="true", FAKE_OPEN_PR="911")
    assert "deps_audit=skip reason=open_deps_pr" in out
    assert "DISPATCH:" not in out
    assert not _stamp(tmp_path).exists()      # not stamped → re-checked next cycle


def test_skip_while_audit_container_running(tmp_path):
    out = _run(tmp_path, DEPS_AUDIT_ENABLED="true",
               FAKE_DOCKER_PS='t484-run-ab12 "/entrypoint.sh \'Audit deps\'"')
    assert "DISPATCH:" not in out


def test_other_instance_audit_container_does_not_block(tmp_path):
    # `docker ps` is host-wide and two instances share the host: only THIS instance's run counts
    out = _run(tmp_path, DEPS_AUDIT_ENABLED="true",
               FAKE_DOCKER_PS='markethawk-dark-factory-run-9 "/entrypoint.sh \'Audit deps\'"')
    assert "DISPATCH:Audit deps" in out


def test_invalid_interval_falls_back_to_24h(tmp_path):
    # non-numeric would abort the scheduler under `set -u`; 0 would dispatch on every poll
    _stamp(tmp_path).touch()
    for bad in ("abc", "0.5", "0"):
        out = _run(tmp_path, DEPS_AUDIT_ENABLED="true", DEPS_AUDIT_INTERVAL_HOURS=bad)
        assert "DISPATCH:" not in out and "DISPATCHED=" in out, bad


def test_call_site_outside_main_red_block_and_pause_guarded():
    src = SCHEDULER.read_text()
    block = src[src.index("# --- Read main-is-red sentinel"):]
    block = block[:block.index("\n  fi\n", block.index("main_red_fixer_check")) + 6]
    assert "deps_audit_check" not in block          # independent of MAIN_IS_RED
    call = src[src.index(block) + len(block):]
    call = call[:call.index("dispatch_stage \"$_stage\"")]
    assert re.search(r'SESSION_WINDOW_PAUSED" = "false" \] && \[ -z "\$DISPATCHED" \]', call)
    assert "deps_audit_check" in call
