from functools import lru_cache

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def _modello():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_MODEL_NAME)


def vettorizza(testi: list[str], tipo: str = "passage") -> list[list[float]]:
    if not testi:
        return []

    if tipo == "query":
        testi = [f"query: {t}" for t in testi]
    else:
        testi = [f"passage: {t}" for t in testi]
    return _modello().encode(testi, normalize_embeddings=True, show_progress_bar=False).tolist()
