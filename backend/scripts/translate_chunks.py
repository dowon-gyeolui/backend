"""Translate `content_original` → `content_korean` in a knowledge JSONL file.

Uses OpenAI's Responses API.

Usage
-----
    export OPENAI_API_KEY=sk-...
    python scripts/translate_chunks.py \
        --input  data/processed/적천수.jsonl \
        --output data/processed/적천수.ko.jsonl \
        --skip-existing

Behavior
--------
- `content_original` is never modified.
- `content_korean` is filled with a faithful, literal Korean translation.
- Every input row is written to the output (even if not translated this run),
  so the script is resumable: point `--input` at the last output and re-run
  with `--skip-existing` to pick up where it stopped.
- Rows that fail to translate keep their original `content_korean` value
  (null or existing) and are logged to stderr.

Install
-------
    pip install -r requirements.txt     # openai SDK is already a runtime dep
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file, load_jsonl, write_jsonl_row  # noqa: E402

load_env_file(_BACKEND_ROOT)


_SYSTEM_PROMPT = (
    "You are a translator of classical Chinese (古文/文言文) into modern Korean.\n"
    "Rules:\n"
    "- Translate faithfully and literally. Do NOT add interpretation, commentary, or explanation.\n"
    "- Preserve paragraph structure and line breaks where possible.\n"
    "- Keep technical terms of 사주/자미두수 in their standard Korean renderings "
    "(예: 五行→오행, 天干→천간, 地支→지지, 甲木→갑목, 日主→일주).\n"
    "- Preserve classical heading / title strings. When a short standalone line "
    "is a chapter or section title such as 羅序, 太微賦, 形性賦, 星垣論, 斗數準繩, "
    "五行總論, 十干分論, 論木, 論甲木, 正月甲木, 例曰, or 問紫微所主若何, "
    "keep the original Hanja followed by its Korean transliteration in parentheses — "
    "e.g. `太微賦 (태미부)`, `論甲木 (논갑목)`, `正月甲木 (정월갑목)`. "
    "Do NOT paraphrase titles into descriptive phrases.\n"
    "- Preserve star names (紫微, 天府, 太陰, 貪狼, 七殺, 破軍, 文昌, 文曲 …), "
    "palace names (命宮, 官祿, 財帛 …), stem/branch pairs (甲子, 乙丑 …), and "
    "person/place proper nouns as recognizable Korean Hanja transliterations; "
    "do not translate them into meaning-based phrases.\n"
    "- Prefer conservative/literal rendering over natural paraphrase when in doubt.\n"
    "- Do not add any preface, apology, note, or footnote.\n"
    "- Output ONLY the translated text, nothing else."
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--model", default="gpt-5.4-mini",
                   help="OpenAI model id (default: gpt-5.4-mini).")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip rows that already have a non-null content_korean.")
    p.add_argument("--limit", type=int, default=None,
                   help="Only translate the first N otherwise-eligible rows (rest pass through).")
    p.add_argument("--max-output-tokens", type=int, default=4096)
    return p.parse_args()


def _load_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        env_path = _BACKEND_ROOT / ".env"
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        if env_path.exists():
            print(
                f"  {env_path} exists but does not contain a usable OPENAI_API_KEY. "
                "Check for stray whitespace or an empty value.",
                file=sys.stderr,
            )
        else:
            print(
                f"  Create {env_path} with a line `OPENAI_API_KEY=sk-...` "
                "or export the variable in your shell.",
                file=sys.stderr,
            )
        sys.exit(1)
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai SDK not installed. Run: pip install -r requirements.txt",
              file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key)


def _extract_output_text(resp) -> str:
    direct = getattr(resp, "output_text", None)
    if direct:
        return direct.strip()

    parts: list[str] = []
    for block in getattr(resp, "output", []) or []:
        for content in getattr(block, "content", []) or []:
            piece = getattr(content, "text", None)
            if piece:
                parts.append(piece)
    return "".join(parts).strip()


def _translate(client, *, model: str, text: str, max_output_tokens: int) -> str:
    resp = client.responses.create(
        model=model,
        instructions=_SYSTEM_PROMPT,
        input=text,
        max_output_tokens=max_output_tokens,
    )
    return _extract_output_text(resp)


def main() -> int:
    args = _parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    if input_path.resolve() == output_path.resolve():
        print("ERROR: --input and --output must be different files.", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = list(load_jsonl(input_path))
    client = _load_client()

    translated = 0
    skipped_existing = 0
    passed_through = 0
    failed = 0

    with output_path.open("w", encoding="utf-8") as fp:
        for row in rows:
            if args.skip_existing and row.get("content_korean"):
                skipped_existing += 1
                write_jsonl_row(fp, row)
                continue

            if args.limit is not None and translated >= args.limit:
                passed_through += 1
                write_jsonl_row(fp, row)
                continue

            original = row.get("content_original") or ""
            if not original.strip():
                passed_through += 1
                write_jsonl_row(fp, row)
                continue

            try:
                ko = _translate(
                    client,
                    model=args.model,
                    text=original,
                    max_output_tokens=args.max_output_tokens,
                )
                row["content_korean"] = ko
                translated += 1
                idx = row.get("chunk_index")
                print(f"[{idx}] translated ({len(ko)} chars)")
            except Exception as exc:
                failed += 1
                idx = row.get("chunk_index")
                print(f"[{idx}] FAILED: {exc}", file=sys.stderr)

            write_jsonl_row(fp, row)

    print(
        f"done. translated={translated}, skipped_existing={skipped_existing}, "
        f"passed_through={passed_through}, failed={failed}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
