-- 0013 : favoris. Un favori reste en tête des listes et dans la vue « Sans statut », même rangé dans une collection.

ALTER TABLE works ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0;
CREATE INDEX works_favorite ON works (favorite) WHERE favorite = 1;
