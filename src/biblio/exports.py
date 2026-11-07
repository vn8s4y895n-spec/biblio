"""Citations (styles CSL) et exports : CSL-JSON, BibTeX, RIS, Markdown, `library.bib`.

Le CSL-JSON est produit à partir des colonnes de la base (pas de copie stockée) ; les autres formats en dérivent.
Styles : ceux fournis (`data/styles/`) et ceux ajoutés par l'utilisateur (`styles/` du dossier de données).
"""

import re
import sqlite3
import warnings
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from . import core, db
from .formats import BIBTEX_TYPES, RIS_TYPES

DEFAULT_STYLE = "bibliotheque-universitaire-de-medecine-vancouver"
EXPORT_FORMATS = ("csljson", "bibtex", "ris", "markdown")
BIB_TYPES = {csl: bib for bib, csl in reversed(BIBTEX_TYPES.items())} | {"article": "article", "manuscript": "unpublished"}
RIS_OUT = {csl: ris for ris, csl in reversed(RIS_TYPES.items())} | {"article": "JOUR"}


class StyleError(ValueError):
    pass


# --- CSL-JSON ------------------------------------------------------------------------------------


def to_csl(work: dict) -> dict:
    """Notice (core.get_work) → élément CSL-JSON."""
    ids = work.get("identifiers") or {}
    parts = [int(p) for p in (work.get("issued") or "").split("-") if p.isdigit()] or ([work["year"]] if work.get("year") else [])
    item = {
        "id": work.get("citekey") or "item", "type": work.get("csl_type") or "article-journal", "title": work["title"],
        "author": [{"family": a["family"], **({"given": a["given"]} if a.get("given") else {})} for a in work.get("authors", [])],
        "container-title": work.get("container_title"), "container-title-short": work.get("container_abbrev"),
        "volume": work.get("volume"), "issue": work.get("issue"), "page": work.get("pages"),
        "issued": {"date-parts": [parts]} if parts else None, "abstract": work.get("abstract"),
        "language": work.get("language"), "DOI": ids.get("doi"), "PMID": ids.get("pmid"), "PMCID": ids.get("pmcid"),
        "ISSN": work.get("issn_print") or work.get("issn_e"), "URL": ids.get("url"),
        "keyword": ", ".join(s["term"] for s in work.get("subjects", [])) or None,
    }  # fmt: skip
    return {k: v for k, v in item.items() if v not in (None, "", [])}


# --- Styles et citations --------------------------------------------------------------------------


def style_paths(home: Path) -> dict[str, Path]:
    """Styles disponibles : identifiant (nom du fichier) → chemin. Un style ajouté remplace un style fourni."""
    bundled = files("biblio") / "data" / "styles"
    found = {p.stem: Path(str(p)) for p in bundled.iterdir() if p.name.endswith(".csl")}
    found |= {p.stem: p for p in sorted((home / "styles").glob("*.csl"))}
    return found


def styles(home: Path) -> list[dict]:
    out = []
    for key, path in style_paths(home).items():
        title = re.search(r"<title>([^<]+)</title>", path.read_text(encoding="utf-8", errors="replace"))
        out.append({"id": key, "title": title.group(1) if title else key, "default": key == DEFAULT_STYLE,
                    "added": not str(path).startswith(str(files("biblio")))})  # fmt: skip
    return sorted(out, key=lambda s: (not s["default"], s["title"].lower()))


@lru_cache(maxsize=16)
def _load_style(path: str, mtime: float):
    from citeproc import CitationStylesStyle

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return CitationStylesStyle(path, validate=False)


def add_style(home: Path, data: bytes, filename: str | None) -> dict:
    """Ajoute un fichier .csl (vérifié en formatant une notice d'essai)."""
    text = data.decode("utf-8", errors="replace")
    if "<style" not in text or "citation-style-language" not in text:
        raise StyleError("Ce fichier n'est pas un style CSL (.csl)")
    if 'rel="independent-parent"' in text:
        raise StyleError("Style dépendant : ajouter plutôt le style parent qu'il désigne (lien « independent-parent »)")
    key = re.sub(r"[^a-z0-9-]+", "-", Path(filename or "style").stem.lower()).strip("-") or "style"
    folder = home / "styles"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{key}.csl"
    path.write_text(text, encoding="utf-8")
    try:
        bibliography([{"id": "essai", "type": "article-journal", "title": "Essai", "author": [{"family": "Essai"}],
                       "issued": {"date-parts": [[2020]]}}], home, key)  # fmt: skip
    except Exception as exc:
        path.unlink()
        raise StyleError(f"Style illisible : {exc}") from exc
    return next(s for s in styles(home) if s["id"] == key)


def bibliography(items: list[dict], home: Path, style: str = DEFAULT_STYLE, html: bool = False) -> list[str]:
    """Références formatées, dans l'ordre des éléments (numérotées par les styles numériques)."""
    from citeproc import Citation, CitationItem, CitationStylesBibliography, formatter
    from citeproc.source.json import CiteProcJSON

    path = style_paths(home).get(style)
    if path is None:
        raise StyleError(f"Style inconnu : {style}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bib = CitationStylesBibliography(_load_style(str(path), path.stat().st_mtime), CiteProcJSON(items),
                                         formatter.html if html else formatter.plain)  # fmt: skip
        for item in items:
            bib.register(Citation([CitationItem(item["id"])]))
        return [str(entry) for entry in bib.bibliography()]


def citation(work: dict, home: Path, style: str = DEFAULT_STYLE) -> str:
    """Référence seule, sans numéro (à coller dans un texte)."""
    return re.sub(r"^\s*\[?\d+[.\]]\s*", "", bibliography([to_csl(work)], home, style)[0])


# --- BibTeX, RIS, Markdown --------------------------------------------------------------------------


def _bib_escape(text: str) -> str:
    return re.sub(r"([&%$#_])", r"\\\1", str(text)).replace("{", r"\{").replace("}", r"\}")


def bibtex(works: list[dict]) -> str:
    out = []
    for w in works:
        ids = w["identifiers"]
        fields = {
            "title": w["title"],
            # Auteur collectif entre accolades : son « and » ne sépare pas deux auteurs.
            "author": " and ".join(f"{_bib_escape(a['family'])}, {_bib_escape(a['given'])}" if a.get("given")
                                   else f"{{{_bib_escape(a['family'])}}}" for a in w["authors"]),  # fmt: skip
            "journal": w.get("container_title"), "shortjournal": w.get("container_abbrev"), "year": w.get("year"),
            "volume": w.get("volume"), "number": w.get("issue"), "pages": (w.get("pages") or "").replace("-", "--") or None,
            "doi": ids.get("doi"), "pmid": ids.get("pmid"), "pmcid": ids.get("pmcid"),
            "issn": ", ".join(i for i in (w.get("issn_print"), w.get("issn_e")) if i) or None,
            "language": w.get("language"), "abstract": w.get("abstract"),
            "keywords": ", ".join(s["term"] for s in w.get("subjects", [])) or None,
        }  # fmt: skip
        if ids.get("arxiv"):
            fields |= {"eprint": ids["arxiv"], "archiveprefix": "arXiv"}
        body = ",\n".join(f"  {k} = {{{v if k in ('doi', 'pmcid', 'author') else _bib_escape(v)}}}" for k, v in fields.items() if v)
        out.append(f"@{BIB_TYPES.get(w.get('csl_type'), 'misc')}{{{w['citekey']},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def ris(works: list[dict]) -> str:
    out = []
    for w in works:
        ids = w["identifiers"]
        start, _, end = (w.get("pages") or "").partition("-")
        lines = [("TY", RIS_OUT.get(w.get("csl_type"), "GEN"))]
        # Un auteur collectif garde sa virgule finale : sinon il serait lu « Prénom Nom ».
        lines += [("AU", f"{a['family']}, {a.get('given') or ''}".rstrip()) for a in w["authors"]]
        lines += [("TI", w["title"]), ("T2", w.get("container_title")), ("J2", w.get("container_abbrev")),
                  ("PY", w.get("year")), ("DA", (w.get("issued") or "").replace("-", "/") or None), ("VL", w.get("volume")),
                  ("IS", w.get("issue")), ("SP", start or None), ("EP", end or None), ("DO", ids.get("doi")),
                  ("AN", ids.get("pmid")), ("SN", w.get("issn_print")), ("SN", w.get("issn_e")), ("LA", w.get("language")),
                  ("AB", w.get("abstract")), ("ID", w["citekey"])]  # fmt: skip
        lines += [("KW", s["term"]) for s in w.get("subjects", [])]
        out.append("\n".join(f"{tag}  - {str(v).replace(chr(10), ' ')}" for tag, v in lines if v) + "\nER  - ")
    return "\n\n".join(out) + "\n"


def markdown(works: list[dict], home: Path, style: str = DEFAULT_STYLE) -> str:
    """Liste formatée, avec un lien DOI et la clé de citation (lien [[clé]] vers la note, façon Obsidian)."""
    lines = []
    for n, w in enumerate(works, 1):  # une à une : un style auteur-date trierait la liste autrement
        text = citation(w, home, style)
        link = f" [doi](https://doi.org/{w['identifiers']['doi']})" if w["identifiers"].get("doi") else ""
        lines.append(f"{n}. {text}{link} [[{w['citekey']}]]")
    return "\n".join(lines) + "\n"


def export(conn: sqlite3.Connection, home: Path, refs: list, fmt: str, style: str = DEFAULT_STYLE) -> tuple[str, str]:
    """Exporte les notices désignées. Renvoie (contenu, type MIME)."""
    import json

    works = [core.get_work(conn, ref) for ref in refs]
    if fmt == "csljson":
        return json.dumps([to_csl(w) for w in works], ensure_ascii=False, indent=2), "application/vnd.citationstyles.csl+json"
    if fmt == "bibtex":
        return bibtex(works), "application/x-bibtex"
    if fmt == "ris":
        return ris(works), "application/x-research-info-systems"
    if fmt == "markdown":
        return markdown(works, home, style), "text/markdown"
    raise ValueError(f"Format d'export inconnu : {fmt}")


def all_refs(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM works ORDER BY citekey")]


def sync_library_bib(conn: sqlite3.Connection, home: Path) -> bool:
    """Réécrit `library.bib` (toute la bibliothèque, pour LaTeX, Pandoc, Obsidian…) si une notice a changé."""
    last = conn.execute("SELECT coalesce(max(id), 0) || ':' || (SELECT count(*) FROM works) FROM changes").fetchone()[0]
    path = home / "library.bib"
    if db.get_meta(conn, "librarybib.state") == last and path.exists():
        return False
    tmp = path.with_suffix(".tmp")
    tmp.write_text(bibtex([core.get_work(conn, i) for i in all_refs(conn)]) if last != "0:0" else "", encoding="utf-8")
    tmp.replace(path)
    db.set_meta(conn, "librarybib.state", last)
    return True
