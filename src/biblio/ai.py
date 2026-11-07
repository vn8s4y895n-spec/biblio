"""Fonctions d'IA de l'écran Question : requête PubMed écrite par le modèle, réponse rédigée avec citations
vérifiées, résumé d'un article. Tout est facultatif : sans modèle configuré, Biblio garde ses méthodes sans IA.

Garde-fous : le modèle ne voit que les sources trouvées ; chaque phrase de sa réponse est confrontée aux résumés
des sources qu'elle cite (modèle sémantique local, sinon mots en commun) et signalée si rien ne l'appuie.
"""

import hashlib
import re
import sqlite3
from collections.abc import Iterator

from . import ask, db, llm, semantic

MAX_SOURCES = 8  # sources données au modèle pour rédiger
ABSTRACT_CHARS = 1500
SUPPORT_SEMANTIC = 0.5  # cosinus minimal entre une phrase et une phrase de la source citée
SUPPORT_LEXICAL = 0.35  # sans modèle sémantique : part des mots de la phrase présents dans la source
CITATION = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")

PLAN_PROMPT = """Tu aides un médecin biologiste (bactériologie, maladies infectieuses) à chercher dans PubMed.
À partir de sa question (en français ou en anglais), écris :
- "pubmed_query" : une requête PubMed en anglais, booléenne, avec les descripteurs MeSH utiles ([MeSH]) et des
  synonymes en texte libre ([tiab]) reliés par OR, les concepts reliés par AND. Pas de filtre de date ni de type
  d'étude. Si la question contient plusieurs sous-questions, couvre-les toutes (OR entre les sous-requêtes).
  Les synonymes doivent être de vrais synonymes : n'ajoute jamais une molécule, un organisme ou une maladie
  absents de la question, même au nom proche (dalbavancine n'est pas dalfopristine).
- "related_questions" : 3 questions proches qu'il pourrait poser ensuite, dans la langue de sa question.
Réponds uniquement en JSON : {"pubmed_query": "...", "related_questions": ["...", "...", "..."]}"""

ANSWER_PROMPT = """Tu es un assistant de recherche bibliographique pour un médecin biologiste.
Réponds à la question uniquement à partir des sources numérotées ci-dessous, dans la langue de la question.
Règles :
- 120 à 220 mots, en paragraphes courts, sans titre, sans liste, sans mise en forme Markdown (ni * ni #).
- Chaque phrase se termine par le numéro de la ou des sources qui l'appuient, entre crochets : [1] ou [2][4].
- N'écris rien qui ne soit pas dans les sources ; si elles ne suffisent pas, dis-le clairement.
- Précise le type d'étude et l'effectif quand ils comptent pour le niveau de preuve.
- Signale les résultats contradictoires entre sources."""

SUMMARY_PROMPT = """Résume cet article scientifique en 3 phrases au plus, en {lang} : type d'étude et population
(avec l'effectif), résultat principal (avec les chiffres clés), conclusion des auteurs. Uniquement à partir du texte
fourni, sans rien ajouter."""


def enabled() -> bool:
    return llm.current.ready


# --- Requête PubMed -----------------------------------------------------------------------------------


def plan(question: str) -> dict:
    """Requête PubMed et questions liées. Lève llm.LLMError si le modèle échoue ou répond mal."""
    data = llm.chat_json([{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": question}])
    query = " ".join(str(data.get("pubmed_query") or "").split())
    if not query or len(query) > 1500 or query.count("(") != query.count(")"):
        raise llm.LLMError("Requête PubMed du modèle inutilisable")
    related = [str(q).strip() for q in data.get("related_questions") or [] if str(q).strip()][:3]
    return {"query": query, "related": related}


# --- Réponse rédigée ------------------------------------------------------------------------------------


def _source_block(n: int, rec: dict) -> str:
    meta = [rec.get("container_abbrev") or rec.get("container_title"), rec.get("year"), rec.get("study_type"),
            f"n = {rec['sample_size']}" if rec.get("sample_size") else None]  # fmt: skip
    abstract = (rec.get("abstract") or rec.get("summary") or "")[:ABSTRACT_CHARS]
    return f"[{n}] {rec['title']} ({', '.join(str(m) for m in meta if m)})\n{abstract}"


def answer_messages(question: str, sources: list[dict]) -> list[dict]:
    blocks = "\n\n".join(_source_block(n, rec) for n, rec in enumerate(sources[:MAX_SOURCES], 1))
    return [{"role": "system", "content": ANSWER_PROMPT},
            {"role": "user", "content": f"Sources :\n\n{blocks}\n\nQuestion : {question}"}]  # fmt: skip


def answer_stream(question: str, sources: list[dict]) -> Iterator[str]:
    if not sources:
        raise llm.LLMError("Aucune source pour rédiger une réponse")
    yield from llm.stream(answer_messages(question, sources), max_tokens=1200)


def _cited(group: str) -> list[int]:
    """« 2 », « 2, 4 », « 2-4 » → numéros de sources (une plage absurde est ignorée)."""
    numbers = []
    for part in (p.strip() for p in group.split(",")):
        if m := re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part):
            if int(m[2]) - int(m[1]) <= MAX_SOURCES:
                numbers += range(int(m[1]), int(m[2]) + 1)
        elif part.isdigit():
            numbers.append(int(part))
    return numbers


def verify(text: str, sources: list[dict]) -> list[dict]:
    """Découpe la réponse en phrases et vérifie chacune : `ok` (appuyée par une source citée), `weak` (les sources
    citées ne disent rien d'approchant), `unsourced` (sans citation), `invalid` (cite une source qui n'existe pas)."""
    limit = min(len(sources), MAX_SOURCES)
    model = semantic.embedder()
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|(?<=\])\s+(?=[A-ZÀ-Ý])|\n+", text.strip()):
        if not sentence.strip():
            continue
        cites = sorted({n for g in CITATION.findall(sentence) for n in _cited(g)})
        bare = CITATION.sub("", sentence).strip()
        if len(bare.split()) < 4:  # fragment (« Oui. »), titre : rien à vérifier
            out.append({"text": sentence, "cites": cites, "status": "ok"})
            continue
        if not cites:
            status = "unsourced"
        elif any(n < 1 or n > limit for n in cites):
            status = "invalid"
        else:
            status = "ok" if _supported(bare, [sources[n - 1] for n in cites], model) else "weak"
        out.append({"text": sentence, "cites": cites, "status": status})
    return out


def _supported(sentence: str, cited: list[dict], model) -> bool:
    pool = [s for rec in cited for s in ask.sentences(f"{rec.get('title', '')}. {rec.get('abstract') or rec.get('summary') or ''}")]
    if not pool:
        return False
    if model is not None:
        return max(semantic.similarity(model, sentence, pool)) >= SUPPORT_SEMANTIC
    words = ask._words(sentence)
    text = ask._words(" ".join(pool))
    return bool(words) and len(words & text) / len(words) >= SUPPORT_LEXICAL


# --- Résumé d'un article -------------------------------------------------------------------------------


def summary(conn: sqlite3.Connection, rec: dict, lang: str = "fr") -> dict:
    """Résumé rédigé d'un article (à partir de son titre et de son résumé), gardé en cache par modèle et langue."""
    text = f"{rec.get('title', '')}\n\n{rec.get('abstract') or ''}".strip()
    if not rec.get("abstract"):
        raise ValueError("Pas de résumé à partir duquel rédiger")
    model = llm.current.model
    key = hashlib.sha256(f"{model}|{lang}|{text}".encode()).hexdigest()
    if row := conn.execute("SELECT text FROM ai_summaries WHERE key = ?", (key,)).fetchone():
        return {"text": row[0], "model": model, "cached": True}
    language = {"fr": "français", "en": "anglais"}.get(lang, "français")
    out = llm.chat([{"role": "system", "content": SUMMARY_PROMPT.format(lang=language)},
                    {"role": "user", "content": text[: ABSTRACT_CHARS * 2]}], max_tokens=400).strip()  # fmt: skip
    with db.transaction(conn):
        conn.execute("INSERT OR REPLACE INTO ai_summaries (key, model, lang, text) VALUES (?, ?, ?, ?)", (key, model, lang, out))
    return {"text": out, "model": model, "cached": False}
