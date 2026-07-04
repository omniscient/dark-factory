"""Pytest configuration for dark-factory tests.

Provides a Windows-compatible `fcntl` stub so that factory_core modules that
import it (run_record.py) can be imported during local development on Windows.
The stub is a no-op — the real fcntl behaviour is only exercised inside the
Linux factory container.
"""
import sys
import types

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
