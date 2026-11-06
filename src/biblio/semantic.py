"""Recherche sémantique locale : modèle bge-m3 (multilingue) exécuté sur la machine avec ONNX Runtime.
Aucun texte n'est envoyé à un tiers. Le modèle est téléchargé une fois depuis le dépôt officiel (version figée).

Les vecteurs des passages sont calculés en arrière-plan ; tant qu'un passage n'en a pas, seule la recherche plein
texte le trouve.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from . import db

log = logging.getLogger("biblio")

MODEL = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"  # version figée : résultats reproductibles
DIM = 1024
MAX_TOKENS = 512  # un passage fait ~1 200 caractères, soit ~300 jetons
PRESELECT = 400  # candidats retenus par la distance de Hamming avant le reclassement exact
READY_MARK = "biblio-ready"


class Embedder:
    """bge-m3 : vecteur dense = jeton [CLS] de la dernière couche, normalisé (méthode de référence du modèle)."""

    name = MODEL

    def __init__(self, folder: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(folder / "onnx" / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(folder / "onnx" / "model.onnx"), options, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.lock = threading.Lock()

    def encode(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            batch = self.tokenizer.encode_batch(texts)
        feed = {"input_ids": np.array([e.ids for e in batch], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in batch], dtype=np.int64)}  # fmt: skip
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
        hidden = self.session.run(None, feed)[0]
        vectors = hidden[:, 0, :] if hidden.ndim == 3 else hidden  # [CLS], sauf si le graphe le fait déjà
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True).clip(1e-12)


# --- Modèle : téléchargement et chargement -----------------------------------------------------------------

_embedder: Embedder | None = None
_state = {"status": "absent", "error": None}
_lock = threading.Lock()


def model_folder(models_dir: Path) -> Path:
    return models_dir / "bge-m3"


def is_downloaded(models_dir: Path) -> bool:
    return (model_folder(models_dir) / READY_MARK).exists()


def download(models_dir: Path) -> None:
    """Télécharge le modèle ONNX officiel (~2,3 Go). Reprend un téléchargement interrompu."""
    from huggingface_hub import snapshot_download

    _state.update(status="downloading", error=None)
    try:
        folder = model_folder(models_dir)
        snapshot_download(MODEL, revision=REVISION, allow_patterns=["onnx/*"], local_dir=folder)
        (folder / READY_MARK).write_text(f"{MODEL}@{REVISION}\n")
        _state.update(status="ready")
    except Exception as exc:  # réseau, disque plein… : on le dit, on réessaiera au prochain démarrage
        log.warning("Téléchargement du modèle sémantique impossible : %s", exc)
        _state.update(status="error", error=str(exc))


def start_download(models_dir: Path) -> bool:
    """Lance le téléchargement en arrière-plan, sauf s'il est déjà fait ou en cours. Renvoie True s'il démarre."""
    with _lock:
        if _state["status"] == "downloading" or is_downloaded(models_dir):
            return False
        _state.update(status="downloading", error=None)
    threading.Thread(target=download, args=(models_dir,), daemon=True, name="model-download").start()
    return True


def set_embedder(embedder) -> None:
    """Utilisé par les tests (modèle simulé) et par `load`."""
    global _embedder
    _embedder = embedder
    _state.update(status="ready" if embedder else "absent")


def load(models_dir: Path) -> object | None:
    """Charge le modèle s'il est téléchargé (une fois par processus, ~2 Go de mémoire)."""
    with _lock:
        if _embedder is None and is_downloaded(models_dir):
            try:
                set_embedder(Embedder(model_folder(models_dir)))
            except Exception as exc:
                _state.update(status="error", error=f"Modèle illisible : {exc}")
        return _embedder


def embedder():
    return _embedder


def status(conn: sqlite3.Connection, models_dir: Path) -> dict:
    total = conn.execute("SELECT count(*) FROM passages").fetchone()[0]
    done = conn.execute("SELECT count(*) FROM passage_vectors").fetchone()[0]
    folder = model_folder(models_dir)
    downloaded = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0
    state = _state["status"]
    if state == "absent" and is_downloaded(models_dir):
        state = "downloaded"  # présent sur le disque, pas encore chargé
    return {"model": MODEL, "revision": REVISION, "status": state, "error": _state["error"],
            "downloaded_bytes": downloaded, "expected_bytes": 2_290_000_000, "passages": total,
            "indexed": done, "progress": round(done / total, 4) if total else 1.0}  # fmt: skip


# --- Index ---------------------------------------------------------------------------------------------------

# Les métadonnées et résumés d'abord : la recherche sur toute la bibliothèque devient utile au plus vite.
_BACKLOG = """SELECT p.id, p.text FROM passages p LEFT JOIN passage_vectors v ON v.passage_id = p.id
              WHERE v.passage_id IS NULL
              ORDER BY CASE p.origin WHEN 'metadata' THEN 0 WHEN 'abstract' THEN 1 WHEN 'note' THEN 2 ELSE 3 END, p.id
              LIMIT ?"""


def index_batch(conn: sqlite3.Connection, model, size: int = 16) -> int:
    """Calcule les vecteurs d'un lot de passages qui n'en ont pas. Renvoie le nombre traité (0 : à jour)."""
    rows = conn.execute(_BACKLOG, (size,)).fetchall()
    if not rows:
        return 0
    vectors = model.encode([r["text"] for r in rows]).astype(np.float32)
    with db.transaction(conn):
        for row, vector in zip(rows, vectors):
            if not conn.execute("SELECT 1 FROM passages WHERE id = ? AND text = ?", (row["id"], row["text"])).fetchone():
                continue  # passage supprimé ou modifié entre-temps : il sera repris
            conn.execute("DELETE FROM passage_bits WHERE rowid = ?", (row["id"],))
            conn.execute("INSERT INTO passage_bits (rowid, embedding) VALUES (?, vec_quantize_binary(?))", (row["id"], vector.tobytes()))
            conn.execute("INSERT OR REPLACE INTO passage_vectors (passage_id, model, vector) VALUES (?, ?, ?)",
                         (row["id"], model.name, vector.astype(np.float16).tobytes()))  # fmt: skip
    return len(rows)


def run_indexer(db_path: Path, models_dir: Path, stop: threading.Event | None = None) -> None:
    """Boucle de fond : charge le modèle quand il est là, puis indexe les passages au fil de l'eau."""
    # ponytail: un seul thread d'indexation ; plusieurs processus si l'indexation initiale s'avère trop lente.
    while not (stop and stop.is_set()):
        model = load(models_dir)
        if model is None:
            time.sleep(10)
            continue
        conn = db.connect(db_path)
        try:
            while index_batch(conn, model):
                if stop and stop.is_set():
                    return
        except Exception:
            log.exception("Indexation sémantique : erreur")
        finally:
            conn.close()
        time.sleep(2)


# --- Recherche -----------------------------------------------------------------------------------------------


def nearest(conn: sqlite3.Connection, query: np.ndarray, k: int = 200) -> list[tuple[int, float]]:
    """Passages les plus proches d'un vecteur : présélection binaire (Hamming), puis cosinus exact. [(id, score)]"""
    query = np.asarray(query, dtype=np.float32)
    rows = conn.execute(
        "SELECT rowid FROM passage_bits WHERE embedding MATCH vec_quantize_binary(?) AND k = ?", (query.tobytes(), PRESELECT)
    ).fetchall()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    found = conn.execute(f"SELECT passage_id, vector FROM passage_vectors WHERE passage_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    matrix = np.frombuffer(b"".join(r[1] for r in found), dtype=np.float16).reshape(len(found), DIM).astype(np.float32)
    scores = matrix @ query
    order = np.argsort(-scores)[:k]
    return [(found[i][0], float(scores[i])) for i in order]


def similarity(model, query: str, texts: list[str]) -> list[float]:
    """Cosinus entre une requête et des textes (reclassement des résultats en ligne)."""
    vectors = model.encode([query, *texts])
    return [float(s) for s in vectors[1:] @ vectors[0]]
