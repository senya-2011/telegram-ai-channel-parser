import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load the sentence-transformer model."""
    global _model
    if _model is None:
        logger.info("Loading sentence-transformers model (all-MiniLM-L6-v2)...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Model loaded successfully")
    return _model


def generate_embedding(text: str) -> Optional[list[float]]:
    """
    Generate a 384-dimensional embedding vector for the given text.
    Uses all-MiniLM-L6-v2 model (runs locally, no API needed).
    """
    try:
        model = _get_model()
        # Truncate to ~512 tokens worth of text
        if len(text) > 2000:
            text = text[:2000]
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.error(f"Embedding generation error: {e}")
        return None


def cosine_similarity(vec1, vec2) -> float:
    """Compute cosine similarity between two vectors (lists or numpy arrays)."""
    a = np.asarray(vec1, dtype=np.float32).flatten()
    b = np.asarray(vec2, dtype=np.float32).flatten()
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)
