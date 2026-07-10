"""Pytest configuration for dark-factory tests.

Provides a Windows-compatible `fcntl` stub so that factory_core modules that
import it (run_record.py) can be imported during local development on Windows.
The stub is a no-op — the real fcntl behaviour is only exercised inside the
Linux factory container.

Also scrubs inherited FACTORY_* identity env (see below) so the suite is
hermetic no matter which instance dispatches it.
"""
import os
import sys
import types

# --- Hermetic identity: strip inherited FACTORY_* env before any test imports ---
# The self-target smoke gate runs `pytest tests/` INSIDE a dispatched run
# container, which carries the instance's identity env (FACTORY_PROJECT_NUMBER,
# FACTORY_PRODUCT_NAME, FACTORY_REPO, …, from deploy/instance-self.env). Tests
# that assert on the built-in MarketHawk defaults (test_factory_core_identity,
# test_comment_digest) then fail — but ONLY in a run container, never in CI or a
# clean checkout — which false-latches the main-red gate and blocks all implement
# dispatch. Modules under test snapshot these vars at IMPORT (identity.py's
# module-level constants; comment_digest._BOT_RE), so the scrub MUST run here at
# conftest import — before pytest imports the test modules — not in an autouse
# fixture (too late; the constants are already bound). Tests that need a specific
# identity set it explicitly via monkeypatch (e.g. test_env_overrides).
for _k in [k for k in os.environ if k.startswith("FACTORY_")]:
    del os.environ[_k]

if sys.platform == "win32" and "fcntl" not in sys.modules:
    stub = types.ModuleType("fcntl")

    def _noop(*args, **kwargs):
        pass

    stub.flock = _noop
    stub.fcntl = _noop
    stub.lockf = _noop
    stub.LOCK_EX = 2
    stub.LOCK_NB = 4
    stub.LOCK_SH = 1
    stub.LOCK_UN = 8
    sys.modules["fcntl"] = stub
