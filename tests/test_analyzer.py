"""analyzer 純函式測試（不需網路 / 金鑰）。"""

from __future__ import annotations

import pytest

from threads_outputer.analyzer import (
    AnalyzerError,
    _batch_posts,
    _build_attempts,
    _supports_custom_temperature,
    _BATCH_CHAR_LIMIT,
    _PER_POST_CHAR_LIMIT,
    generate_video_outlines,
)
from threads_outputer.models import Post


def _make_posts(n: int, text_len: int) -> list:
    return [Post(id=str(i), text="字" * text_len) for i in range(n)]


def test_batch_posts_splits_by_char_limit():
    # 每則 1000 字、共 20 則 = 20000 字，應切成多批
    posts = _make_posts(20, 1000)
    batches = _batch_posts(posts)
    assert len(batches) >= 2
    # 攤平後不漏文、順序不變
    flat = [p for b in batches for p in b]
    assert [p.id for p in flat] == [p.id for p in posts]


def test_batch_posts_respects_limit_per_batch():
    posts = _make_posts(20, 1000)
    for batch in _batch_posts(posts):
        used = sum(min(len(p.text), _PER_POST_CHAR_LIMIT) for p in batch)
        # 單批至多一則會造成略超過上限，故允許一則的寬限
        assert used <= _BATCH_CHAR_LIMIT + _PER_POST_CHAR_LIMIT


def test_batch_posts_single_batch_when_small():
    posts = _make_posts(3, 100)
    assert len(_batch_posts(posts)) == 1


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "o1", "o3-mini", "o4-mini"])
def test_reasoning_models_skip_temperature(model):
    assert _supports_custom_temperature(model) is False
    attempts = _build_attempts(model, 0.6)
    # 第一次就不帶 temperature，避免注定失敗的請求
    assert "temperature" not in attempts[0]


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1"])
def test_standard_models_use_temperature_first(model):
    assert _supports_custom_temperature(model) is True
    attempts = _build_attempts(model, 0.6)
    assert attempts[0].get("temperature") == 0.6


def test_generate_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AnalyzerError):
        generate_video_outlines("x", [Post(id="1", text="hi")], api_key=None)


def test_generate_requires_posts(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AnalyzerError):
        generate_video_outlines("x", [], api_key="sk-test")
