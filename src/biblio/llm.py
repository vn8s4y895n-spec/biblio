"""Modèle de langue : une seule interface, l'API « chat completions » compatible OpenAI.

Ollama (en local, gratuit, rien ne sort de l'ordinateur), Mistral et OpenAI la proposent. La clé éventuelle est
rangée dans le trousseau du système (Keychain sur Mac, Credential Manager sur Windows), jamais dans config.toml.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

PRESETS = {
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3.5:9b", "key": False},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "model": "mistral-medium-latest", "key": True},
    "custom": {"base_url": "", "model": "", "key": True},
}
KEYRING_SERVICE = "Biblio"
TIMEOUT = httpx.Timeout(300, connect=5)  # un modèle local met parfois une minute à charger puis à répondre
transport: httpx.BaseTransport | None = None  # remplacé dans les tests


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    enabled: bool
    provider: str
    base_url: str
    model: str

    @classmethod
    def of(cls, cfg) -> "Settings":
        preset = PRESETS.get(cfg.llm_provider, PRESETS["custom"])
        return cls(cfg.llm_enabled, cfg.llm_provider, (cfg.llm_base_url or preset["base_url"]).rstrip("/"),
                   cfg.llm_model or preset["model"])  # fmt: skip

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.base_url and self.model)


# Réglages en vigueur (posés par l'API au démarrage et à chaque modification).
current = Settings(False, "ollama", PRESETS["ollama"]["base_url"], PRESETS["ollama"]["model"])


def configure(settings: Settings) -> None:
    global current
    current = settings


def get_key(provider: str) -> str | None:
    import keyring

    try:
        return keyring.get_password(KEYRING_SERVICE, f"llm:{provider}")
    except keyring.errors.KeyringError:
        return None


def set_key(provider: str, key: str) -> None:
    """Enregistre la clé dans le trousseau (chaîne vide : la supprime)."""
    import keyring

    if key:
        keyring.set_password(KEYRING_SERVICE, f"llm:{provider}", key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, f"llm:{provider}")
        except keyring.errors.PasswordDeleteError:
            pass


def _client(settings: Settings) -> httpx.Client:
    headers = {}
    if PRESETS.get(settings.provider, PRESETS["custom"])["key"] and (key := get_key(settings.provider)):
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=settings.base_url, headers=headers, timeout=TIMEOUT, transport=transport)


def _body(settings: Settings, messages: list[dict], json_mode: bool, max_tokens: int, stream: bool) -> dict:
    body = {"model": settings.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2, "stream": stream}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if settings.provider == "ollama":
        body["reasoning_effort"] = "none"  # pas de réflexion interne : réponses bien plus rapides en local
    return body


def _raise(response: httpx.Response) -> None:
    if response.status_code >= 400:
        try:
            detail = response.json().get("error")
            detail = detail.get("message") if isinstance(detail, dict) else detail
        except ValueError:
            detail = response.text[:200]
        raise LLMError(f"Modèle de langue : HTTP {response.status_code} {detail or ''}".strip())


def chat(messages: list[dict], json_mode: bool = False, max_tokens: int = 2000, settings: Settings | None = None) -> str:
    settings = settings or current
    if not settings.ready:
        raise LLMError("Intelligence artificielle désactivée (Réglages)")
    try:
        with _client(settings) as c:
            response = c.post("/chat/completions", json=_body(settings, messages, json_mode, max_tokens, False))
            _raise(response)
            return response.json()["choices"][0]["message"]["content"] or ""
    except httpx.HTTPError as exc:
        raise LLMError(f"Modèle de langue injoignable ({settings.base_url}) : {exc}") from exc


def chat_json(messages: list[dict], max_tokens: int = 1000) -> dict:
    text = chat(messages, json_mode=True, max_tokens=max_tokens).strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("Le modèle n'a pas renvoyé de JSON lisible") from exc


def stream(messages: list[dict], max_tokens: int = 2000) -> Iterator[str]:
    """Texte de la réponse, morceau par morceau (événements « data: » de l'API)."""
    settings = current
    if not settings.ready:
        raise LLMError("Intelligence artificielle désactivée (Réglages)")
    try:
        with _client(settings) as c, c.stream("POST", "/chat/completions", json=_body(settings, messages, False, max_tokens, True)) as r:
            if r.status_code >= 400:
                r.read()
                _raise(r)
            for line in r.iter_lines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                choice = (json.loads(line[5:]).get("choices") or [{}])[0]
                if piece := (choice.get("delta") or {}).get("content"):
                    yield piece
    except httpx.HTTPError as exc:
        raise LLMError(f"Modèle de langue injoignable ({settings.base_url}) : {exc}") from exc


def status(settings: Settings | None = None) -> dict:
    """Fournisseur joignable ? Modèles proposés (Ollama : ceux qui sont téléchargés)."""
    settings = settings or current
    out = {"enabled": settings.enabled, "provider": settings.provider, "base_url": settings.base_url,
           "model": settings.model, "key_set": bool(get_key(settings.provider)), "reachable": False, "models": []}  # fmt: skip
    try:
        with _client(settings) as c:
            response = c.get("/models", timeout=5)
            _raise(response)
            out["models"] = sorted(m["id"] for m in response.json().get("data", []))
            out["reachable"] = True
    except (httpx.HTTPError, LLMError, ValueError) as exc:
        out["error"] = str(exc)
    return out
