"""Compare unchanged text tasks; dry-run by default, never writes to the bot DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.openai_client import OpenAIClient, OpenAISettings
from utils.item_context import get_item_text_context


def read_cases(path: Path, min_cases: int = 100) -> list[dict]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("Every case needs a unique nonempty string id")
        seen.add(case_id)
        if not isinstance(case.get("source_text"), str):
            raise ValueError("Every case needs source_text")
        text = get_item_text_context({"content": case["source_text"]})
        without_urls = re.sub(r"(?:https?://|www\.)\S+", "", text, flags=re.IGNORECASE)
        if not any(char.isalnum() for char in without_urls):
            raise ValueError(
                "URL-only or empty cases cannot evaluate factual generation"
            )
        for field in ("owner_notes", "expected_facts", "forbidden_facts"):
            if field in case and not isinstance(case[field], str):
                raise ValueError(f"{field} must be a string")
    if len(cases) < min_cases:
        raise ValueError(f"At least {min_cases} cases required; found {len(cases)}")
    return cases


class Metrics(logging.Handler):
    def __init__(self):
        super().__init__()
        self.calls = []

    def emit(self, record):
        if record.msg == "text_completion_succeeded":
            self.calls.append(
                {
                    key: getattr(record, key, None)
                    for key in (
                        "text_model",
                        "reasoning_effort",
                        "latency_ms",
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "cached_tokens",
                    )
                }
            )


async def evaluate(cases: list[dict], output: Path, effort: str) -> int:
    base = OpenAISettings.from_config()
    if not base.api_key:
        raise ValueError("OPENAI_API_KEY is required for --run")
    if output.exists():
        raise ValueError("Output already exists; choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    clients = [
        OpenAIClient(replace(base, text_model=model, text_reasoning_effort=effort))
        for model in ("gpt-4o-mini", "gpt-6-luna")
    ]
    metrics = Metrics()
    logger = logging.getLogger("nlp.openai_client")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(metrics)
    errors = 0
    try:
        with output.open("x", encoding="utf-8") as report:
            for case in cases:
                for client in clients:
                    record = {
                        "id": case["id"],
                        "model": client._settings.text_model,
                        "effort": effort
                        if client._settings.text_model == "gpt-6-luna"
                        else None,
                        "source_text": case["source_text"],
                        "expected_facts": case.get("expected_facts", ""),
                        "forbidden_facts": case.get("forbidden_facts", ""),
                        "review": {
                            "facts_correct": None,
                            "same_news": None,
                            "russian": None,
                            "owner_voice": None,
                            "accepted": None,
                        },
                    }
                    start = len(metrics.calls)
                    try:
                        record["summary"] = await client.summarize(
                            case["source_text"], lang="ru"
                        )
                        record["questions"] = await client.gen_questions(
                            case["source_text"], lang="ru"
                        )
                        if case.get("owner_notes"):
                            record["rewrite"] = await client.author_rewrite(
                                case["source_text"],
                                case["owner_notes"],
                                base_summary=record["summary"],
                                lang="ru",
                            )
                    except Exception as exc:
                        # Exception messages may include request details; report only the type.
                        record["error_type"] = type(exc).__name__
                        errors += 1
                    record["calls"] = metrics.calls[start:]
                    report.write(json.dumps(record, ensure_ascii=False) + "\n")
                    report.flush()
    finally:
        logger.removeHandler(metrics)
        logger.setLevel(old_level)
        for client in clients:
            if client._client is not None:
                await client._client.close()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("eval-results.jsonl"))
    parser.add_argument("--min-cases", type=int, default=100)
    parser.add_argument("--effort", choices=["none", "low"], default="none")
    parser.add_argument(
        "--run", action="store_true", help="Make paid API calls; default validates only"
    )
    args = parser.parse_args()
    if args.min_cases < 1:
        parser.error("--min-cases must be positive")
    try:
        cases = read_cases(args.cases, args.min_cases)
        calls = 2 * sum(2 + bool(c.get("owner_notes")) for c in cases)
        print(
            f"Validated {len(cases)} cases; planned {calls} text calls (plus configured retries)"
        )
        if not args.run:
            print("Dry-run: no API requests, no output written")
            return 0
        errors = asyncio.run(evaluate(cases, args.output, args.effort))
        print(
            f"Comparison saved; failed case/model pairs: {errors}; human review required"
        )
        return 1 if errors else 0
    except (ValueError, OSError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
