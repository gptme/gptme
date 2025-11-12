"""Extractive compression using sentence selection."""

import re

from .compressor import CompressionResult, ContextCompressor
from .config import CompressionConfig


class ExtractiveSummarizer(ContextCompressor):
    """Extractive summarization via sentence selection."""

    def __init__(self, config: CompressionConfig):
        self.config = config
        self._embedder = None  # Lazy load

    def _get_embedder(self):
        """Lazy load sentence transformer model."""
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(self.config.embedding_model)
            except ImportError as e:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                ) from e
        return self._embedder

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        # Simple sentence splitting (could be improved with NLTK)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]

    def _preserve_structure(self, text: str) -> tuple[list[str], list[int]]:
        """
        Extract preservable elements (code blocks, headings) and their positions.

        Returns:
            Tuple of (preserved_elements, positions)
        """
        preserved = []
        positions = []

        if self.config.preserve_code:
            # Find code blocks
            for match in re.finditer(r"```[\s\S]*?```", text):
                preserved.append(match.group())
                positions.append(match.start())

        if self.config.preserve_headings:
            # Find markdown headings
            for match in re.finditer(r"^#{1,6}\s+.+$", text, re.MULTILINE):
                preserved.append(match.group())
                positions.append(match.start())

        return preserved, positions

    def _score_sentences(self, sentences: list[str], context: str = "") -> list[float]:
        """
        Score sentences by relevance to context.

        Args:
            sentences: List of sentences to score
            context: Current conversation context

        Returns:
            List of relevance scores (0.0-1.0)
        """
        if not context:
            # No context provided, score by length (longer = more important)
            max_len = max(len(s) for s in sentences) if sentences else 1
            return [len(s) / max_len for s in sentences]

        # Use embeddings for relevance scoring
        embedder = self._get_embedder()
        sentence_embeddings = embedder.encode(sentences)
        context_embedding = embedder.encode([context])[0]

        # Cosine similarity
        import numpy as np

        scores = []
        for sent_emb in sentence_embeddings:
            similarity = np.dot(sent_emb, context_embedding) / (
                np.linalg.norm(sent_emb) * np.linalg.norm(context_embedding)
            )
            scores.append(float(similarity))

        return scores

    def _select_sentences(
        self, sentences: list[str], scores: list[float], target_count: int
    ) -> list[int]:
        """
        Select top sentences based on scores.

        Args:
            sentences: List of sentences
            scores: Relevance scores
            target_count: Number of sentences to select

        Returns:
            List of selected sentence indices (maintains original order)
        """
        # Get top-k indices by score
        scored_indices = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        selected_indices = sorted([idx for idx, _ in scored_indices[:target_count]])
        return selected_indices

    def compress(
        self, content: str, target_ratio: float = 0.7, context: str = ""
    ) -> CompressionResult:
        """
        Compress content using extractive summarization.

        Args:
            content: Text to compress
            target_ratio: Target compression ratio (0.7 = retain 70%)
            context: Current conversation context

        Returns:
            CompressionResult with compressed text
        """
        original_length = len(content)

        # Skip compression for short content
        if original_length < self.config.min_section_length:
            return CompressionResult(
                compressed=content,
                original_length=original_length,
                compressed_length=original_length,
                compression_ratio=1.0,
            )

        # Split into sentences (preservation disabled for baseline)
        sentences = self._split_sentences(content)
        if not sentences:
            return CompressionResult(
                compressed=content,
                original_length=original_length,
                compressed_length=original_length,
                compression_ratio=1.0,
            )

        # Score sentences by relevance
        scores = self._score_sentences(sentences, context)

        # Calculate target sentence count
        target_count = max(1, int(len(sentences) * target_ratio))

        # Select top sentences
        selected_indices = self._select_sentences(sentences, scores, target_count)

        # Reconstruct compressed content
        compressed_sentences = [sentences[i] for i in selected_indices]
        compressed = " ".join(compressed_sentences)

        compressed_length = len(compressed)
        actual_ratio = (
            compressed_length / original_length if original_length > 0 else 1.0
        )

        return CompressionResult(
            compressed=compressed,
            original_length=original_length,
            compressed_length=compressed_length,
            compression_ratio=actual_ratio,
        )
