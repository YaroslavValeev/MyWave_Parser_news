from utils.card_preview_text import (
    PUBLIC_TITLE_MAX_LEN,
    editorial_structure_issues,
    lead_from_text,
    normalize_public_title,
    public_text_quality_issues,
    strip_embedded_video_urls,
)


def test_public_title_max_is_90():
    assert PUBLIC_TITLE_MAX_LEN == 90
    long_title = "A" * 120
    assert len(normalize_public_title(long_title)) <= 90


def test_lead_from_text_limits_to_two_sentences():
    text = "First sentence. Second sentence. Third should be dropped."
    lead = lead_from_text(text)
    assert "First sentence" in lead
    assert "Second sentence" in lead
    assert "Third" not in lead


def test_strip_embedded_video_urls_removes_youtube_line():
    body = "Intro paragraph.\n\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n\nOutro."
    cleaned = strip_embedded_video_urls(body)
    assert "youtube.com" not in cleaned
    assert "Intro paragraph" in cleaned
    assert "Outro" in cleaned


def test_editorial_structure_issues_requires_source():
    issues = editorial_structure_issues(
        title="Wake park opens",
        lead="Short lead.",
        body="Para one.\n\nPara two.",
        source_name="",
        source_url="",
    )
    assert "source_name_missing" in issues
    assert "source_url_missing" in issues


def test_public_text_quality_enforce_editorial():
    issues = public_text_quality_issues(
        title="Ok title",
        excerpt="This excerpt is long enough for the card preview checks.",
        lead="Lead sentence.",
        body="Only one paragraph without a blank line break.",
        source_name="Wake",
        source_url="https://example.com",
        enforce_editorial=True,
    )
    assert "body_too_few_paragraphs" in issues
