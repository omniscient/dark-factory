"""Shared CodeHost contract-test suite (design doc §10) — the reusable base every
CodeHost implementation parametrizes into, alongside its own golden-argv/parity
suite. This suite proves the ABC itself is host-agnostic: every assertion here
must hold for every implementation's own opaque id shape without any per-host
branching in the test body — a hidden GitHub-shaped assumption in a method
signature would fail here even though it passes GitHubCodeHost's own parity
suite. Structural/opaque-ID contract only (no VCR/live-HTTP fixtures — each
implementation's own parity/unit-test file covers real I/O).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core.providers.codehost.base import CodeHost
from factory_core.providers.codehost.github import GitHubCodeHost

# name -> (implementation class, an opaque id shaped like that host's real ids,
#          whether its HTTP-backed methods are unimplemented stubs)
IMPLEMENTATIONS = {
    "github": (GitHubCodeHost, "42", False),
}

HTTP_BACKED_ARGS = {
    "find_change_for": ("feat/issue-1-x",),
    "open_change": (None, None, "title", "body"),
    "update_change_body": ("{id}", "body"),
    "mark_ready": ("{id}",),
    "merge_change": ("{id}",),
    "get_change_checks": ("{id}",),
    "get_change_mergeable": ("{id}",),
    "get_change_reviews": ("{id}",),
    "get_change_inline_comments": ("{id}",),
}


@pytest.fixture(params=sorted(IMPLEMENTATIONS), ids=sorted(IMPLEMENTATIONS))
def impl(request, monkeypatch):
    name = request.param
    cls, change_id, is_stub = IMPLEMENTATIONS[name]
    if name == "github":
        monkeypatch.setenv("GH_TOKEN", "x")
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
        )
    return cls, change_id, is_stub


def test_implementation_is_instantiable_codehost(impl):
    # ABC instantiation itself raises TypeError if any of base.py's 11 abstract
    # methods is left unimplemented — the strongest available "declares the
    # full contract" assertion.
    cls, _change_id, _is_stub = impl
    assert issubclass(cls, CodeHost)
    cls()


def test_remote_url_is_a_string(impl):
    cls, _change_id, _is_stub = impl
    assert isinstance(cls().remote_url(), str)


def test_close_keyword_is_a_string_never_none(impl):
    cls, _change_id, _is_stub = impl
    result = cls().close_keyword("99")
    assert isinstance(result, str)


@pytest.mark.parametrize("method_name", sorted(HTTP_BACKED_ARGS))
def test_http_backed_method_accepts_hosts_own_opaque_id_shape(impl, method_name):
    cls, change_id, is_stub = impl
    args = tuple(
        a.format(id=change_id) if isinstance(a, str) else a
        for a in HTTP_BACKED_ARGS[method_name]
    )
    if is_stub:
        with pytest.raises(NotImplementedError):
            getattr(cls(), method_name)(*args)
        return
    getattr(cls(), method_name)(*args)  # must not raise given a mocked subprocess boundary
