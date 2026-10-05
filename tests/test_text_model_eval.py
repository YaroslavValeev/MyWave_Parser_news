import json

import pytest

from scripts.evaluate_text_models import read_cases


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    return path


def test_eval_requires_real_source_text_and_representative_case_count(tmp_path):
    path = write_cases(
        tmp_path, [{"id": "pcm", "source_text": "Реальная статья о PCM."}]
    )
    assert len(read_cases(path, 1)) == 1
    with pytest.raises(ValueError, match="100 cases"):
        read_cases(path)


@pytest.mark.parametrize(
    "cases",
    [
        [{"id": "pcm", "source_text": "https://wakeflot.ru/news/1785"}],
        [{"id": "pcm", "source_text": ""}],
        [{"source_text": "Текст статьи"}],
        [{"id": "pcm", "source_text": "Текст статьи"}] * 2,
    ],
)
def test_invalid_corpus_is_rejected_before_api(tmp_path, cases):
    with pytest.raises(ValueError):
        read_cases(write_cases(tmp_path, cases), 1)
