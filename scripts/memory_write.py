#!/usr/bin/env python3
"""Write-through memory adapter — flat-file only.

Ports write logic from gate_lib.sh::write_memory_entry() to Python,
adding normalized dedup and agent/scope tagging. It does not write index.jsonl
(see the note at the end of main()).

Usage:
    python3 memory_write.py \\
        --target      <path to .archon/memory/*.md>             \\
        --path-prefix <e.g. dark-factory/scripts/>              \\
        --text        <core lesson text (no tag prefix)>         \\
        --source      <conformance|code-review|refine|implement> \\
        --issue       <issue number>                            \\
        [--kind       <pattern|avoid|fix>]   (default: avoid)

Exit 0: write succeeded, or intentionally skipped (dedup/reinforcement). At the
30-entry cap the oldest authoritative entries are evicted to make room.
Exit 1: markdown I/O error.
Exit 2: invalid arguments (argparse), e.g. --kind outside pattern|avoid|fix;
        nothing is created or written.
"""
import argparse
import calendar
import re
import sys
from datetime import date
from pathlib import Path

_ENTRY_RE = re.compile(r"^\- \[(PATTERN|AVOID|FIX)\] ")


def _normalize(text):
    """Lowercase, collapse whitespace, strip trailing punctuation."""
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(".,;:!?")
    return text


def _scope_from_stem(stem):
    """Derive scope from markdown filename stem.

    backend-patterns  → backend
    dark-factory-ops  → dark-factory
    frontend-patterns → frontend
    architecture      → architecture
    codebase-patterns → codebase
    """
    for suffix in ("-patterns", "-ops"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _add_months(d, months):
    """Add months to a date, clamping to the last day of the resulting month."""
    month = d.month + months
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _extract_body(line):
    """Extract lesson text from a [PATTERN]/[AVOID]/[FIX] line (before <!-- comment).

    Splits on the LAST occurrence of ' <!--' so that body text containing '<!--'
    is not prematurely truncated.
    """
    m = re.match(r"^\- \[(?:PATTERN|AVOID|FIX)\] (.+)$", line)
    if not m:
        return ""
    body = m.group(1)
    # Split on the LAST '<!--' to avoid truncating body text that contains '<!--'
    idx = body.rfind("<!--")
    if idx != -1:
        body = body[:idx]
    return body.strip()


CAP = 30  # authoritative entries per memory file (R4)


def _entry_date(line):
    """date: tag of an entry line as YYYY-MM-DD; undated entries sort as oldest."""
    m = re.search(r"date:(\d{4}-\d{2}-\d{2})", line)
    return m.group(1) if m else "0000-00-00"


def _is_expired(line, today_str):
    m = re.search(r"expires:(\d{4}-\d{2}-\d{2})", line)
    return bool(m) and m.group(1) < today_str


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True, help="Path to .archon/memory/*.md")
    p.add_argument("--path-prefix", required=True, dest="path_prefix")
    p.add_argument("--text", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--issue", required=True, type=int)
    # Closed allowlist: the tag is derived from the validated choice only, so no
    # caller text reaches the tag position. Default avoid keeps callers that omit
    # the flag (gate_lib.sh::write_memory_entry) byte-identical to the old output.
    p.add_argument("--kind", choices=["pattern", "avoid", "fix"], default="avoid",
                   help="entry tag: pattern -> [PATTERN], avoid -> [AVOID], fix -> [FIX]")
    return p.parse_args()


def main():
    args = _parse_args()
    target = Path(args.target)
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    expires_str = _add_months(today, 6).strftime("%Y-%m-%d")
    scope = _scope_from_stem(target.stem)

    if not args.text.strip():
        print("memory-write: error: --text is empty", file=sys.stderr)
        sys.exit(1)

    # Create .archon/memory/ on a fresh target (#444). Placed after the empty-text
    # guard so an invalid-input run never creates the directory as a side effect.
    # exist_ok=False + catching FileExistsError (rather than checking .exists() first)
    # keeps the "created" log line accurate when two writers race to create the
    # same directory concurrently.
    created = True
    try:
        target.parent.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if target.parent.is_dir():
            created = False
        else:
            print(
                f"memory-write: error: cannot create {target.parent}: "
                f"path exists and is not a directory",
                file=sys.stderr,
            )
            sys.exit(1)
    except OSError as exc:
        print(f"memory-write: error: cannot create {target.parent}: {exc}", file=sys.stderr)
        sys.exit(1)
    if created:
        print(f"memory-write: created {target.parent.resolve()}")

    # Sanitize: collapse whitespace (removes embedded newlines) and strip HTML
    # comment delimiters (same as the prior bash pipeline: tr -d '\n\r' | sed 's/-->//g').
    # Both '<!--' and '-->' are stripped so prose containing either cannot break
    # the metadata comment appended to each entry.
    args.text = re.sub(r"\s+", " ", args.text).strip()
    args.text = args.text.replace("<!--", "").replace("-->", "")

    # Load existing content (treat missing file as empty)
    if target.is_dir():
        print(f"memory-write: error: --target is a directory, not a file: {target}", file=sys.stderr)
        sys.exit(1)
    try:
        raw = target.read_text(encoding="utf-8") if target.exists() else ""
    except OSError as exc:
        print(f"memory-write: error reading {target}: {exc}", file=sys.stderr)
        sys.exit(1)

    lines = raw.splitlines(keepends=True)

    # Step 1: Expiry cleanup — strip expired authoritative entries
    lines = [
        l for l in lines
        if not (_ENTRY_RE.match(l) and _is_expired(l, today_str))
    ]

    # Step 2: Normalized dedup check + reinforcement
    candidate_norm = _normalize(args.text)

    for i, line in enumerate(lines):
        if not _ENTRY_RE.match(line):
            continue
        if _normalize(_extract_body(line)) == candidate_norm:
            # REINFORCE: update date: and expires: in-place; one-line diff.
            # The dedup key is body-only (tag not included), so a write with a
            # different --kind than the stored entry still reinforces rather than
            # duplicating. Warn rather than silently retagging, so the mismatch is
            # visible without changing the stored tag out from under other readers.
            stored_tag = _ENTRY_RE.match(line).group(1)
            if stored_tag != args.kind.upper():
                print(
                    f"memory-write: warning: reinforced entry tagged [{stored_tag}] in "
                    f"{target} differs from requested --kind {args.kind} "
                    f"([{args.kind.upper()}]); tag left unchanged",
                    file=sys.stderr,
                )
            updated = re.sub(r"date:\d{4}-\d{2}-\d{2}", f"date:{today_str}", line)
            updated = re.sub(r"expires:\d{4}-\d{2}-\d{2}", f"expires:{expires_str}", updated)
            # Handle legacy/manually added entries that have no date:/expires: tags:
            # append a new metadata comment so reinforcement is always recorded.
            if "date:" not in updated:
                trailing_newline = "\n" if updated.endswith("\n") else ""
                updated = updated.rstrip("\n")
                updated += f" <!-- date:{today_str} expires:{expires_str} -->{trailing_newline}"
            lines[i] = updated
            try:
                target.write_text("".join(lines), encoding="utf-8")
            except OSError as exc:
                print(f"memory-write: error writing {target}: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"memory-write: reinforced existing entry in {target}")
            break
    else:
        # Step 3: Build the new entry; step 6 makes room if the file is at the cap.
        # Step 4: Build entry with full tag set per #645 contract.
        # Strip --> and newlines from metadata fields to prevent early comment closure.
        safe_source = args.source.replace("-->", "").replace("\n", "").replace("\r", "")
        safe_path = args.path_prefix.replace("-->", "").replace("\n", "").replace("\r", "")
        entry = (
            f"- [{args.kind.upper()}] {args.text} "
            f"<!-- issue:#{args.issue} date:{today_str} expires:{expires_str} "
            f"source:{safe_source} agent:{safe_source} scope:{scope} "
            f"path:{safe_path} -->"
        )

        # Step 5: Insert before --- delimiter, or append if absent
        delim_idx = next(
            (i for i, l in enumerate(lines) if l.rstrip("\n") == "---"),
            None,
        )
        if delim_idx is not None:
            lines.insert(delim_idx, entry + "\n")
        else:
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append(entry + "\n")

        # Step 6: Cap (R4, 30 authoritative [PATTERN]/[AVOID]/[FIX] entries per file).
        # Write the new lesson and evict the oldest authoritative entries by
        # `date:` (undated legacy entries count as oldest; ties go to the earliest
        # in the file) until the file is back at the cap. The new entry is never
        # the one evicted. [PROVISIONAL] and [INVALID] lines are not counted or
        # evicted. This matches the implement agent's R4 drop rule ("oldest by date").
        new_idx = next(i for i, l in enumerate(lines) if l.rstrip(chr(10)) == entry)
        evicted = 0
        while True:
            idxs = [i for i, l in enumerate(lines) if _ENTRY_RE.match(l) and i != new_idx]
            if len(idxs) + 1 <= CAP:
                break
            victim = min(idxs, key=lambda i: (_entry_date(lines[i]), i))
            del lines[victim]
            if victim < new_idx:
                new_idx -= 1
            evicted += 1
        if evicted:
            print(f"memory-write: cap reached in {target} - evicted {evicted} oldest entr"
                  f"{'y' if evicted == 1 else 'ies'} to make room")

        # Write markdown (source of truth)
        try:
            target.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            print(f"memory-write: error writing {target}: {exc}", file=sys.stderr)
            sys.exit(1)

    # No index.jsonl write (#486). The markdown file is the only store this script
    # writes. The index rows it used to append lacked the `id`/`source_file` fields
    # the retriever requires, so they were never read; and any row the retriever
    # *can* read would flip retrieval to the index path and hide every markdown-only
    # entry. Readable rows come only from `memory_import.py`.
    sys.exit(0)


if __name__ == "__main__":
    main()
