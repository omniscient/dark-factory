"""Static checks for #438: entrypoint.sh's deconflict validate pre-check must not pre-empt run_hook.

A present-but-non-executable .factory/hooks/validate has to reach run_hook (which warns and runs
it via bash) instead of falling into MarketHawk's inline `npx tsc --noEmit`. Verified as text so
it runs off-image, in `python -m pytest tests/ -v`.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = (REPO_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
HOOKS = (REPO_ROOT / "scripts" / "hooks.sh").read_text(encoding="utf-8")


def test_validate_precheck_no_longer_requires_exec_bit():
    assert '[ -x "$CLONE_DIR/.factory/hooks/validate" ]' not in ENTRYPOINT


def test_validate_precheck_uses_non_empty_regular_file_test():
    assert (
        'if [ -f "$CLONE_DIR/.factory/hooks/validate" ] && '
        '[ -s "$CLONE_DIR/.factory/hooks/validate" ]; then'
    ) in ENTRYPOINT


def test_precheck_matches_run_hook_presence_test():
    # entrypoint.sh and run_hook must agree on what "a hook is present" means.
    assert 'if [ -f "$hook" ] && [ -s "$hook" ]; then' in HOOKS


def test_validate_precheck_still_routes_through_run_hook_gate():
    precheck = ENTRYPOINT.index('[ -s "$CLONE_DIR/.factory/hooks/validate" ]')
    assert ENTRYPOINT.index("run_hook --gate validate", precheck) > precheck
