-- 0015 : l'historique des questions garde le dernier résultat (sources, réponse, synthèse de l'IA), pour le
-- revoir sans relancer la recherche.

ALTER TABLE question_history ADD COLUMN result_json TEXT;
ALTER TABLE question_history ADD COLUMN synthesis_json TEXT;
