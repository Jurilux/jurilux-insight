from ingest.chunking import chunk_text


def test_empty():
    assert chunk_text("") == []


def test_short_text_single_chunk():
    assert chunk_text("Bonjour le monde.") == ["Bonjour le monde."]


def test_long_text_overlap_and_coverage():
    text = ("La faute grave rend impossible le maintien de la relation de travail. " * 60).strip()
    chunks = chunk_text(text, size=500, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)
    # aucune perte : chaque chunk est bien un extrait du texte source
    assert all(c in text for c in chunks)
    # le dernier chunk atteint la fin du texte
    assert text.endswith(chunks[-1][-40:])
