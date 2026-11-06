import os
import re
import threading
import zlib
from pathlib import Path

import numpy as np
import pytest
from conftest import MERINO_DOI

from biblio import core, library, search, semantic

PM_QUERY = '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'


class FakeEmbedder:
    """Modèle simulé, déterministe : sac de mots haché sur 1024 dimensions, avec quelques équivalences
    français → anglais, pour vérifier qu'une requête sans mot commun trouve le bon passage."""

    name = "fake"
    SYNONYMS = {"blse": "esbl", "foyer": "household", "familial": "household", "famille": "household",
                "familles": "household", "mortalité": "mortality", "études": "study"}  # fmt: skip

    def encode(self, texts):
        out = np.zeros((len(texts), semantic.DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                word = self.SYNONYMS.get(word, word)
                if len(word) >= 4:
                    out[i, zlib.crc32(word.encode()) % semantic.DIM] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


@pytest.fixture
def model():
    fake = FakeEmbedder()
    semantic.set_embedder(fake)
    yield fake
    semantic.set_embedder(None)


def index_all(conn, model):
    while semantic.index_batch(conn, model, size=8):
        pass


def household(conn):
    work, _ = core.add_manual(conn, {"title": "Household transmission of ESBL Enterobacterales within families",
                                     "authors": ["Riccio, Marie"], "year": 2021})  # fmt: skip
    return work


def test_index_backlog_and_status(conn, cfg, model):
    core.add_by_identifier(conn, MERINO_DOI)
    household(conn)
    before = semantic.status(conn, cfg.models_dir)
    assert before["indexed"] == 0 and before["passages"] > 0 and before["status"] == "ready"
    index_all(conn, model)
    after = semantic.status(conn, cfg.models_dir)
    assert after["indexed"] == after["passages"] and after["progress"] == 1.0
    assert conn.execute("SELECT count(*) FROM passage_bits").fetchone()[0] == after["passages"]
    vector = conn.execute("SELECT vector FROM passage_vectors LIMIT 1").fetchone()[0]
    assert len(vector) == semantic.DIM * 2  # float16
    assert semantic.index_batch(conn, model) == 0


def test_vectors_follow_passages(conn, model):
    work = household(conn)
    index_all(conn, model)
    conn.execute("UPDATE works SET title = 'Household transmission of ESBL: updated title' WHERE id = ?", (work["id"],))
    library.index_work(conn, work["id"])  # passages remplacés : leurs vecteurs disparaissent avec eux
    assert conn.execute("SELECT count(*) FROM passage_vectors").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM passage_bits").fetchone()[0] == 0
    index_all(conn, model)
    conn.execute("DELETE FROM works WHERE id = ?", (work["id"],))
    assert conn.execute("SELECT count(*) FROM passage_bits").fetchone()[0] == 0


def test_nearest_ranks_by_cosine(conn, model):
    core.add_by_identifier(conn, MERINO_DOI)
    target = household(conn)
    index_all(conn, model)
    hits = semantic.nearest(conn, model.encode(["BLSE dans les familles"])[0], k=5)
    first = conn.execute("SELECT work_id FROM passages WHERE id = ?", (hits[0][0],)).fetchone()[0]
    assert first == target["id"] and hits[0][1] > hits[-1][1]


def test_hybrid_finds_meaning_without_shared_words(conn, model):
    core.add_by_identifier(conn, MERINO_DOI)
    target = household(conn)
    index_all(conn, model)
    # Aucun de ces mots n'est dans les notices : le plein texte seul ne trouve rien.
    assert library.search(conn, "BLSE foyer", engine="lexical")["results"] == []
    found = library.search(conn, "BLSE foyer")
    assert found["engine"] == "hybrid" and found["results"][0]["id"] == target["id"]
    assert found["results"][0]["passages"][0]["match"] == "semantic"
    assert "<mark>" not in found["results"][0]["passages"][0]["snippet"]
    # Les deux moteurs d'accord : le passage est marqué « both » et passe devant.
    both = library.search(conn, "household transmission ESBL")
    assert both["results"][0]["id"] == target["id"] and both["results"][0]["passages"][0]["match"] == "both"
    assert library.search(conn, "BLSE foyer", engine="semantic")["results"][0]["id"] == target["id"]


def test_unrelated_query_returns_nothing(conn, model):
    household(conn)
    index_all(conn, model)
    assert library.search(conn, "zygomycose cutanée")["results"] == []


def test_without_model_search_stays_lexical(conn):
    household(conn)
    found = library.search(conn, "household transmission")
    assert found["engine"] == "lexical" and found["results"]
    with pytest.raises(ValueError, match="indisponible"):
        library.search(conn, "household", engine="semantic")
    with pytest.raises(ValueError):
        library.search(conn, "household", engine="magique")


def test_online_results_are_reranked(conn, model):
    plain = search.search(conn, PM_QUERY, per_source=5, filters=search.Filters(journal_status=[]), rerank=False)
    reranked = search.search(conn, PM_QUERY, per_source=5, filters=search.Filters(journal_status=[]))
    assert plain["reranked"] is False and reranked["reranked"] is True
    top = reranked["results"][: search.RERANK_TOP]
    assert all("semantic_score" in r for r in top)
    assert [r["score"] for r in top] == sorted((r["score"] for r in top), reverse=True)
    assert {r["identifiers"].get("doi") for r in top} == {r["identifiers"].get("doi") for r in plain["results"][: search.RERANK_TOP]}


def test_indexer_thread(conn, cfg, model):
    household(conn)
    stop = threading.Event()
    thread = threading.Thread(target=semantic.run_indexer, args=(cfg.db_path, cfg.models_dir, stop))
    thread.start()
    for _ in range(100):
        if semantic.status(conn, cfg.models_dir)["progress"] == 1.0:
            break
        threading.Event().wait(0.05)
    stop.set()
    thread.join(timeout=5)
    assert semantic.status(conn, cfg.models_dir)["progress"] == 1.0


def test_download_is_started_once(cfg, monkeypatch):
    started = []
    monkeypatch.setattr(semantic, "download", lambda folder: started.append(folder))
    monkeypatch.setitem(semantic._state, "status", "absent")
    assert semantic.start_download(cfg.models_dir) is True
    assert semantic.start_download(cfg.models_dir) is False  # déjà en cours
    semantic._state["status"] = "absent"
    (semantic.model_folder(cfg.models_dir)).mkdir(parents=True)
    (semantic.model_folder(cfg.models_dir) / semantic.READY_MARK).write_text("ok")
    assert semantic.start_download(cfg.models_dir) is False  # déjà téléchargé


def test_search_10k_hybrid_is_fast(conn, model):
    """Objectif du plan : < 200 ms sur 10 000 notices (encodage de la requête compris, ici simulé)."""
    import time

    rng = np.random.default_rng(0)
    rows = [(f"k{i}", f"Title {i} household transmission number {i}", "f") for i in range(10_000)]
    with conn:
        conn.executemany("INSERT INTO works (citekey, title, fingerprint) VALUES (?, ?, ?)", rows)
        conn.execute("INSERT INTO passages (work_id, origin, ord, text) SELECT id, 'metadata', 0, title FROM works")
        ids = [r[0] for r in conn.execute("SELECT id FROM passages")]
        vectors = rng.standard_normal((len(ids), semantic.DIM)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        conn.executemany("INSERT INTO passage_bits (rowid, embedding) VALUES (?, vec_quantize_binary(?))",
                         [(i, v.tobytes()) for i, v in zip(ids, vectors)])  # fmt: skip
        conn.executemany("INSERT INTO passage_vectors (passage_id, model, vector) VALUES (?, 'fake', ?)",
                         [(i, v.astype(np.float16).tobytes()) for i, v in zip(ids, vectors)])  # fmt: skip
    start = time.perf_counter()
    found = library.search(conn, "household transmission 4242")
    assert found["engine"] == "hybrid" and found["results"][0]["citekey"] == "k4242"
    assert time.perf_counter() - start < 0.2


MODEL_DIR = os.environ.get("BIBLIO_TEST_MODELS")


@pytest.mark.skipif(not MODEL_DIR, reason="vrai modèle bge-m3 absent (BIBLIO_TEST_MODELS=<dossier des modèles>)")
def test_real_model_is_cross_lingual():
    model = semantic.Embedder(semantic.model_folder(Path(MODEL_DIR)))
    scores = semantic.similarity(model, "études sur la transmission de BLSE en foyer familial", [
        "Household transmission of extended-spectrum beta-lactamase-producing Enterobacterales in families.",
        "Piperacillin-tazobactam versus meropenem for bloodstream infection: a randomized trial.",
    ])  # fmt: skip
    assert scores[0] > library.SEMANTIC_MIN > scores[1]


def test_partial_word_match_weighs_less_than_meaning(conn, model):
    flu, _ = core.add_manual(conn, {"title": "Transmission of influenza in schools", "year": 2019})
    esbl = household(conn)
    index_all(conn, model)
    found = library.search(conn, "BLSE foyer transmission")
    assert found["mode"] == "any"  # « BLSE » et « foyer » ne sont dans aucun texte
    assert [r["id"] for r in found["results"][:2]] == [esbl["id"], flu["id"]]
