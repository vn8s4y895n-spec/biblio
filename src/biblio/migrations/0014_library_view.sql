-- 0014 : rangement et affichage de la bibliothèque. Couleur et épingle des collections et tags, date de dernière
-- ouverture d'une notice (vue « Récemment ouverts »), vues enregistrées (cases cochées, filtres, tri, sous un nom).

ALTER TABLE labels ADD COLUMN color TEXT;                         -- red, orange, yellow, green, teal, blue, purple, gray
ALTER TABLE labels ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;  -- 1 : en cours, en haut ; les autres sous « Archives »
ALTER TABLE works ADD COLUMN opened_at TEXT;
CREATE INDEX works_opened ON works (opened_at) WHERE opened_at IS NOT NULL;

CREATE TABLE views (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    state_json TEXT NOT NULL,  -- état de l'écran Bibliothèque : vue, cases cochées, filtres, tri, affichage
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
