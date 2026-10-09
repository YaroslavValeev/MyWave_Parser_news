#!/usr/bin/env bash
# Audit installed code and the selected source before a release.
# Does not restart the bot, change application settings, or write to SQLite.
(
set +x
set -eu
cd /opt/bot3/parser-new-bot
printf '\nSERVICE\n'
systemctl show parser-news-bot --property=ActiveState,SubState,MainPID,ExecMainStatus,WorkingDirectory,FragmentPath
printf '\nGIT\n'
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git rev-parse HEAD
    git branch --show-current
    git status --short
    parser_release_sha=15711991519a7ec254bb7883fb486fa59b879c0c
    printf 'RELEASE_SHA=%s\n' "$parser_release_sha"
    if timeout 45s env GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=10' git fetch origin feat/blog-editorial-media-pipeline >/dev/null 2>&1; then
        printf 'FETCH=ok\n'
        if git cat-file -e "$parser_release_sha^{commit}" 2>/dev/null; then
            printf 'CURRENT_ONLY RELEASE_ONLY: '
            git rev-list --left-right --count "HEAD...$parser_release_sha"
        fi
    else
        printf 'FETCH=failed\n'
    fi
else
    printf 'git_repository_missing_or_inaccessible\n'
fi
if [ -x venv/bin/python ]; then
    parser_python=venv/bin/python
elif [ -x .venv/bin/python ]; then
    parser_python=.venv/bin/python
else
    parser_python=python3
fi
printf '\nPYTHON / DATABASE / MODEL METADATA\n'
"$parser_python" -B - <<'PY'
import asyncio
import html
import importlib.metadata
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path.cwd()))
from config.settings import config


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def has_text(value):
    text = html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))
    return any(c.isalnum() for c in re.sub(r"(?:https?://|www\.)\S+", "", text, flags=re.I))


for package in ("openai", "httpx", "aiogram", "aiosqlite"):
    try:
        emit(package=package, version=importlib.metadata.version(package))
    except importlib.metadata.PackageNotFoundError:
        emit(package=package, version="missing")
emit(text_model=getattr(config, "TEXT_MODEL", None),
     openai_key_set=bool(getattr(config, "OPENAI_API_KEY", "")),
     telegram_token_set=bool(getattr(config, "TELEGRAM_BOT_TOKEN", "")),
     openai_proxy_set=bool(getattr(config, "OPENAI_HTTP_PROXY", None)))

db_path = Path(getattr(config, "DB_PATH", "data.db")).resolve()
emit(db_path=str(db_path), db_present=db_path.is_file())
if db_path.is_file():
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        emit(sqlite_ok=db.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "items" not in tables:
            emit(items_table_missing=True)
        else:
            columns = {r[1] for r in db.execute("PRAGMA table_info(items)")}
            if not {"id", "link", "content"} <= columns:
                emit(item_schema_incompatible=True)
            else:
                rows = db.execute(
                    "SELECT * FROM items WHERE link LIKE ? OR content LIKE ? "
                    "OR link LIKE ? OR content LIKE ? ORDER BY id DESC",
                    ("%t.me/%Wakeflot/3048%", "%t.me/%Wakeflot/3048%",
                     "%wakeflot.ru/news/1785%", "%wakeflot.ru/news/1785%"),
                ).fetchall()
                target = re.compile(r"(?:wakeflot\.ru/news/1785|t\.me/(?:s/)?Wakeflot/3048)(?=$|[\s/?#<])", re.I)
                matches = [dict(r) for r in rows if target.search(str(r["link"] or "") + "\n" + str(r["content"] or ""))]
                emit(wakeflot_matches=len(matches))
                for item in matches[:20]:
                    nlp_row = db.execute("SELECT * FROM nlp_results WHERE item_id=?", (item["id"],)).fetchone() if "nlp_results" in tables else None
                    nlp = dict(nlp_row) if nlp_row else {}
                    try:
                        extra = json.loads(nlp.get("extra") or "{}")
                    except (ValueError, TypeError):
                        extra = {}
                    extra = extra if isinstance(extra, dict) else {}
                    summary = str(nlp.get("summary") or "").casefold()
                    emit(item_id=item["id"], status=item.get("status"),
                         content_has_text=has_text(item.get("content")),
                         transcript_has_text=has_text(item.get("transcript")),
                         sanitized_has_text=has_text(extra.get("sanitized_text")),
                         summary_has_pcm="pcm" in summary,
                         summary_has_championship="чемпионат" in summary,
                         summary_has_2023="2023" in summary,
                         owner_rewritten=extra.get("owner_rewritten") is True)
    finally:
        db.close()


async def probe():
    from nlp.openai_client import OpenAIClient
    sdk = await OpenAIClient()._ensure_client()
    try:
        for model in dict.fromkeys((config.TEXT_MODEL, "gpt-6-luna")):
            try:
                await sdk.with_options(timeout=15, max_retries=0).models.retrieve(model)
                emit(model=model, model_visible=True)
            except Exception as exc:
                emit(model=model, model_visible=False, error_type=type(exc).__name__, http_status=getattr(exc, "status_code", None))
    finally:
        await sdk.close()


if getattr(config, "OPENAI_API_KEY", ""):
    try:
        asyncio.run(asyncio.wait_for(probe(), timeout=35))
    except Exception as exc:
        emit(api_probe_error=type(exc).__name__)
else:
    emit(api_probe_skipped="OPENAI_API_KEY missing")
PY
)
