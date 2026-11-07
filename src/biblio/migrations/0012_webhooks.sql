-- 0012 : webhooks. Chaque événement choisi (notice ajoutée, synthèse enregistrée, alerte de veille) est envoyé en
-- POST JSON à l'adresse, signé par HMAC-SHA256 avec le secret (en-tête X-Biblio-Signature: sha256=<hex>).

CREATE TABLE webhooks (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL,
    events_json TEXT NOT NULL,   -- ["work.created", "search.saved", "alert.created"]
    secret      TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    last_at     TEXT,            -- dernier envoi : date, statut HTTP ou erreur
    last_status INTEGER,
    last_error  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
