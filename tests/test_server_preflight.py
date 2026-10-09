"""Exercise the server audit without importing the bot or contacting real APIs."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/parser_server_preflight.sh"


def test_server_preflight_reads_selected_source_and_masks_secrets(tmp_path):
    script = SCRIPT.read_text(encoding="utf-8")
    if shutil.which("bash"):
        subprocess.run(["bash", "-n"], input=script.encode("utf-8"), check=True, timeout=10)
    # Run the exact embedded Python against a disposable deployment fixture.
    audit = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    for package in ("config", "nlp"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").touch()
    (tmp_path / "config/settings.py").write_text(
        "from types import SimpleNamespace\n"
        "config=SimpleNamespace(DB_PATH='data.db',TEXT_MODEL='gpt-4o-mini',"
        "OPENAI_API_KEY='secret-marker-openai',TELEGRAM_BOT_TOKEN='secret-marker-telegram',"
        "OPENAI_HTTP_PROXY='secret-marker-proxy')\n",
        encoding="utf-8",
    )
    (tmp_path / "nlp/openai_client.py").write_text(
        "class MaskedApiError(Exception):\n"
        " status_code=403\n"
        "class SDK:\n"
        " def with_options(self,**kwargs): return self\n"
        " @property\n"
        " def models(self): return self\n"
        " async def retrieve(self,model):\n"
        "  if model=='gpt-6-luna': raise MaskedApiError('secret-marker-error')\n"
        "  return object()\n"
        " async def close(self): pass\n"
        "class OpenAIClient:\n"
        " async def _ensure_client(self): return SDK()\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            "CREATE TABLE items(id INTEGER,link TEXT,content TEXT,status TEXT,transcript TEXT);"
            "CREATE TABLE nlp_results(item_id INTEGER,summary TEXT,extra TEXT);"
        )
        db.executemany("INSERT INTO items VALUES(?,?,?,?,?)", [
            (77, "https://t.me/Wakeflot/3048", "https://wakeflot.ru/news/1785", "review", ""),
            (78, "https://t.me/Wakeflot/9000", "https://wakeflot.ru/news/17850", "new", ""),
            (79, "https://wakeflot.ru/news/1785", "Консервация двигателя PCM", "review", ""),
            (3048, "https://example.com/unrelated", "Другой материал", "new", ""),
        ])
        db.execute("INSERT INTO nlp_results VALUES(?,?,?)", (
            77, "Чемпионат 2023 года",
            json.dumps({"sanitized_text": "https://wakeflot.ru/news/1785"}),
        ))
    initial = hashlib.sha256(db_path.read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, "-B", "-"], input=audit, cwd=tmp_path,
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=15,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert next(r for r in rows if "wakeflot_matches" in r)["wakeflot_matches"] == 2
    items = {r["item_id"]: r for r in rows if "item_id" in r}
    assert set(items) == {77, 79}  # Telegram message ID is not the database ID.
    assert items[77]["content_has_text"] is False
    assert items[79]["content_has_text"] is True
    assert items[77]["summary_has_championship"] is True
    assert items[77]["summary_has_2023"] is True
    assert next(r for r in rows if r.get("model") == "gpt-6-luna")["http_status"] == 403
    assert "secret-marker" not in result.stdout + result.stderr
    assert "Консервация" not in result.stdout
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == initial
    assert not (tmp_path / "bot.log").exists()
    assert not list(tmp_path.rglob("__pycache__"))
