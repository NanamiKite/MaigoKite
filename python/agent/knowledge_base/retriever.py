"""Optional retrieval adapters using public LangChain retriever contracts.

These adapters accept already configured SDK stores/retrievers. They do not
provision an index or choose a user's document corpus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import AIMessage
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore
from langchain_core.vectorstores import VectorStoreRetriever as LCVectorStoreRetriever


class RetrievalResult:
    """A retrieved document; absent SDK scores stay unknown instead of becoming zero."""

    def __init__(self, document: Document, score: float | None = None) -> None:
        self.document = document
        self.score = score


class IRetriever(ABC):
    """Query documents; None for top_k uses the retriever's configured default."""

    @abstractmethod
    async def retrieve(
        self, query: str, top_k: int | None = None, filters: dict[str, Any] | None = None
    ) -> list[RetrievalResult]:
        ...

    @abstractmethod
    async def retrieve_by_id(self, doc_id: str) -> Document | None:
        ...


def _limit(top_k: int | None, default: int) -> int:
    value = default if top_k is None else top_k
    if value <= 0:
        raise ValueError("top_k must be positive")
    return value


async def _get_document(vector_store: VectorStore, doc_id: str) -> Document | None:
    # Unsupported ID lookup must propagate NotImplementedError, not pretend the ID is absent.
    documents = await vector_store.aget_by_ids([doc_id])
    return next((doc for doc in documents if doc.id == doc_id), None)


class VectorStoreRetriever(IRetriever):
    """Text retrieval over a LangChain VectorStore with its embedding backend configured."""

    def __init__(self, vector_store: VectorStore, top_k: int = 5, **kwargs: Any) -> None:
        self._vector_store = vector_store
        self._top_k = _limit(top_k, 5)
        self._lc_retriever = vector_store.as_retriever(
            search_type="similarity", search_kwargs={**kwargs, "k": self._top_k},
        )

    async def retrieve(
        self, query: str, top_k: int | None = None, filters: dict[str, Any] | None = None
    ) -> list[RetrievalResult]:
        limit = _limit(top_k, self._top_k)
        kwargs: dict[str, Any] = {"k": limit}
        if filters is not None:
            kwargs["filter"] = filters
        documents = await self._lc_retriever.ainvoke(query, **kwargs)
        return [RetrievalResult(document) for document in documents[:limit]]

    async def retrieve_by_id(self, doc_id: str) -> Document | None:
        return await _get_document(self._vector_store, doc_id)


class ContextualCompressionRetriever(IRetriever):
    """Apply the SDK's LLM extractor to an explicitly supplied base retriever.

    Filters are supported only for a vector-store retriever whose search kwargs
    can be scoped before any documents are sent to the compressor model.
    """

    def __init__(
        self,
        base_retriever: BaseRetriever,
        llm: BaseLanguageModel[AIMessage] | BaseLanguageModel[str],
        top_k: int = 20,
    ) -> None:
        try:
            from langchain_classic.retrievers import ContextualCompressionRetriever as LCCompressionRetriever
            from langchain_classic.retrievers.document_compressors import LLMChainExtractor
        except ImportError as exc:
            raise ImportError("Compression retrieval requires the optional vectorstores dependencies") from exc

        self._top_k = _limit(top_k, 20)
        if isinstance(base_retriever, LCVectorStoreRetriever):
            base_retriever = base_retriever.model_copy(
                update={"search_kwargs": {**base_retriever.search_kwargs, "k": self._top_k}},
            )
        self._base_retriever = base_retriever
        self._compression_retriever = LCCompressionRetriever(
            base_compressor=LLMChainExtractor.from_llm(llm), base_retriever=base_retriever,
        )

    async def retrieve(
        self, query: str, top_k: int | None = None, filters: dict[str, Any] | None = None
    ) -> list[RetrievalResult]:
        limit = _limit(top_k, self._top_k)
        retriever = self._compression_retriever
        base = self._base_retriever
        if filters is not None and not isinstance(base, LCVectorStoreRetriever):
            raise NotImplementedError("Metadata filtering is not implemented for this base retriever")
        if isinstance(base, LCVectorStoreRetriever):
            kwargs = {**base.search_kwargs, "k": max(limit, self._top_k)}
            if filters is not None:
                kwargs["filter"] = filters
            # Per-call copies keep concurrent queries from changing each other's scope.
            retriever = retriever.model_copy(
                update={"base_retriever": base.model_copy(update={"search_kwargs": kwargs})},
            )
        documents = await retriever.ainvoke(query)
        return [RetrievalResult(document) for document in documents[:limit]]

    async def retrieve_by_id(self, doc_id: str) -> Document | None:
        if not isinstance(self._base_retriever, LCVectorStoreRetriever):
            raise NotImplementedError("ID lookup is not implemented for this base retriever")
        return await _get_document(self._base_retriever.vectorstore, doc_id)


class EnsembleRetriever(IRetriever):
    """Fuse semantic and caller-provided keyword results using the SDK's RRF.

    A keyword retriever must be built from the intended corpus by the caller;
    a similarity search for a blank string cannot enumerate that corpus.
    """

    def __init__(
        self, vector_store: VectorStore, llm: BaseLanguageModel[AIMessage] | BaseLanguageModel[str] | None = None,
        top_k: int = 5, *, keyword_retriever: BaseRetriever | None = None,
    ) -> None:
        if llm is not None:
            raise ValueError("Configure embeddings on the vector store; this retriever does not use an llm")
        if keyword_retriever is None:
            raise NotImplementedError("Keyword index construction is not implemented; supply keyword_retriever")
        try:
            from langchain_classic.retrievers import EnsembleRetriever as LCEnsembleRetriever
        except ImportError as exc:
            raise ImportError("Ensemble retrieval requires the optional vectorstores dependencies") from exc

        self._vector_store = vector_store
        self._top_k = _limit(top_k, 5)
        self._keyword_retriever = keyword_retriever
        self._ensemble = LCEnsembleRetriever(
            retrievers=[
                vector_store.as_retriever(search_kwargs={"k": self._top_k * 2}),
                keyword_retriever,
            ],
            weights=[0.5, 0.5],
        )

    async def retrieve(
        self, query: str, top_k: int | None = None, filters: dict[str, Any] | None = None
    ) -> list[RetrievalResult]:
        limit = _limit(top_k, self._top_k)
        if filters is not None:
            # Filtering only the vector branch would leak unfiltered keyword results.
            raise NotImplementedError("Metadata filtering across both ensemble branches is not implemented")
        ensemble = self._ensemble.model_copy(update={
            "retrievers": [
                self._vector_store.as_retriever(search_kwargs={"k": limit * 2}),
                self._keyword_retriever,
            ],
        })
        documents = await ensemble.ainvoke(query)
        return [RetrievalResult(document) for document in documents[:limit]]

    async def retrieve_by_id(self, doc_id: str) -> Document | None:
        return await _get_document(self._vector_store, doc_id)
