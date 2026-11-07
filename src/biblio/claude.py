"""Paquetage pour Claude (seule partie de Biblio propre à Claude) : extension Claude Desktop (.mcpb) et skill.

L'extension ne contient ni jeton ni clé : elle lance `biblio mcp`, qui lit config.toml sur l'ordinateur.
"""

import io
import json
import zipfile

from . import __version__, mcp_server
from .config import Config

SKILL = """---
name: biblio
description: Recherche bibliographique médicale sourcée avec Biblio (bibliothèque personnelle, PubMed, liste des revues recommandées). À utiliser pour toute question clinique ou scientifique en bactériologie, maladies infectieuses ou biologie médicale, pour chercher des articles, vérifier ce qui est déjà dans la bibliothèque, faire une revue rapide, ou produire une bibliographie Vancouver.
---

# Biblio

Biblio est la bibliothèque bibliographique de l'utilisateur, médecin biologiste. Ses outils MCP (serveur « biblio »)
cherchent dans les bases en ligne et dans sa bibliothèque, et enregistrent tes synthèses dans Biblio.

## Méthode

1. **Requête.** Traduis la question en requête PubMed en anglais : descripteurs MeSH (`"Enterococcus faecium"[MeSH]`)
   et synonymes en texte libre (`[tiab]`) reliés par OR, concepts reliés par AND. N'ajoute jamais une molécule ou un
   organisme absent de la question, même au nom proche.
2. **Recherche en ligne.** `search_online` avec cette requête et la question telle que l'utilisateur l'a posée. Par
   défaut, seules les revues de la liste des revues recommandées (CDD / CNU santé) sont montrées : garde ce filtre.
   N'utilise `all_journals: true` que si l'utilisateur le demande ou si rien n'est trouvé, et dis-le.
3. **Bibliothèque.** `search_library` : ce que l'utilisateur a déjà lu, ses PDF et ses notes comptent.
4. **Lecture.** Avant de citer un article, lis son résumé (`get_summary`) ou ses passages (`get_passages`, pour un
   article de la bibliothèque avec PDF). Cite des passages précis, avec la page quand elle est connue.
5. **Réponse.** Dans la langue de la question. Chaque phrase se termine par ses sources `[1]`, `[2]`, numérotées dans
   l'ordre où tu les passeras à `save_synthesis`. Donne le type d'étude et l'effectif quand ils comptent pour le
   niveau de preuve ; signale les résultats contradictoires. Rien qui ne soit dans les sources.
6. **Alertes.** Signale toute source rétractée (`retracted`) et tout preprint (`journal_status: preprint`, non relu
   par les pairs).
7. **Enregistrement.** Appelle `save_synthesis` (question, réponse, sources dans l'ordre des numéros, requête).
   Biblio vérifie chaque phrase contre les sources citées : corrige les phrases `flagged` et réenregistre.
8. **Suite.** Propose d'ajouter les articles utiles (`add_by_identifier`, avec la question) et une bibliographie
   Vancouver (`export_bibliography`). Ne modifie jamais la bibliothèque (ajout, tag, collection, note) sans
   l'accord de l'utilisateur.

## Autres demandes

- « Quoi de neuf ? » : `watch_alerts`, rétractations d'abord.
- Bibliographie d'une collection : `export_bibliography` avec `collection`, texte rendu tel quel.
- Envoyer des articles à un collègue : `create_package` (fichier .biblio dans Téléchargements).
"""


def skill_zip() -> bytes:
    """Skill à importer dans Claude (Réglages → Capacités → Skills) : un dossier biblio/ avec son SKILL.md."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("biblio/SKILL.md", SKILL)
    return buffer.getvalue()


def mcpb(cfg: Config) -> bytes:
    """Extension Claude Desktop (format MCP Bundle) : un double-clic l'installe. Elle lance `biblio mcp` par /bin/sh,
    toujours exécutable, plutôt que de compter sur les droits d'exécution d'un fichier extrait du zip."""
    command = mcp_server.connectors(cfg)["command"]
    script = f'#!/bin/sh\nexec "{command}" mcp\n'
    manifest = {
        "manifest_version": "0.3",
        "name": "biblio",
        "display_name": "Biblio",
        "version": __version__,
        "description": "Ta bibliothèque Biblio dans Claude : recherche PubMed filtrée par la liste des revues "
        "recommandées, bibliothèque et PDF, synthèses enregistrées dans Biblio.",
        "author": {"name": "Biblio"},
        "server": {"type": "binary", "entry_point": "server/biblio-mcp",
                   "mcp_config": {"command": "/bin/sh", "args": ["${__dirname}/server/biblio-mcp"], "env": {}}},
        "tools": [{"name": name} for name in mcp_server.connectors(cfg)["tools"]],
        "compatibility": {"platforms": ["darwin"]},
    }  # fmt: skip
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        info = zipfile.ZipInfo("server/biblio-mcp")
        info.external_attr = 0o755 << 16
        z.writestr(info, script)
    return buffer.getvalue()
