"""Static guard (#485): entrypoint.sh must not call a `_`-prefixed shell function that
no shell file in the repo defines. An undefined call dies with "command not found" at
runtime, on a failure path that is rarely exercised (the deconflict branches did exactly
this for `_conflict_escalate`)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_DEF = re.compile(r"^\s*(?:function\s+)?(_[A-Za-z0-9_]+)\s*\(\)")
# A call is a `_name` in command position: line start, or after ; & | ( ! then do if while.
_CALL = re.compile(
    r"(?:^\s*|[;&|(]\s*|\b(?:if|then|elif|while|until|do)\s+!?\s*|!\s+)(_[a-z][A-Za-z0-9_]*)(?=\s|$|;|\))"
)


def _defined_functions():
    defs = set()
    for p in ROOT.rglob("*.sh"):
        if {".git", ".claude"} & set(p.relative_to(ROOT).parts):
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _DEF.match(line)
            if m:
                defs.add(m.group(1))
    return defs


def _called_functions():
    calls = {}
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8", errors="replace")
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in _CALL.finditer(line):
            calls.setdefault(m.group(1), n)
    return calls


def test_entrypoint_calls_only_defined_underscore_functions():
    defs = _defined_functions()
    undefined = {f: n for f, n in _called_functions().items() if f not in defs}
    assert not undefined, f"entrypoint.sh calls undefined shell functions (name: first line): {undefined}"
