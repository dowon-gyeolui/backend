"""Quick quality report for a knowledge JSONL file.

Usage
-----
    python scripts/validate_jsonl.py --input data/processed/적천수천미.jsonl

Reports (to stdout):
    total rows
    null counts for chapter / section / tags / content_korean
    shortest / longest / average content_original length
    number of chunks below a configurable length threshold
    top N most frequent (chapter, section) pairs
    top N most frequent tags

Exit code: 0 always (this is a report tool, not a gate).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_jsonl  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--input", required=True, help="Path to a JSONL file.")
    p.add_argument("--short-threshold", type=int, default=80,
                   help="Chunks shorter than this are counted as 'short' (default: 80).")
    p.add_argument("--top", type=int, default=10,
                   help="How many top entries to show for chapters / tags (default: 10).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found.", file=sys.stderr)
        return 0

    total = 0
    null_chapter = 0
    null_section = 0
    null_tags = 0
    null_korean = 0
    short_count = 0
    lens: list[int] = []
    chap_sec_counter: Counter[tuple[str, str]] = Counter()
    tag_counter: Counter[str] = Counter()

    for row in load_jsonl(input_path):
        total += 1
        if row.get("chapter") is None:
            null_chapter += 1
        if row.get("section") is None:
            null_section += 1
        if not row.get("tags"):
            null_tags += 1
        if row.get("content_korean") is None:
            null_korean += 1

        content = row.get("content_original") or ""
        lens.append(len(content))
        if len(content) < args.short_threshold:
            short_count += 1

        chap_sec_counter[(row.get("chapter") or "∅", row.get("section") or "∅")] += 1
        for t in row.get("tags") or []:
            tag_counter[t] += 1

    if total == 0:
        print(f"{input_path}: empty file.")
        return 0

    print(f"=== {input_path} ===")
    print(f"total chunks            : {total}")
    print(f"null chapter            : {null_chapter:>4} ({null_chapter*100//total}%)")
    print(f"null section            : {null_section:>4} ({null_section*100//total}%)")
    print(f"null tags               : {null_tags:>4} ({null_tags*100//total}%)")
    print(f"null content_korean     : {null_korean:>4} ({null_korean*100//total}%)")
    print(f"chunk length min/avg/max: {min(lens)} / {sum(lens)//total} / {max(lens)}")
    print(f"chunks < {args.short_threshold} chars      : {short_count}")

    print(f"\ntop {args.top} (chapter, section) pairs:")
    for (chap, sec), cnt in chap_sec_counter.most_common(args.top):
        print(f"  {cnt:>4}  chapter={chap!r:30}  section={sec!r}")

    print(f"\ntop {args.top} tags:")
    for tag, cnt in tag_counter.most_common(args.top):
        print(f"  {cnt:>4}  {tag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
