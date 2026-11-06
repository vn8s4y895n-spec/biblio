-- 0006 : vecteurs sémantiques des passages (modèle bge-m3, 1024 dimensions).
-- Présélection rapide sur les vecteurs binaires (distance de Hamming, sqlite-vec), puis reclassement exact sur les
-- vecteurs float16. Un passage sans vecteur attend d'être traité par l'indexation de fond.

CREATE VIRTUAL TABLE passage_bits USING vec0 (embedding bit[1024]);  -- rowid = passages.id

CREATE TABLE passage_vectors (
    passage_id INTEGER PRIMARY KEY REFERENCES passages (id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    vector     BLOB NOT NULL      -- 1024 × float16, normalisé (produit scalaire = cosinus)
);

CREATE TRIGGER passages_vectors_ad AFTER DELETE ON passages BEGIN
    DELETE FROM passage_bits WHERE rowid = old.id;
END;
CREATE TRIGGER passages_vectors_au AFTER UPDATE OF text ON passages BEGIN
    DELETE FROM passage_bits WHERE rowid = old.id;
    DELETE FROM passage_vectors WHERE passage_id = old.id;
END;
