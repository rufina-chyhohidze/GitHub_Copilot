"""OpenAI embeddings with full-input token counting, bounded retries, and safe errors."""

import asyncio
import os
from collections.abc import Sequence

import httpx
import tiktoken

from app.config import PROJECT_ROOT, Settings
from app.models.contracts import UsageLimits
from app.providers.interfaces import EmbeddingBatch


class OpenAIEmbeddings:
    provider = "openai"

    def __init__(self, settings: Settings, *, transport=None, tokenizer=None):
        settings.require_embeddings()
        self.model_id = settings.embedding_model_id
        self.dimensions = settings.embedding_dimensions
        self.version = settings.embedding_model_version
        self.key = settings.provider_api_key
        self.transport = transport
        self._tokenizer = tokenizer
        if self.model_id not in {"text-embedding-3-small", "text-embedding-3-large"}:
            raise ValueError(
                "This adapter supports text-embedding-3-small or text-embedding-3-large"
            )
        maximum = 1536 if self.model_id.endswith("small") else 3072
        if not 1 <= self.dimensions <= maximum:
            raise ValueError(f"Embedding dimensions must be between 1 and {maximum}")

    def count_tokens(self, text: str) -> int:
        if self._tokenizer is None:
            os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(PROJECT_ROOT / ".data/tiktoken"))
            try:
                self._tokenizer = tiktoken.get_encoding("cl100k_base")
            except Exception:
                raise ValueError(
                    "Tokenizer unavailable; its first download requires network access"
                ) from None
        return len(self._tokenizer.encode(text, disallowed_special=()))

    async def embed(self, texts: Sequence[str], *, limits: UsageLimits) -> EmbeddingBatch:
        if not texts or len(texts) > 128 or any(not text.strip() for text in texts):
            raise ValueError("Embedding batch must contain 1–128 nonempty inputs")
        counts = [self.count_tokens(text) for text in texts]
        if max(counts) > 8191 or sum(counts) > min(limits.max_context_tokens, 300000):
            raise ValueError(
                "Embedding input exceeds token limits; reparse with a smaller chunk budget"
            )
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=min(30, limits.timeout_seconds),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    for attempt in range(3):
                        try:
                            response = await client.post(
                                "https://api.openai.com/v1/embeddings",
                                headers={"Authorization": f"Bearer {self.key.get_secret_value()}"},
                                json={
                                    "model": self.model_id,
                                    "dimensions": self.dimensions,
                                    "input": list(texts),
                                    "encoding_format": "float",
                                },
                            )
                        except httpx.TransportError:
                            if attempt == 2:
                                raise ValueError(
                                    "Embedding service unavailable after bounded retries"
                                ) from None
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt < 2:
                                await asyncio.sleep(0.5 * (2**attempt))
                                continue
                        if response.status_code != 200:
                            raise ValueError(
                                f"Embedding request failed (HTTP {response.status_code}); "
                                "check API access and limits"
                            )
                        try:
                            data = response.json()
                            rows = sorted(data["data"], key=lambda row: row["index"])
                            if [row["index"] for row in rows] != list(range(len(texts))):
                                raise ValueError
                            if data["model"] != self.model_id:
                                raise ValueError
                            batch = EmbeddingBatch(
                                model_id=data["model"],
                                dimensions=self.dimensions,
                                vectors=tuple(tuple(row["embedding"]) for row in rows),
                                input_tokens=data["usage"]["total_tokens"],
                            )
                            if any(
                                not any(value != 0 for value in vector) for vector in batch.vectors
                            ):
                                raise ValueError
                            return batch
                        except (KeyError, TypeError, ValueError):
                            raise ValueError(
                                "Embedding service returned an invalid response"
                            ) from None
        except TimeoutError:
            raise ValueError("Embedding request exceeded its time limit") from None
