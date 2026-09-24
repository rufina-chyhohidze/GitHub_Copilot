from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.contracts import SourceSpan
from app.providers.interfaces import EmbeddingBatch


def span(**overrides):
    values = dict(
        snapshot_id=uuid4(), file_id=uuid4(), path="src/auth.py", start_line=1, end_line=8
    )
    return SourceSpan(**(values | overrides))


@pytest.mark.parametrize("path", ["../secret", "/tmp/file", "src/../../x", "a\\b", "", "."])
def test_rejects_unsafe_source_paths(path):
    with pytest.raises(ValidationError):
        span(path=path)


@pytest.mark.parametrize("start,end", [(0, 1), (8, 2)])
def test_rejects_invalid_citation_ranges(start, end):
    with pytest.raises(ValidationError):
        span(start_line=start, end_line=end)


def test_preserves_single_line_citation():
    source = span(start_line=8, end_line=8)
    assert source.start_line == source.end_line == 8


@pytest.mark.parametrize("vectors", [((1.0,),), ((float("nan"), 0.0),)])
def test_rejects_invalid_embedding_vectors(vectors):
    with pytest.raises(ValidationError):
        EmbeddingBatch(model_id="fixture", dimensions=2, vectors=vectors, input_tokens=1)
