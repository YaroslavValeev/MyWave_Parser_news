import hashlib
import json
from unittest.mock import AsyncMock

import pytest


from services import source_article
from services.nlp_pipeline import reprocess_items
from storage.repository import AsyncNewsRepository, initialize_database
from utils.item_context import get_item_text_context, is_title_only_summary_fallback
from utils.source_context import linked_article_context, source_input_hash


URL = "https://news.example.test/news/1785"
TEXT = (
    "Материал о подготовке двигателя PCM к зимнему хранению и проверке технических жидкостей. "
    * 5
)
HTML = f'<html><h1>Хранение PCM</h1><div id="textContainer"><p>{TEXT}</p><aside>Чемпионат 2023</aside></div><footer>Другая новость</footer></html>'.encode()


def item():
    return {
        "id": 7,
        "content": URL,
        "transcript": "",
        "link": "https://t.me/Channel/3048",
    }


def evidence(value):
    return {
        "version": 1,
        "requested_url": URL,
        "final_url": URL,
        "title": "Хранение PCM",
        "text": TEXT,
        "fetched_at": "2026-10-06T00:00:00+00:00",
        "input_sha256": source_input_hash(value),
        "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "html_sha256": hashlib.sha256(HTML).hexdigest(),
    }


def test_context_is_bound_to_original_item_and_derived_result():
    value = item()
    bound = evidence(value)
    value["source_context"] = json.dumps(bound)
    assert get_item_text_context(value) == TEXT
    assert is_title_only_summary_fallback(value, {"summary": "Чемпионат"})
    nlp = {
        "summary": "PCM",
        "extra": {
            "source_input_sha256": source_input_hash(value),
            "source_text_sha256": bound["text_sha256"],
        },
    }
    assert not is_title_only_summary_fallback(value, nlp)
    for change in (
        {"id": 8},
        {"content": "https://news.example.test/news/9999"},
        {"link": "https://t.me/Other/1"},
    ):
        assert linked_article_context({**value, **change}) is None
    broken = {**bound, "text": "подменённый текст " * 30}
    assert linked_article_context({**value, "source_context": broken}) is None


def test_parser_selects_article_without_other_news_or_navigation():
    title, text = source_article.extract_article(HTML, URL)
    assert title == "Хранение PCM" and "PCM" in text
    assert "Чемпионат" not in text and "Другая новость" not in text


@pytest.mark.parametrize(
    "html,reason",
    [
        (b"<h1>Listing</h1><main><p>links</p></main>", "article_body_missing"),
        (b"<h1>A</h1><h1>B</h1><article>data</article>", "ambiguous_article_heading"),
        (b"<h1>A</h1><article>short</article>", "article_text_length"),
        (
            HTML + b'<link rel="canonical" href="https://news.example.test/news/9999">',
            "canonical_source_changed",
        ),
    ],
)
def test_parser_refuses_ambiguous_or_unbound_page(html, reason):
    with pytest.raises(source_article.ArticleFetchError, match=reason):
        source_article.extract_article(html, URL)


class Response:
    def __init__(self, status=200, data=HTML, headers=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}
        self.is_redirect = status in {301, 302}
        self.data = data
        self.closed = False

    def iter_content(self, size):
        yield self.data

    def close(self):
        self.closed = True


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(
        source_article.socket,
        "getaddrinfo",
        lambda *args: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )


def test_fetch_has_bounds_and_closes_response(monkeypatch, public_dns):
    response = Response()
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(source_article, "safe_get", get)
    result = source_article.fetch_article(item(), {"news.example.test"})
    assert result["requested_url"] == URL and result[
        "input_sha256"
    ] == source_input_hash(item())
    assert (
        response.closed
        and calls[0][1]["stream"] is True
        and calls[0][1]["allow_redirects"] is False
    )


@pytest.mark.parametrize(
    "case,reason",
    [
        ("private", "non_public_address"),
        ("host", "host_not_allowed"),
        ("redirect", "redirect_source_changed"),
        ("binary", "not_html"),
        ("oversize", "html_too_large"),
        ("multiple", "ambiguous_or_missing_url"),
    ],
)
def test_fetch_refuses_unsafe_or_wrong_source(monkeypatch, public_dns, case, reason):
    value = item()
    response = Response()
    hosts = {"news.example.test"}
    if case == "private":
        monkeypatch.setattr(
            source_article.socket,
            "getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("127.0.0.1", 443))],
        )
    elif case == "host":
        hosts = set()
    elif case == "redirect":
        response = Response(
            302, headers={"Location": "https://news.example.test/news/9999"}
        )
    elif case == "binary":
        response.headers = {"Content-Type": "video/mp4"}
    elif case == "oversize":
        response.data = b"x" * (source_article.MAX_HTML_BYTES + 1)
    elif case == "multiple":
        value["content"] = URL + " https://news.example.test/news/9999"
    monkeypatch.setattr(source_article, "safe_get", lambda *args, **kwargs: response)
    with pytest.raises(source_article.ArticleFetchError, match=reason):
        source_article.fetch_article(value, hosts)
    if case in {"redirect", "binary", "oversize"}:
        assert response.closed


@pytest.mark.asyncio
async def test_pipeline_fetches_selected_source_preserving_original_and_other_queue(
    tmp_path, monkeypatch
):
    from config.settings import config

    db = tmp_path / "data.db"
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    selected = await repo.create_item(
        {
            "source": "Telegram",
            "content": URL,
            "link": "https://t.me/Channel/3048",
            "status": "review",
            "images": "photo.jpg",
        }
    )
    pending = await repo.create_item(
        {
            "source": "Other",
            "content": "Другой материал",
            "link": "https://news.example.test/other",
            "status": "new",
        }
    )
    await repo.upsert_author_notes(selected, "Комментарий владельца")
    original = await repo.get_item(selected)
    monkeypatch.setattr(config, "SOURCE_ARTICLE_FETCH_ENABLED", True)
    monkeypatch.setattr(config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("news.example.test",))
    monkeypatch.setattr(
        source_article,
        "retrieve_article",
        AsyncMock(side_effect=lambda value, hosts: evidence(value)),
    )
    client = AsyncMock()
    client.summarize.return_value = "Подготовка PCM к хранению"
    client.gen_questions.return_value = ["Как готовить двигатель?"]
    client.moderate.return_value = {"flagged": False}
    client.generate_cover.return_value = {}
    assert await reprocess_items([selected], repository=repo, client=client) == 1
    client.summarize.assert_awaited_once_with(TEXT, lang="ru")
    saved = await repo.get_item(selected)
    assert saved["content"] == original["content"] and saved["images"] == "photo.jpg"
    assert (await repo.get_item(pending))[
        "status"
    ] == "new" and await repo.get_nlp_results(pending) is None
    nlp = await repo.get_nlp_results(selected)
    assert nlp["author_notes"] == "Комментарий владельца"
    assert not is_title_only_summary_fallback(saved, nlp)
    assert await repo.get_last_log(selected, "source_article_fetched")


@pytest.mark.asyncio
async def test_source_changed_while_fetching_and_owner_rejection_are_preserved(
    tmp_path,
):
    db = tmp_path / "data.db"
    await initialize_database(db)
    await initialize_database(db)  # migration is idempotent via schema versions
    repo = AsyncNewsRepository(db)
    selected = await repo.create_item(
        {"source": "Telegram", "content": URL, "link": "https://t.me/Channel/3048"}
    )
    value = await repo.get_item(selected)
    bound = evidence(value)
    await repo.update_item_content(selected, "Новый исходник")
    with pytest.raises(ValueError, match="input_changed"):
        await repo.save_source_context(selected, bound)
    await repo.update_status(selected, "discarded")
    await repo.restore_source_review(selected)
    assert (await repo.get_item(selected))["status"] == "discarded"
    with pytest.raises(ValueError, match="unavailable"):
        await repo.save_source_context(selected, bound)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["fetch_refusal", "generation"])
async def test_owner_rejection_during_processing_is_preserved(
    tmp_path, monkeypatch, stage
):
    from config.settings import config

    db = tmp_path / "data.db"
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    selected = await repo.create_item(
        {"source": "Telegram", "content": URL, "link": URL}
    )
    monkeypatch.setattr(config, "SOURCE_ARTICLE_FETCH_ENABLED", True)
    monkeypatch.setattr(config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("news.example.test",))

    async def retrieve(value, hosts):
        if stage == "fetch_refusal":
            await repo.update_status(selected, "discarded")
            raise source_article.ArticleFetchError("http_error")
        return evidence(value)

    async def summarize(*args, **kwargs):
        await repo.update_status(selected, "discarded")
        return "Подготовка PCM к хранению"

    monkeypatch.setattr(source_article, "retrieve_article", retrieve)
    client = AsyncMock()
    client.summarize.side_effect = summarize
    client.gen_questions.return_value = []
    client.moderate.return_value = {"flagged": False}
    client.generate_cover.return_value = {}
    assert await reprocess_items([selected], repository=repo, client=client) == 0
    assert (await repo.get_item(selected))["status"] == "discarded"
    assert await repo.get_nlp_results(selected) is None


def test_query_cannot_select_another_article(monkeypatch):
    url = "https://news.example.test/news?id=1785"
    other = "https://news.example.test/news?id=9999"
    monkeypatch.setattr(
        source_article.socket,
        "getaddrinfo",
        lambda *args: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    with pytest.raises(
        source_article.ArticleFetchError, match="redirect_source_changed"
    ):
        source_article._check_url(other, url, {"news.example.test"})
    html = (
        '<link rel="canonical" href="'
        + other
        + '"><h1>PCM</h1><article>'
        + TEXT
        + "</article>"
    )
    with pytest.raises(
        source_article.ArticleFetchError, match="canonical_source_changed"
    ):
        source_article.extract_article(html, url)
    value = {"id": 1, "content": url, "link": url}
    bound = evidence(value)
    bound.update(requested_url=url, final_url=other)
    assert linked_article_context({**value, "source_context": bound}) is None
