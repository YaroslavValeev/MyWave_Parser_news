"""Read-only runtime/Git/database audit: no publication, migration or service restart."""

from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path("/opt/bot3/parser-new-bot")
EXPECTED = {
    "bot.py": "8b73c70a591c45debdab7fbe8a07c97bf7c9fb7c06c5fb3f742ba3a2d8e15da0",
    "bot_aiogram.py": "fabec75e2730bf98d0042db94fb732da7b043e9cd0937398f913885bbf426f59",
    "collectors/competitions_html_calendar.py": "506527f662081103ae6c650ed6b35dd6e0db3c096845432dab78b5e17d22432b",
    "collectors/competitions_ical_calendar.py": "b565de93f70771cdc5d6944f7a7a2733d695b30f2202879df60745706b6900ba",
    "collectors/contacts_parser.py": "7cff7839451f98ce696c24fd1cad0919aee16bcc27f1cdaae4e63dd73468c771",
    "collectors/rss_collector.py": "fab48b325862416aec08079c7ff5a3436141851c69a748922b5b5c25e0c71b23",
    "collectors/rss_parser.py": "73f31e7d65c999e0142bad227519fcdd33c050dde392bb97f7bc6ce6679eb0b9",
    "collectors/telegram_collector.py": "6f4d10256d93d842e72247143ed8d77d4ca31f681cfdc973e39936000b716b61",
    "collectors/telegram_discussion.py": "489b2206a78091e06af0cae73a72e9e51d77d87e7a73fedb0ffbdd94eff21592",
    "collectors/telegram_parser.py": "7cb01bb9e5b7d3d2d8ee07493b63bc744efa5bec600ea7250356d3dc5404dcab",
    "collectors/website_parser.py": "c5262d55dc1e9997288596af0be3b22323f6ad89ebfff35e90819ba2b444eac4",
    "collectors/youtube_parser.py": "b18e06a9dfd44ec75bf910e09bc970f72f3adcec185b524dd6eea60d49abfb3b",
    "config.py": "0a2758e68c45674ba09fb73b1e129ecc0d0b937e41c0e20a52b9e45801ae3257",
    "config/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "config/settings.py": "c4a0b5cb88785b830fcbccda01d6d882a494c9c17d91e4fe05106f2ed3602d20",
    "core/__init__.py": "a42a05aa619a83f92d95ee9dcb04090e8f9864bb33d3dfacf0030bb7d71aa5d2",
    "core/collectors/rss_collector.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "core/logger.py": "461274f80a9d3d73eb41fe765cee45e763ee8f31c66df9bf6cb476638d9cc6b4",
    "core/models.py": "246afd6e9ec0098ec4e7123e38c4f984a3848578b8529942fb151488284e8981",
    "core/processors/deduplication.py": "6f7807fda8701e9a95000e7253d6cf28bf23b92e46c8df8d36e93552a43ca69d",
    "core/scheduler.py": "3991b137aef9ede03e108d3b7a22ab7ce084744b9c00cf29b7e3351777536c7f",
    "main.py": "3dcb4084e4f70a3856cfbc4cd911c6c089dfce5f0c4d2866d5c7b94d4d3dc624",
    "models.py": "ae6766c60fa7077d247d925f4f6d7a80c40b41c40ec0bbc318e07ddd40db7a50",
    "nlp/__init__.py": "95a00a0dfca5cd917ee882ee34ec439d0b181f802aa9c11b1b5fa5235ab4f590",
    "nlp/openai_client.py": "cd088abc7fb4e29aa49d863075ec502c36111601f531a0c10da638441362d6df",
    "nlp/routing.py": "8ae89dd5c2c84c78ddb7938e97f964eca226615389edb3db3ca1ddf9372218a5",
    "nlp/sanitize.py": "5918213a723d1d37c924fe843b201fec121078c041c755d3cf87e8464c176a82",
    "requirements.txt": "070757f43a07f3f2d76b4bd6ae7155dc990d354c546214fab2cdaaae60cb0663",
    "services/channel_adapters.py": "fa57a352f372639ace7b78ce3694ff197e3461d73ed6b40232cc33de6d672b7d",
    "services/channel_engagement.py": "b677b3f44798cd99ab9aa72d26578764517dcb5c5ea12acb45a2e6b699ca0dff",
    "services/collector_runner.py": "2653bee07bdf63dc6b630c20cd7de135ac86202eb2710a2ad4a66ef69b68a90e",
    "services/competitions_cache.py": "2c22de4316ceb730afae591ee43b449ca70c618f2c4373f5d64f9678c5141f82",
    "services/competitions_external_calendars.py": "52f881429d50dbce713cf699a86444ba1e4621ac45dbc4114474781efedf2d15",
    "services/competitions_from_news.py": "7fb4fd2a144fee8dd5c19e978ef8990e3c1f8fde8e714858f2b3b7ca5327a85f",
    "services/competitions_sources_collect.py": "6280d3a5cf116f9bd376df2cd3130f4b8e61b472682014422ac815cc4d8383d7",
    "services/competitions_ticker_sync.py": "aa61d7a24afb65c52b5d8143a2cf361e5ccce189d7a09b99d91e361e542ae4a6",
    "services/contact_consent.py": "df93c06a3d781f40a4f9117481e0c6316256de26cbc4e488b5f4814646704926",
    "services/editorial_layers.py": "3e8ea2439acd4bce5333547a880468e461861e75a7fde08a7577d9ee11bf3c82",
    "services/google_sheets.py": "ba04236459a1c252784536ac1344bd19c32a0c58568a50573efaf8147b8e9fb7",
    "services/knowledge_contract.py": "528aafbe53826bdb15774011c061ce39277c9f5e2388b1d499e8eea6126b4ed2",
    "services/maintenance.py": "c9ff4cca9e02855acb7f0dfa6d37fd2c3a0fd661fbab1a35b35924c230eb7ec3",
    "services/manual_collect.py": "99d08257c3afe7719a71e2720e2527a161abf5304097f1ba32f44310f2ee37f2",
    "services/media_pipeline.py": "2955a2ac23ec1bb218e11358975a69f14ae325119a356b5a77aa7bb952199b3a",
    "services/media_upload.py": "76859c972d28f7759eda4b88c0b4fdd696212149b2552da20b34704731dcfbf1",
    "services/nlp_pipeline.py": "e59a4e2f429c964c85139bbccf30beb577c65a59955bcf73e07f0854d4c78653",
    "services/owner_audit_export.py": "22adee6c3dca501fcf58ea42679293e716c2d9f7f3215752d9e7e9ce16437ba2",
    "services/publication.py": "9af5fa2a93f99c1e6d92a485840298cfa7a6ece3ec1978270ce9521c9f7a2cd4",
    "services/raw_feed_sync.py": "c09c33725de337910c65470667bcd1abf91530880790bd9e41739c9f4610fb40",
    "services/scheduler.py": "59aa4bd6a7f42d89ae29ca621bab00531dbdfaa999e87ca4bd771605de592510",
    "services/semantic_dedup.py": "92f1804f0a2b16a1c2f2b5c2f06ba3e4669f49e8d97a0a177a08bc82557128b5",
    "services/site_cache.py": "bd426781d46c6a25e65e9859368be0eb26a11b59723be83a972abd68725a04ff",
    "services/site_media_client.py": "f0d0fbf7eb730522429a83f7166eda986fcd2254a3153e26d87029967f28452e",
    "services/source_article.py": "0f5a982fe61dad48c45221b53d766712b5a7ba8128510122abef89bde4685e9b",
    "services/source_telemetry.py": "3a3748b90c95239ab374f61f50bc2ce061816b7db5da163b0857ddd5119a91b4",
    "services/telegram_media_hydrate.py": "b50e2df8e10c971f0d3aed14f3909d21ef7614f4707f27513696420178e50be1",
    "services/user_messages_sync.py": "db81d06ced01949bc21472409e1452cb9d9ef340e031ea444569a9d44281cf39",
    "start_telethon.py": "31d6086fe6ca7c8fb699bdce1ed64cb93b8a66843d573ddae3a30a466f0b0782",
    "storage/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "storage/data.py": "2b8c4af3fbb3c181fa3b3326c62aa2bba7311c4110d15a236f0f5725066196d8",
    "storage/google_sheets.py": "f14148a12b519ff5e2f308bd1ad2b271ceafef295c2aa4b42d8922c4769c9039",
    "storage/migrations/001_init.sql": "cb25e7b6b24c663ade0de67846a20b2ac1b1659aab7b236feb8f5a44a06acb79",
    "storage/migrations/002_add_contacts.sql": "f271bc08ed501c9b22476d8194f18f1c554c5d1985c4a2a632d528de7e3abcf5",
    "storage/migrations/002_add_scheduled_at.sql": "dcbfc810915672d05294527e8f41dd7e4f7c6ab183bcd9c82cd39d765197c6b8",
    "storage/migrations/003_source_health.sql": "6a0f0bbe25781db510e9e46f2f91c268543f27cffc10c017f8555b35e4bd0e5f",
    "storage/migrations/004_source_context.sql": "77d6eea365f2e00ae47b5c97c066137915371f5ed224f34557d5e5059b7ed71c",
    "storage/repository.py": "de7a3c07ca1b4a12ca1b88822fdc4ec95c6b94d515d331a37cc5737e819baea9",
    "storage/sources.py": "baf5089dc5dc8be469b3441c1608d82166276bd2b50a427c3a943a82df509995",
    "telegram_bot/__init__.py": "03268968f024d0b7d2e450367ff8b286d7c11c11c871d7eb5d2a726f83fe37a4",
    "telegram_bot/access.py": "3943c6ca9c250ac805d6dc9039439aaec65a64045c938e48ec4d7d159e2d02fd",
    "telegram_bot/client_copy.py": "170ec4a4b9744c6796164a015b64e209971d45a92bda2ba2d47896db5718e556",
    "telegram_bot/filters.py": "a9ac589d04069f087e29a6f4674889c5600168ef048a598c0d95615bf3eb549d",
    "telegram_bot/keyboards.py": "4d2025a3186534d1b4bb893f40b0d48d9c12c36214e4763b12afa18210b6f16d",
    "telegram_bot/middlewares.py": "607c6af078ca84fd81a330ab860dee9567c48ad1d51c102658e95054b25dc5b1",
    "telegram_bot/private_only.py": "22edfb220b0c6d31b6a29f43ac17eddf060ad4de794037be3fe7c802988fe17c",
    "telegram_bot/router.py": "4809bfec8d9e3712e5b81ce97cb0c870f9b9053cf0676250b6f0954af7b05158",
    "telegram_bot/views.py": "56e0fc0d96def21b762de0a31609903ed18eace3bfa1099b520ec042e7af38b3",
    "telegram_rate_limiter.py": "829d04a13d9c1424c78e13e5091781aa289c2381378796a6efce30375511cf51",
    "telegram_session.py": "78f4cf1795accfd6b868ff48abbf607203ba48bd047e1138745b5a3776865812",
    "test_p1_integration.py": "4fd91302ff1e378f46c934c7df4c23c8d533469ae9ae9d995391a9e9e709f13c",
    "utils/card_preview_text.py": "23dd91c2a61979c904e86249fdf9bdeb14327fd0972b42a80d3c482d15d0b79a",
    "utils/channel_commenters_contract.py": "f8f481ac329b58638789386e80a1f7ccd6f951c96ad75dd475f2b5be57e58872",
    "utils/collect_report.py": "f15ef0c27ce618a411fb8b614170ab8d80952a4d2e1cbeeb1f7d5911ea60e4b9",
    "utils/competitions_contract.py": "a95779efa7ac2c72e6aaba027b90bad586c4c0b3436b7542945bf4f2a4a99561",
    "utils/contract_schema.py": "88122343bb08cf81bd0df5a05fc2982b07cc4e321a2449da0de59ba080550292",
    "utils/fake_telethon.py": "e6710fa9b212ef591e8bd0b79c4d399f374b7efffc3a587f186dee4dc441d512",
    "utils/helpers.py": "62c4336f903ca16433cdc220a0194bc38d105df55e8cfa25491b2b6529c4cdd7",
    "utils/import_asyncio.py": "8f49e4862b5f456c0341f2ff1b8fc5a39875ab351f40d9c7afba241790c4682e",
    "utils/item_context.py": "8f3e5782079dfb872352aa6d574f6a78abe7b8a2444e463ab844aa2fbeaeaacf",
    "utils/item_freshness.py": "cc8df3574cc0cce08a0d2780f2f6867b3965f17c2dc31803f3823d6a103971d7",
    "utils/media_utils.py": "09d35048a1487a498ffbdb12e40dfdaddc7ac8d52a4df039da55d7c8bd868a62",
    "utils/owner_content.py": "9acf8234f1a051a293d72d4ddba53b4096021b18da759e520d2f8cf217f7ccfc",
    "utils/rate_limiter.py": "c76154b1496c287dbaa50a248a1c6b76f9d04689c4c55d7fb08cf7bbe51d3095",
    "utils/row_utils.py": "1fb51ca3ea4ead7dd2a8fcd06c5f85b1d22193dd23e621de08fe0311fb88d00f",
    "utils/rss_media.py": "5093f9a10e1d7ca38bd4af242b089a923a09937341fdecb985571cde16ff52dc",
    "utils/russian_summary.py": "b96d596f2c6e52ca79d7feec0b37d3af38c3fa922c4b28e2ed880da7a637b1ba",
    "utils/safe_http.py": "d493c74b0efb5fdfefe557c7848e7751569de3c6798b3eb0c995f93c9fec5f8c",
    "utils/sheet_gateway.py": "7d5c7c49ddfc2d42e0d0c02762b64e75ddbcd1cdf218759e7b9ab5a79bc127dd",
    "utils/sheet_schema.py": "5be2ba384b4f132eb5e6fd7ff1288b12e0f7a680bbd8d019e5ed46fef0f8f5e2",
    "utils/source_context.py": "f59dd197a8fe8a32189c696ed7416fa1b0395b782036e6094d952cf2b9fdd281",
    "utils/status_labels_ru.py": "db45d78fd9f88eb9a22ed430f1fdc024f64e83f2d1bc7d21728923bc0c9a7262",
    "utils/telegram_editorial.py": "37996238b2803b70971bdde1557e45f06bda1dd68c825d3dc2d55765119c79dd",
    "utils/telegram_media_input.py": "cf7edcf5a2c86bb0eb031fbca1253fbdc9d5a5c2473c05fd6a6ac7eb43c7a285",
    "utils/telegram_session.py": "ec9ee968c977947956cc8cc57dbe6ef59f2190287bd5546c12f497bd355d894a",
    "utils/video_providers.py": "27b630d4c8720c397b6e6ed0826437aec4db6cb05f31328f11c15391fcaf6e39",
    "utils/web_editorial.py": "236f9070221917d3bf41d2cefb82e1d768348ddb6178a7ce795849e97d002a0c",
}
SOURCE_REF = "5846f565a1e78a123697892ebc24654e527534b6"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def runtime_files(root, expected=EXPECTED):
    matched, different, missing, unsafe = [], {}, [], []
    for name, value in expected.items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            unsafe.append(name)
        elif not path.is_file():
            missing.append(name)
        else:
            actual = digest(path.read_bytes())
            if actual == value:
                matched.append(name)
            else:
                different[name] = actual
    return {
        "matched_count": len(matched),
        "expected_count": len(expected),
        "different": different,
        "missing": missing,
        "unsafe": unsafe,
    }


def database_state(path):
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    ) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        result = {}
        for item_id in (626, 649):
            row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            result[str(item_id)] = {
                "status": row["status"] if row else "missing",
                "publications": db.execute(
                    "SELECT COUNT(*) FROM publications WHERE item_id=?", (item_id,)
                ).fetchone()[0],
            }
            if item_id != 626 or row is None:
                continue
            nlp = db.execute(
                "SELECT extra FROM nlp_results WHERE item_id=626"
            ).fetchone()
            try:
                item = dict(row)
                source = json.loads(item.get("source_context") or "null")
                extra = json.loads(nlp["extra"] or "{}") if nlp else {}
                fields = {
                    key: item.get(key)
                    for key in ("id", "content", "transcript", "link")
                }
                input_sha = hashlib.sha256(
                    json.dumps(
                        fields, ensure_ascii=False, sort_keys=True, default=str
                    ).encode()
                ).hexdigest()
                text_sha = hashlib.sha256(source["text"].encode()).hexdigest()
                valid = (
                    source.get("version") == 1
                    and source.get("input_sha256") == input_sha
                    and source.get("text_sha256") == text_sha
                    and source.get("requested_url") == "https://wakeflot.ru/news/1786"
                    and source.get("final_url") == source.get("requested_url")
                    and 200 <= len(source["text"]) <= 30000
                )
                result["626"].update(
                    source_bound=valid,
                    nlp_matches_source=bool(
                        valid
                        and extra.get("source_input_sha256") == input_sha
                        and extra.get("source_text_sha256") == text_sha
                    ),
                    article_sha256=text_sha,
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                result["626"].update(source_bound=False, nlp_matches_source=False)
        return result


def command(args):
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return (
        subprocess.check_output(
            args, cwd=ROOT, env=env, stderr=subprocess.DEVNULL, timeout=15
        )
        .decode("utf-8", errors="replace")
        .strip()
    )


def main():
    if Path.cwd().resolve() != ROOT:
        raise RuntimeError("run_in_server_project")
    files = runtime_files(ROOT)
    props = command(
        [
            "systemctl",
            "show",
            "parser-news-bot",
            "--property=ActiveState,SubState,MainPID,ExecMainStatus,WorkingDirectory",
        ]
    )
    service = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
    changes = command(["git", "status", "--porcelain=v1", "--untracked-files=normal"])
    git_state = {
        "head": command(["git", "rev-parse", "HEAD"]),
        "changed_entries": sum(
            not line.startswith("??") for line in changes.splitlines()
        ),
        "untracked_entries": sum(
            line.startswith("??") for line in changes.splitlines()
        ),
    }
    settings = {"checked": False}
    if not any(
        name.startswith("config/")
        for name in [*files["different"], *files["missing"], *files["unsafe"]]
    ):
        sys.path.insert(0, str(ROOT))
        from config.settings import config

        if Path(config.DB_PATH).resolve() != ROOT / "data.db":
            raise RuntimeError("database_path_changed")
        settings = {
            "checked": True,
            "text_model": config.TEXT_MODEL,
            "source_fetch_enabled": bool(config.SOURCE_ARTICLE_FETCH_ENABLED),
            "source_hosts": list(config.SOURCE_ARTICLE_ALLOWED_HOSTS),
        }
    result = {
        "runtime_audit": "ok",
        "source_ref": SOURCE_REF,
        "files": files,
        "service": service,
        "git": git_state,
        "settings": settings,
        "items": database_state(ROOT / "data.db"),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("openai", "httpx", "aiogram", "aiosqlite")
        },
        "database_writes": 0,
        "real_openai_requests": 0,
        "telegram_sends": 0,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps({"runtime_audit": "failed", "error_type": type(exc).__name__}),
            flush=True,
        )
        raise SystemExit(1)
