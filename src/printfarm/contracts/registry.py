"""Single source of truth mapping CloudEvents ``type`` <-> NATS subject <-> payload model."""

from __future__ import annotations

from printfarm.contracts import messages
from printfarm.contracts.envelope import Payload


def _collect() -> dict[str, type[Payload]]:
    out: dict[str, type[Payload]] = {}
    for obj in vars(messages).values():
        if isinstance(obj, type) and issubclass(obj, Payload) and "subject" in obj.__dict__:
            t = obj.event_type()
            if t in out:
                raise RuntimeError(f"duplicate message type {t}")
            out[t] = obj
    return out


REGISTRY: dict[str, type[Payload]] = _collect()


def model_for_type(event_type: str) -> type[Payload]:
    try:
        return REGISTRY[event_type]
    except KeyError:
        raise KeyError(f"unknown message type {event_type!r}") from None


def subject_for_type(event_type: str) -> str:
    return model_for_type(event_type).subject


def subject_matches(pattern: str, subject: str) -> bool:
    """NATS wildcard semantics: ``*`` = one token, ``>`` = one or more trailing tokens."""
    p, s = pattern.split("."), subject.split(".")
    for i, tok in enumerate(p):
        if tok == ">":
            return len(s) > i
        if i >= len(s) or (tok != "*" and tok != s[i]):
            return False
    return len(p) == len(s)
