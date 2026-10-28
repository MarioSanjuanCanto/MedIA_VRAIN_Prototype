import sys
from types import SimpleNamespace

from media_core.services.embeddings import MultilingualEmbedder


def test_embedder_uses_local_cache_before_downloading(monkeypatch):
    calls = []
    downloaded_model = object()

    def fake_sentence_transformer(model_name, **options):
        calls.append((model_name, options["local_files_only"]))
        if options["local_files_only"]:
            raise OSError("not cached")
        return downloaded_model

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=fake_sentence_transformer),
    )

    embedder = MultilingualEmbedder("test-model")

    assert embedder._get_model() is downloaded_model
    assert calls == [("test-model", True), ("test-model", False)]


def test_warm_up_reuses_model_for_queries(monkeypatch):
    calls = []
    model = object()

    def fake_sentence_transformer(model_name, **options):
        calls.append(model_name)
        return model

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=fake_sentence_transformer),
    )

    embedder = MultilingualEmbedder("test-model")
    embedder.warm_up()
    assert embedder._get_model() is model
    assert calls == ["test-model"]
