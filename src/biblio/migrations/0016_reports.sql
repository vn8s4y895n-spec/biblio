-- 0016 : signalements d'une réponse de l'écran Question (articles mal choisis, synthèse de l'IA fausse), gardés
-- pour corriger la recherche ensuite.

CREATE TABLE reports (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    question TEXT NOT NULL,
    reasons_json TEXT NOT NULL,  -- ["sources", "answer"]
    comment TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL  -- requête, sources (et celles jugées hors sujet), synthèse, modèle, version
);
