"""Round-robin чанки collect_sources."""
from __future__ import annotations

import pytest

from config.settings import config


def test_slice_sources_full_when_chunk_zero(monkeypatch, tmp_path):
    idx_file = tmp_path / ".collect_chunk_index"
    monkeypatch.setattr("core.scheduler._chunk_index_path", lambda: idx_file)
    monkeypatch.setattr(config, "COLLECT_SOURCES_CHUNK_SIZE", 0)
    from core.scheduler import _slice_sources_for_chunk

    src = [1, 2, 3, 4, 5]
    out, mode = _slice_sources_for_chunk(list(src))
    assert out == src
    assert mode == "full"


def test_slice_sources_chunk_rotates(monkeypatch, tmp_path):
    idx_file = tmp_path / ".collect_chunk_index"
    monkeypatch.setattr("core.scheduler._chunk_index_path", lambda: idx_file)
    monkeypatch.setattr(config, "COLLECT_SOURCES_CHUNK_SIZE", 2)
    from core.scheduler import _slice_sources_for_chunk

    s = list(range(5))
    a, m1 = _slice_sources_for_chunk(list(s))
    b, m2 = _slice_sources_for_chunk(list(s))
    c, m3 = _slice_sources_for_chunk(list(s))
    d, m4 = _slice_sources_for_chunk(list(s))
    assert a == [0, 1]
    assert b == [2, 3]
    assert c == [4]
    assert d == [0, 1]
    assert "chunk" in m1
