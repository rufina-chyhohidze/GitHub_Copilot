"""Small interfaces let the pipeline change providers without changing repository tools."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field, model_validator

from app.models.contracts import Contract, UsageLimits


class Generation(Contract):
    text: str
    model_id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class EmbeddingBatch(Contract):
    model_id: str
    dimensions: int = Field(gt=0)
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent_dimensions(self) -> "EmbeddingBatch":
        import math

        if any(len(vector) != self.dimensions for vector in self.vectors):
            raise ValueError("Every vector must match the declared dimensions")
        if any(not math.isfinite(value) for vector in self.vectors for value in vector):
            raise ValueError("Embedding vectors must contain finite values")
        return self


class TextModel(Protocol):
    async def generate(self, prompt: str, *, limits: UsageLimits) -> Generation:
        """Adapters must enforce token and time limits and return measured usage."""
        ...


class EmbeddingModel(Protocol):
    async def embed(self, texts: Sequence[str], *, limits: UsageLimits) -> EmbeddingBatch:
        """Return one vector per input in input order, enforcing configured limits."""
        ...
