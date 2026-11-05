#!/usr/bin/env python3
"""Validate science-bridge finding files (format science-finding/v0.1).

Standard library only, Python 3.8+. Used on both sides of the handoff:
  - Claude Science runs it on a finding before saving it to science/inbox/.
  - The Claude Code side (bridge.py) imports validate_finding() before routing.

Usage:
    python3 check_finding.py <finding.md> [<finding.md> ...]

Prints one JSON object per file ({"file", "ok", "errors", "meta"}) as a JSON list.
Exit code: 0 if every file is valid, 1 otherwise, 2 on usage error.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Union

FORMAT_VERSION = "science-finding/v0.1"
ALLOWED_TYPES = ("finding", "literature", "analysis-proposal", "rule-check", "question")
ALLOWED_CONFIDENCE = ("high", "medium", "low")
REQUIRED_KEYS = ("format", "title", "type", "created", "author", "confidence", "sources", "contains_case_data")
REQUIRED_HEADINGS = ("## Summary", "## Details", "## Suggested action")
FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BYTES = 200 * 1024
MAX_TITLE = 120

Value = Union[str, bool, List[str]]


def _strip_comment(value: str) -> str:
    """Drop a trailing ' # comment' (only when '#' follows whitespace, so URLs with '#' survive)."""
    m = re.search(r"\s#", value)
    return value[: m.start()].rstrip() if m else value


def _scalar(raw: str) -> Value:
    v = _strip_comment(raw.strip())
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()] if inner else []
    return v


def parse_frontmatter(text: str) -> Tuple[Dict[str, Value], str, List[str]]:
    """Parse the YAML subset the format uses: `key: value`, `key: [a, b]`, and block lists.

    Returns (meta, body, errors).
    """
    errors: List[str] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, ["file must start with a '---' frontmatter line"]
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text, ["frontmatter is not closed with a second '---' line"]

    meta: Dict[str, Value] = {}
    current_list_key = None
    for lineno, line in enumerate(lines[1:end], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.*)$", line)
        if item:
            if current_list_key is None:
                errors.append("line %d: list item without a key" % lineno)
                continue
            meta[current_list_key].append(_strip_comment(item.group(1)).strip().strip("'\""))  # type: ignore[union-attr]
            continue
        kv = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if not kv:
            errors.append("line %d: cannot parse %r" % (lineno, line.strip()))
            current_list_key = None
            continue
        key, raw = kv.group(1), kv.group(2)
        if key in meta:
            errors.append("line %d: duplicate key %r" % (lineno, key))
        if raw.strip() == "":
            meta[key] = []
            current_list_key = key
        else:
            meta[key] = _scalar(raw)
            current_list_key = None
    body = "\n".join(lines[end + 1:])
    return meta, body, errors


def validate_finding(path: Union[str, Path]) -> Dict[str, object]:
    p = Path(path)
    errors: List[str] = []
    result: Dict[str, object] = {"file": str(p), "ok": False, "errors": errors, "meta": {}}

    if not p.is_file():
        errors.append("file not found")
        return result
    if not FILENAME_RE.match(p.name):
        errors.append("filename must look like YYYY-MM-DD-some-slug.md (lowercase, digits, hyphens)")
    size = p.stat().st_size
    if size > MAX_BYTES:
        errors.append("file is %d bytes; limit is %d" % (size, MAX_BYTES))
        return result
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append("file is not valid UTF-8")
        return result

    meta, body, parse_errors = parse_frontmatter(text)
    errors.extend(parse_errors)
    result["meta"] = {k: v for k, v in meta.items() if k in ("title", "type", "created", "confidence", "affects")}

    for key in REQUIRED_KEYS:
        if key not in meta:
            errors.append("missing required key %r" % key)

    if "format" in meta and meta["format"] != FORMAT_VERSION:
        errors.append("format must be %r (got %r)" % (FORMAT_VERSION, meta["format"]))
    title = meta.get("title")
    if "title" in meta and (not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE):
        errors.append("title must be a non-empty single line of at most %d characters" % MAX_TITLE)
    if "type" in meta and meta["type"] not in ALLOWED_TYPES:
        errors.append("type must be one of %s" % ", ".join(ALLOWED_TYPES))
    if "created" in meta and not (isinstance(meta["created"], str) and DATE_RE.match(meta["created"])):
        errors.append("created must be YYYY-MM-DD")
    if "author" in meta and not (isinstance(meta["author"], str) and meta["author"].strip()):
        errors.append("author must be a non-empty string")
    if "confidence" in meta and meta["confidence"] not in ALLOWED_CONFIDENCE:
        errors.append("confidence must be one of %s" % ", ".join(ALLOWED_CONFIDENCE))
    if "sources" in meta:
        sources = meta["sources"]
        if not isinstance(sources, list) or not [s for s in sources if s]:
            errors.append("sources must be a non-empty list")
    if "contains_case_data" in meta and meta["contains_case_data"] is not False:
        errors.append("contains_case_data must be false; findings must never include patient-level data")
    if "affects" in meta and not isinstance(meta["affects"], list):
        errors.append("affects must be a list, e.g. [LBL-01, LBL-06]")

    positions = []
    for heading in REQUIRED_HEADINGS:
        m = re.search(r"^%s\s*$" % re.escape(heading), body, flags=re.MULTILINE)
        if not m:
            errors.append("missing body heading %r" % heading)
        else:
            positions.append(m.start())
    if len(positions) == len(REQUIRED_HEADINGS) and positions != sorted(positions):
        errors.append("body headings must appear in order: %s" % ", ".join(REQUIRED_HEADINGS))

    result["ok"] = not errors
    return result


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    results = [validate_finding(a) for a in argv]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
