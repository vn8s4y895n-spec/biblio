"""Écran de question, sans LLM : la réponse est faite des phrases des sources les plus proches de la question,
chacune avec sa référence. Chaque source reçoit un « résumé Biblio » (sa conclusion) et son effectif."""

import html
import re
import sqlite3

from . import core, library, search, semantic

ANSWER_SOURCES = 8  # sources dont on lit le résumé pour composer la réponse
ANSWER_SENTENCES = 5
LEXICAL_MIN = 0.3  # sans modèle : part minimale des mots de la question présents dans la phrase
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
# Sections d'un résumé structuré qui portent les résultats (les autres décrivent le contexte ou la méthode).
FINDINGS = re.compile(r"^(results?|findings|conclusions?|interpretation|main outcomes? and measures?|résultats?|conclusions?)\b", re.I)
CONCLUSION = re.compile(r"^(conclusions?( and relevance)?|interpretation|conclusion|significance)\s*:", re.I)
# Intitulés de résumé structuré en capitales, parfois collés au texte précédent (« …coliIMPORTANCE Entero… »).
LABELS = re.compile(
    r"\s*(?<![A-Z])(BACKGROUND|INTRODUCTION|IMPORTANCE|OBJECTIVES?|AIMS?|PURPOSE|METHODS?|DESIGN|SETTING|PARTICIPANTS|"
    r"RESULTS?|FINDINGS|CONCLUSIONS?(?: AND RELEVANCE)?|INTERPRETATION|SIGNIFICANCE)(?![a-z])\s*:?\s*"
)
MIN_SAMPLE = 10  # en dessous, le nombre trouvé est rarement un effectif
SAMPLE = re.compile(
    r"\b(?:n\s*=\s*)?(\d{1,3}(?:[ ,]\d{3})+|\d+)\s+(?:\w+\s){0,2}?"
    r"(patients|participants|subjects|adults|children|infants|neonates|individuals|persons|people|households|"
    r"cases|women|men|isolates|strains|samples|episodes|residents|volunteers)\b",
    re.I,
)


def sections(abstract: str | None) -> list[tuple[str, str]]:
    """Découpe un résumé (structuré « LABEL: texte » par ligne, ou d'un seul bloc) en (section, texte)."""
    out = []
    for line in LABELS.sub(lambda m: f"\n{m.group(1).capitalize()}: ", abstract or "").splitlines():
        label, sep, text = line.partition(": ")
        if sep and len(label.split()) <= 5 and label[:1].isupper():
            out.append((label.strip(), text.strip()))
        elif line.strip():
            out.append(("", line.strip()))
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(text) if len(s.split()) >= 6]


def summary(abstract: str | None) -> str | None:
    """Conclusion des auteurs si le résumé est structuré ; sinon ses deux dernières phrases."""
    parts = sections(abstract)
    for label, text in parts:
        if CONCLUSION.match(f"{label}:"):
            return text
    flat = sentences(" ".join(text for _, text in parts))
    return " ".join(flat[-2:]) or None


def sample_size(abstract: str | None) -> int | None:
    """Premier effectif cité (« 391 patients », « n = 1 250 participants »)."""
    for match in SAMPLE.finditer(abstract or ""):
        if (n := int(re.sub(r"[ ,]", "", match.group(1)))) >= MIN_SAMPLE:
            return n
    return None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.casefold()) if len(w) > 2 and w not in library.QUESTION_STOPWORDS}


def _candidates(rec: dict) -> list[str]:
    """Phrases de résultats d'abord ; tout le résumé s'il n'est pas structuré."""
    parts = sections(rec.get("abstract"))
    findings = [text for label, text in parts if label and FINDINGS.match(label)]
    return [s for text in (findings or [t for _, t in parts]) for s in sentences(text)]


def compose(question: str, sources: list[dict]) -> list[dict]:
    """Choisit au plus une phrase par source parmi les ANSWER_SOURCES premières, classées par proximité avec la
    question (modèle sémantique s'il est chargé, sinon mots en commun) ; une phrase trop éloignée est écartée.
    `source` : numéro de la source (1…)."""
    pool = [(n, s) for n, rec in enumerate(sources[:ANSWER_SOURCES], 1) for s in _candidates(rec)]
    if not pool:
        return []
    model = semantic.embedder()
    if model is not None:
        scores = semantic.similarity(model, question, [s for _, s in pool])
        minimum = library.SEMANTIC_MIN
    else:
        wanted = _words(question)
        scores = [len(wanted & _words(s)) / (len(wanted) or 1) for _, s in pool]
        minimum = LEXICAL_MIN
    best: dict[int, tuple[float, str]] = {}
    for (n, sentence), score in zip(pool, scores, strict=True):
        if score > best.get(n, (-1.0, ""))[0]:
            best[n] = (score, sentence)
    ranked = sorted(best.items(), key=lambda item: -item[1][0])[:ANSWER_SENTENCES]
    return [{"text": sentence, "source": n, "score": round(score, 4)} for n, (score, sentence) in ranked if score >= minimum]


def _decorate(rec: dict) -> dict:
    return {**rec, "summary": summary(rec.get("abstract")), "sample_size": sample_size(rec.get("abstract"))}


def ask_online(conn: sqlite3.Connection, question: str, filters: search.Filters, limit: int = 20,
               query: str | None = None) -> dict:  # fmt: skip
    """`query` : requête PubMed corrigée à la main ; la question sert toujours à choisir les phrases."""
    found = search.search(conn, query or question, limit=limit, filters=filters)
    sources = [_decorate(rec) for rec in found["results"]]
    return {"scope": "online", "question": question, "query": found["query"]["boolean"], "total": found["total"],
            "hidden": found["hidden"], "status": found["sources"], "answer": compose(question, sources),
            "sources": sources}  # fmt: skip


def ask_library(conn: sqlite3.Connection, question: str, limit: int = 20) -> dict:
    """Dans la bibliothèque, la réponse reprend le meilleur passage de chaque notice (résumé, note ou texte
    intégral, avec sa page)."""
    found = library.search(conn, question, limit)
    sources, answer = [], []
    for n, hit in enumerate(found["results"], 1):
        work = core.get_work(conn, hit["id"])
        sources.append({**_decorate(work), "in_library": work["citekey"], "passages": hit["passages"]})
        if hit["passages"] and len(answer) < ANSWER_SENTENCES:
            passage = hit["passages"][0]
            text = html.unescape(re.sub(r"</?mark>", "", passage["snippet"]))
            answer.append({"text": text, "source": n, "page": passage.get("page"), "origin": passage["origin"]})
    return {"scope": "library", "question": question, "query": None, "total": found["total"], "hidden": {},
            "status": {}, "engine": found["engine"], "answer": answer, "sources": sources}  # fmt: skip
