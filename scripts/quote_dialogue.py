#!/usr/bin/env python3
"""Blockquote reviewer prose so no line of it can be read as a verdict header field (#405).

Usage: quote_dialogue.py [FILE]   (stdin when FILE is omitted)

Lines are split with str.splitlines(), the splitter factory_core/verdict.py::parse_verdict
uses, so the "> " prefix lands after every boundary that reader recognises (LF, CRLF, lone
CR, VT, FF, 0x1c-0x1e, NEL, U+2028, U+2029), not only after LF. NUL bytes are dropped (bash
drops them from a variable today; one NUL makes GNU grep treat the whole artifact as
binary). Never prints an unquoted line and always exits 0: a missing or unreadable FILE
prints a quoted placeholder.
"""
import sys


def quote(data: bytes) -> bytes:
    text = data.decode("utf-8", "replace").replace("\x00", "")
    out = "".join("> " + line + "\n" for line in text.splitlines()) or "> (empty)\n"
    return out.encode("utf-8")


def main(argv: list) -> int:
    try:
        if len(argv) > 1:
            with open(argv[1], "rb") as fh:
                data = fh.read()
        else:
            data = sys.stdin.buffer.read()
        out = quote(data)
    except Exception:
        out = b"> (no reviewer output was recorded)\n"
    sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
