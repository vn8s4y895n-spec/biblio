"""Adaptateurs des sources en ligne.

Chaque module de recherche expose `NAME`, `LABEL` et `search(query) -> list[dict]` : des notices au format
interne (champs de `core.FIELDS` + `authors`, `identifiers`, `subjects`, et pour la recherche `is_oa`,
`oa_pdf_url`, `cited_by_count`), dans l'ordre de pertinence de la source. Ajouter une source = ajouter un module.
"""

import re
from dataclasses import dataclass

SEARCH_TTL = 24 * 3600  # les résultats de recherche sont resservis depuis le cache pendant un jour


@dataclass(frozen=True)
class Query:
    text: str  # telle que saisie ; la syntaxe PubMed avancée ([MeSH], [tiab], booléens) est acceptée
    limit: int = 25  # résultats demandés à chaque source
    year_from: int | None = None
    year_to: int | None = None
    mesh: tuple[str, ...] = ()

    @property
    def boolean(self) -> str:
        """Pour les sources qui comprennent AND / OR / NOT et les guillemets : sans les étiquettes [tiab]…"""
        return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", self.text)).strip()

    @property
    def plain(self) -> str:
        """Pour les sources en texte libre : sans opérateurs ni termes exclus par NOT."""
        text = re.sub(r'\bNOT\s+("[^"]*"|\([^)]*\)|\S+)', " ", self.boolean)
        text = re.sub(r"\b(AND|OR)\b|[()\"]", " ", text)
        return " ".join(dict.fromkeys(text.split()))  # sans doublons (requête assouplie : termes répétés)

    @property
    def years(self) -> tuple[int, int] | None:
        if self.year_from is None and self.year_to is None:
            return None
        return self.year_from or 1800, self.year_to or 2999
