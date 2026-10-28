import os
from threading import RLock
from functools import wraps

def locked(fn):
    @wraps(fn)
    def run(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return run


from media_core.config import EMBEDDING_CACHE_DIR, EMBEDDING_MODEL

os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"


class MultilingualEmbedder:
    """Reuse one Sentence Transformers model for normalized embeddings."""

    def __init__(self, model_name: str = EMBEDDING_MODEL) -> None:
        self.model_name = model_name
        self._model = None
        self._lock = RLock()

    @locked
    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            model_options = {
                "cache_folder": str(EMBEDDING_CACHE_DIR),
            }
            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    local_files_only=True,
                    **model_options,
                )
            except OSError:
                # First use: fetch the model. Later processes remain fully local.
                self._model = SentenceTransformer(
                    self.model_name,
                    local_files_only=False,
                    **model_options,
                )
        return self._model

    def warm_up(self) -> None:
        """Load the model before the first search or indexing request."""
        self._get_model()

    @locked
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed transcription chunks in batches."""
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    @locked
    def embed_query(self, text: str) -> list[float]:
        """Embed one natural-language search query."""
        vector = self._get_model().encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector[0].tolist()
