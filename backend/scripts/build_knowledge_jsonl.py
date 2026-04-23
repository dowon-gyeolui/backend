"""Build a retrieval-ready JSONL file from a raw classical-text source.

Usage
-----
    python scripts/build_knowledge_jsonl.py \
        --input   data/raw/적천수천미.txt \
        --output  data/processed/적천수천미.jsonl \
        --source-type 사주 \
        --source-title 적천수천미 \
        --source-author 유백온 \
        --topic 오행론 \
        --language zh \
        --tags "오행,목기" \
        --max-chars 500 \
        --min-chars 120

The output JSONL has one chunk per line. `content_korean` is always null here;
translation is done by translate_chunks.py.

Per-chunk tags combine (CLI --tags) + (rule-based tags auto-extracted from the
chunk's content and heading metadata).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/build_knowledge_jsonl.py` to import from `app.*`
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.knowledge.chunking import chunk_text  # noqa: E402
from scripts._helpers import (  # noqa: E402
    extract_tags,
    parse_source_txt,
    sha256,
    write_jsonl_row,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="Path to the raw source txt file.")
    p.add_argument("--output", required=True, help="Path to the output JSONL file.")
    p.add_argument("--source-type", required=True, help="e.g. 사주 | 자미두수")
    p.add_argument("--source-title", required=True, help="e.g. 적천수천미")
    p.add_argument("--source-author", default=None)
    p.add_argument("--topic", default=None, help="Primary retrieval topic for all chunks.")
    p.add_argument("--language", default="zh", help="ISO code of content_original (default: zh).")
    p.add_argument("--tags", default="",
                   help="Comma-separated baseline tags applied to every chunk.")
    p.add_argument("--max-chars", type=int, default=500)
    p.add_argument("--min-chars", type=int, default=120,
                   help="Merge chunks smaller than this into neighbors (default: 120).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw = input_path.read_text(encoding="utf-8")
    blocks = parse_source_txt(raw)
    baseline_tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    total = 0
    with output_path.open("w", encoding="utf-8") as fp:
        for block in blocks:
            pieces = chunk_text(block.text, max_chars=args.max_chars, min_chars=args.min_chars)
            for piece in pieces:
                tags = extract_tags(
                    piece,
                    chapter=block.chapter,
                    section=block.section,
                    topic=args.topic,
                    extra=baseline_tags or None,
                )
                row = {
                    "source_type":      args.source_type,
                    "source_title":     args.source_title,
                    "source_author":    args.source_author,
                    "topic":            args.topic,
                    "chapter":          block.chapter,
                    "section":          block.section,
                    "chunk_index":      total,
                    "language":         args.language,
                    "tags":             tags,
                    "content_original": piece,
                    "content_korean":   None,
                    "content_hash":     sha256(piece),
                }
                write_jsonl_row(fp, row)
                total += 1

    print(f"wrote {total} chunks across {len(blocks)} blocks → {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
