"""Every concrete provider method must accept the parameters of the ABC method it
overrides (same names, same order, defaults preserved), so the CLI can call any
provider through the ABC's signature. Guards the drift class in #488."""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core.providers.codehost.base import CodeHost
from factory_core.providers.codehost.github import GitHubCodeHost
from factory_core.providers.codehost.gitlab import GitLabCodeHost
from factory_core.providers.tracker.base import Tracker
from factory_core.providers.tracker.github import GitHubTracker
from factory_core.providers.tracker.jira import JiraTracker

PAIRS = [
    (Tracker, GitHubTracker), (Tracker, JiraTracker),
    (CodeHost, GitHubCodeHost), (CodeHost, GitLabCodeHost),
]


def _public_methods(abc):
    return [n for n, v in vars(abc).items()
            if not n.startswith("_") and callable(getattr(abc, n))]


@pytest.mark.parametrize("abc,impl", PAIRS, ids=lambda c: c.__name__)
def test_concrete_methods_accept_every_abc_parameter(abc, impl):
    problems = []
    for name in _public_methods(abc):
        abc_params = list(inspect.signature(getattr(abc, name)).parameters.values())
        impl_params = {p.name: p for p in inspect.signature(getattr(impl, name)).parameters.values()}
        impl_names = list(impl_params)
        positions = [impl_names.index(p.name) for p in abc_params if p.name in impl_params]
        for p in abc_params:
            if p.name not in impl_params:
                problems.append(f"{impl.__name__}.{name} lacks parameter {p.name!r}")
            elif p.default is not inspect.Parameter.empty and \
                    impl_params[p.name].default is inspect.Parameter.empty:
                problems.append(f"{impl.__name__}.{name}: {p.name!r} lost its default")
        if positions != sorted(positions):
            problems.append(f"{impl.__name__}.{name}: ABC parameter order changed")
        abc_names = {p.name for p in abc_params}
        for n, p in impl_params.items():
            if n not in abc_names and p.default is inspect.Parameter.empty \
                    and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                problems.append(f"{impl.__name__}.{name}: extra parameter {n!r} is required")
    assert not problems, "\n".join(problems)
