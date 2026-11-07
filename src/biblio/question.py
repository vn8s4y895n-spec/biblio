"""Question en langage courant → mots-clés pour les bases en ligne, sans IA.

Les bases exigent tous les mots de la requête : une phrase entière (« chez un… est-il possible d'utiliser… »)
ne trouve rien. On garde les termes porteurs de sens, traduits en anglais pour les plus courants en infectiologie.
Ce n'est qu'un dépannage : la traduction par un modèle de langue viendra en phase 9.
"""

import itertools
import re
import unicodedata

# Mots de la question sans valeur de recherche (français et anglais), comparés sans accents.
STOPWORDS = set("""
a afin ainsi alors au aussi autre aux avec avoir ce cela ces cet cette chez comme comment dans de des doit donc du
elle en entre est et etre faire faut il ils je la le les leur lors mais meme moins ne nous on ou par pas peut peuvent
plus possible pour pourquoi quand que quel quelle quelles quels qui quoi sa sans se selon ses si son sont sous sur
tout tous un une utiliser utilisation vous y peut-on faut-il est-il est-ce existe-t-il quelles-sont recommande
recommandee recommandes cas patient patients place role interet
a an and any are as at be best by can could do does for from how i in into is it may of on or should the there
this to use using was what when which who why with
""".split())  # fmt: skip

# Glossaire français → anglais (formes sans accents), limité aux termes fréquents en infectiologie.
GLOSSARY = {
    "resistant": "resistant", "resistante": "resistant", "resistants": "resistant", "resistantes": "resistant",
    "resistance": "resistance", "sensible": "susceptible", "sensibilite": "susceptibility",
    "bacteriemie": "bacteremia", "bacteriemies": "bacteremia", "septicemie": "sepsis", "choc": "shock",
    "endocardite": "endocarditis", "pneumonie": "pneumonia", "pneumopathie": "pneumonia", "meningite": "meningitis",
    "osteite": "osteomyelitis", "osteomyelite": "osteomyelitis", "spondylodiscite": "spondylodiscitis",
    "pyelonephrite": "pyelonephritis", "cystite": "cystitis", "prostatite": "prostatitis", "urinaire": "urinary",
    "peritonite": "peritonitis", "abces": "abscess", "cellulite": "cellulitis", "arthrite": "arthritis",
    "traitement": "treatment", "traitements": "treatment", "therapie": "therapy", "antibiotherapie": "antibiotic therapy",
    "antibiotique": "antibiotic", "antibiotiques": "antibiotics", "antifongique": "antifungal", "association": "combination",
    "duree": "duration", "posologie": "dosing", "toxicite": "toxicity", "efficacite": "efficacy", "echec": "failure",
    "mortalite": "mortality", "transmission": "transmission", "portage": "carriage", "colonisation": "colonization",
    "enfant": "children", "enfants": "children", "nouveau-ne": "neonates", "nouveau-nes": "neonates",
    "grossesse": "pregnancy", "foyer": "household", "intrafamiliale": "household", "intrafamilial": "household",
    "hopital": "hospital", "nosocomiale": "nosocomial", "communautaire": "community", "reanimation": "intensive care",
    "prothese": "prosthetic", "protheses": "prosthetic", "articulaire": "joint", "catheter": "catheter",
    "greffe": "transplant", "immunodeprime": "immunocompromised", "neutropenie": "neutropenia",
    "levure": "yeast", "levures": "yeast", "candidose": "candidiasis", "aspergillose": "aspergillosis",
    "tuberculose": "tuberculosis", "paludisme": "malaria", "diagnostic": "diagnosis", "depistage": "screening",
    "epidemiologie": "epidemiology", "facteurs": "factors", "risque": "risk", "prevention": "prevention", "medecine": "medicine",
    "staphylocoque": "Staphylococcus", "staphylocoques": "Staphylococcus", "streptocoque": "Streptococcus",
    "enterocoque": "Enterococcus", "enterocoques": "Enterococcus", "pneumocoque": "Streptococcus pneumoniae",
    "meningocoque": "Neisseria meningitidis", "gonocoque": "Neisseria gonorrhoeae", "enterobacterie": "Enterobacterales",
    "enterobacteries": "Enterobacterales", "blse": "ESBL", "sarm": "MRSA", "erv": "VRE", "erg": "VRE",
    "epc": "carbapenemase-producing Enterobacterales", "bmr": "multidrug-resistant", "bhre": "XDR",
    "vih": "HIV", "vhb": "hepatitis B", "vhc": "hepatitis C",
}  # fmt: skip

# Médicaments : le nom français perd souvent un « e » final (vancomycine → vancomycin, méropénème → meropenem).
DRUG_SUFFIXES = (("cine", "cin"), ("lline", "llin"), ("fungine", "fungin"), ("planine", "planin"), ("stine", "stin"),
                 ("ricine", "ricin"), ("toine", "toin"), ("eme", "em"), ("zolide", "zolid"), ("prime", "prim"))  # fmt: skip

# Espèces abrégées (« E. faecium ») → nom complet, que PubMed reconnaît.
GENUS = {
    "coli": "Escherichia", "faecium": "Enterococcus", "faecalis": "Enterococcus", "aureus": "Staphylococcus",
    "epidermidis": "Staphylococcus", "pneumoniae": None, "aeruginosa": "Pseudomonas", "baumannii": "Acinetobacter",
    "difficile": "Clostridioides", "cloacae": "Enterobacter", "maltophilia": "Stenotrophomonas",
    "influenzae": "Haemophilus", "meningitidis": "Neisseria", "gonorrhoeae": "Neisseria", "tuberculosis": "Mycobacterium",
    "albicans": "Candida", "auris": "Candida", "monocytogenes": "Listeria", "agalactiae": "Streptococcus",
    "pyogenes": "Streptococcus", "fumigatus": "Aspergillus", "lugdunensis": "Staphylococcus",
}  # fmt: skip
SPECIES = re.compile(r"\b([A-Z])\.\s*([a-z]{4,})\b")
SYNTAX = re.compile(r"\[\w+\]|\b(AND|OR|NOT)\b|\"")


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()


def is_syntax(text: str) -> bool:
    """Requête déjà écrite pour PubMed (étiquettes [tiab], opérateurs, guillemets) : à envoyer telle quelle."""
    return bool(SYNTAX.search(text))


def _species(match: re.Match) -> str:
    initial, epithet = match.groups()
    genus = GENUS.get(epithet)
    if epithet == "pneumoniae":
        genus = {"K": "Klebsiella", "S": "Streptococcus"}.get(initial)
    return f'"{genus} {epithet}"' if genus and genus[0] == initial else f'"{initial}. {epithet}"'


def _translate(word: str) -> str:
    key = plain(word)
    if key in GLOSSARY:
        return GLOSSARY[key]
    for fr, en in DRUG_SUFFIXES:
        if key.endswith(fr) and len(key) > len(fr) + 3:
            return key[: -len(fr)] + en
    return word


def keywords(text: str, translated: list | None = None) -> list[str]:
    """Termes de recherche d'une question : espèces entre guillemets, mots outils retirés, termes traduits.
    `translated` (facultatif) reçoit les traductions faites, en paires (mot de la question, terme anglais)."""
    terms: list[str] = []
    text = SPECIES.sub(lambda m: f" {_species(m)} ", text.replace("’", "'"))
    for token in re.findall(r'"[^"]+"|[\w][\w-]*', text):
        if token.startswith('"'):
            term = token
        else:
            if plain(token) in STOPWORDS or len(token) < 2 or token.isdigit():
                continue
            term = _translate(token)
            if translated is not None and term.lower() != token.lower():
                translated.append((token, term))
            term = f'"{term}"' if " " in term else term
        if term.lower() not in (t.lower() for t in terms):
            terms.append(term)
    return terms


def relaxed(terms: list[str]) -> str:
    """Requête assouplie : il peut manquer un terme (OU de toutes les combinaisons de n-1 termes)."""
    groups = [" AND ".join(c) for c in itertools.combinations(terms, len(terms) - 1)]
    return " OR ".join(f"({g})" for g in groups)


def coverage(terms: list[str], rec: dict) -> float:
    """Part des termes présents dans le titre et le résumé. La comparaison se fait sur le début du mot, sans
    accents : « vancomycin » retrouve aussi « vancomycine »."""
    text = plain(f"{rec.get('title') or ''} {rec.get('abstract') or ''}")
    found = 0
    for term in terms:
        word = plain(term.strip('"').split()[-1])  # pour une espèce : l'épithète (« faecium »)
        found += word[:7] in text
    return found / len(terms) if terms else 1.0


def concept_groups(query: str) -> list[list[str]]:
    """Concepts d'une requête booléenne : les blocs reliés par AND au premier niveau, chacun avec ses termes
    (synonymes reliés par OR). Les étiquettes [MeSH], [tiab]… et les blocs NOT sont ignorés."""
    query = re.sub(r"\[[^\]]*\]", " ", query)
    parts, depth, current = [], 0, ""
    for token in re.split(r'("[^"]*"|[()]|\bAND\b|\bNOT\b)', query):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        if token in ("AND", "NOT") and depth == 0:
            parts.append(current)
            current = "NOT " if token == "NOT" else ""
        else:
            current += token
    parts.append(current)
    groups = []
    for part in parts:
        if re.match(r"\s*\(*\s*NOT\b", part):
            continue
        terms = [a or b for a, b in re.findall(r'"([^"]+)"|\b([A-Za-z][\w-]{2,})\b', part) if (a or b) not in ("OR", "NOT", "AND")]
        if terms:
            groups.append(list(dict.fromkeys(t.lower() for t in terms)))
    return groups


def group_coverage(groups: list[list[str]], rec: dict) -> float:
    """Part des concepts dont au moins un terme figure dans le titre ou le résumé (début des mots, sans accents)."""
    text = plain(f"{rec.get('title') or ''} {rec.get('abstract') or ''}")

    def found(term: str) -> bool:
        return all(plain(w)[:6] in text for w in term.split() if len(w) > 2)

    return sum(any(found(t) for t in g) for g in groups) / len(groups) if groups else 1.0
