"""Déroulé d'une recherche : chaque étape (traduction, requête de l'IA, appel à une base, filtres…) est signalée
à qui écoute, pour l'afficher pendant que la recherche tourne. Sans écouteur, `step` ne fait rien."""

import re
import time
from collections.abc import Callable
from contextvars import ContextVar

_sink: ContextVar[tuple[Callable[[dict], None], float] | None] = ContextVar("trace_sink", default=None)
source: ContextVar[str | None] = ContextVar("trace_source", default=None)  # base en cours d'interrogation
SECRET = re.compile(r"key|token|email|mailto|tool", re.I)  # paramètres jamais montrés (clés, e-mail de contact)


def listen(sink: Callable[[dict], None]) -> None:
    """À appeler dans le fil qui fait la recherche ; les fils lancés avec copy_context() héritent de l'écouteur."""
    _sink.set((sink, time.perf_counter()))


def step(kind: str, **data) -> None:
    """`t` : millisecondes écoulées depuis le début ; `source` : la base qui a fait la requête, s'il y en a une."""
    if (listener := _sink.get()) is not None:
        sink, start = listener
        sink({"kind": kind, "t": round((time.perf_counter() - start) * 1000), "source": source.get(), **data})


def public_url(url) -> str:
    """Adresse d'une requête, sans clé d'API ni e-mail."""
    kept = [(k, v) for k, v in url.params.multi_items() if not SECRET.search(k)]
    return str(url.copy_with(params=kept)) if kept else str(url.copy_with(query=None))
