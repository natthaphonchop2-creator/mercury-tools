"""Embedding providers."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

from mercury_tools.config import Settings


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings):
        if not settings.openai_configured:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings.")
        try:
            from openai import OpenAI
        except ModuleNotFoundError as error:
            if error.name == "openai":
                raise RuntimeError(
                    "Install mercury-tools[openai] to use OpenAI embeddings."
                ) from error
            raise
        self.client: Any = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
        )
        return [list(item.embedding) for item in response.data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class HashEmbeddingProvider:
    """Deterministic local embeddings for tests and offline smoke checks."""

    def __init__(self, dimensions: int = 1536):
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend(((byte / 127.5) - 1.0) for byte in digest)
            counter += 1
        vector = values[: self.dimensions]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def create_embedding_provider(
    settings: Settings,
    *,
    provider: str | None = None,
) -> EmbeddingProvider:
    selected = (provider or settings.embedding_provider).strip().lower()
    if selected == "openai":
        return OpenAIEmbeddingProvider(settings)
    if selected == "hash":
        return HashEmbeddingProvider(settings.embedding_dim)
    raise ValueError(f"Unsupported embedding provider: {selected}")
