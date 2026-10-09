"""Read-only extraction probe: no database writes, OpenAI calls or Telegram sends."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.source_article import ArticleFetchError, fetch_article


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--host", required=True, help="Exact permitted hostname")
    args = parser.parse_args()
    try:
        result = fetch_article(
            {"id": 0, "content": args.url, "link": args.url}, {args.host.lower()}
        )
        print(
            json.dumps(
                {
                    "article_probe": "ok",
                    "title": result["title"],
                    "chars": len(result["text"]),
                    "text_sha256": result["text_sha256"],
                    "html_sha256": result["html_sha256"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except ArticleFetchError as exc:
        print(json.dumps({"article_probe": "refused", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
