#!/usr/bin/env python3
"""
Wrap numbered problems in LaTeX \begin{problem}{N} ... \end{problem} blocks.

Reads text from stdin, writes the transformed text to stdout. Any line that
starts with "<number>." begins a new problem; everything up to the next such
line (or end of input) becomes that problem's body.

Usage:
    python3 latexify_problems.py < input.txt > output.txt
    pbpaste | python3 latexify_problems.py | pbcopy      # via clipboard

Designed for messy OCR output: it strips surrounding triple quotes, trailing
Markdown hard-break spaces, and collapses runs of blank lines in each body.
"""

import re
import sys


PROBLEM_START = re.compile(r"^\s*(\d+)\.[ \t]*(.*)$")


def _clean_body(lines):
    """Trim trailing whitespace, drop leading/trailing blank lines, and
    collapse internal runs of blank lines down to a single blank line."""
    cleaned = [ln.rstrip() for ln in lines]

    # Drop leading and trailing blank lines.
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    # Collapse consecutive blank lines into one.
    result = []
    prev_blank = False
    for ln in cleaned:
        is_blank = not ln.strip()
        if is_blank and prev_blank:
            continue
        result.append(ln)
        prev_blank = is_blank
    return result


def latexify(text: str) -> str:
    # Strip a surrounding pair of triple quotes if present (OCR/paste cruft).
    stripped = text.strip()
    if stripped.startswith('"""') and stripped.endswith('"""'):
        text = stripped[3:-3]

    lines = text.splitlines()

    blocks = []          # list of (number, [body_lines])
    current_num = None
    current_body = []

    for line in lines:
        m = PROBLEM_START.match(line)
        if m:
            # Flush the previous problem before starting a new one.
            if current_num is not None:
                blocks.append((current_num, current_body))
            current_num = m.group(1)
            first = m.group(2)
            current_body = [first] if first.strip() else []
        elif current_num is not None:
            current_body.append(line)
        # Lines before the first numbered item are ignored.

    if current_num is not None:
        blocks.append((current_num, current_body))

    out = []
    for num, body in blocks:
        body = _clean_body(body)
        out.append(f"\\begin{{problem}}{{{num}}}")
        out.extend(body)
        out.append("\\end{problem}")
        out.append("")  # blank line between problems

    # Drop the trailing blank line.
    while out and not out[-1].strip():
        out.pop()

    return "\n".join(out) + "\n"


def main():
    text = sys.stdin.read()
    sys.stdout.write(latexify(text))


if __name__ == "__main__":
    main()
