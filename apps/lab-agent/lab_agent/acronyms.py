"""Approved, versioned acronym dictionary: never invents an expansion.

Unknown or colliding acronym-like terms stay unresolved and visible to the
grounding gate (see :mod:`lab_agent.grounding`) -- the model's own claim
about a term is always re-checked against this dictionary, never trusted
outright.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_RESOURCE = Path(__file__).resolve().parent / "resources" / "acronyms.json"

#: Acronym-like tokens: 2+ leading uppercase letters/digits, optionally one
#: hyphenated suffix (e.g. "PCR", "IL-6"). Single letters (placeholders like
#: "A"/"B" used throughout fixtures/tests) never match.
_TERM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]+)?\b")
_INLINE_DEFINITION_RE = re.compile(
    r"(?P<long>(?:[A-Za-z][A-Za-z-]*[ \t]+){1,7}[A-Za-z][A-Za-z-]*)"
    r"[ \t]*\((?P<term>[A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]+)?)\)"
)


@dataclass(frozen=True)
class AcronymDictionary:
    """A versioned, approved set of acronym -> candidate expansion(s)."""

    version: int
    entries: dict[str, tuple[str, ...]]

    def resolve(self, term: str) -> tuple[bool, str]:
        """Resolve ``term`` (case-insensitive) to its approved expansion.

        Returns ``(True, expansion)`` only when exactly one approved
        candidate exists; an unknown term or one with 2+ colliding
        candidates returns ``(False, "")`` -- surfaced, never guessed.
        """
        candidates = self.entries.get(term.upper(), ())
        if len(candidates) == 1:
            return True, candidates[0]
        return False, ""


def _parse(raw: dict[str, Any]) -> AcronymDictionary:
    entries = {
        str(term).upper(): tuple(str(x) for x in expansions)
        for term, expansions in raw.get("entries", {}).items()
    }
    return AcronymDictionary(version=int(raw.get("version", 0)), entries=entries)


@lru_cache(maxsize=8)
def load_acronym_dictionary(path: Path | None = None) -> AcronymDictionary:
    """Load the approved acronym dictionary from ``path`` (default: the
    packaged ``resources/acronyms.json``).

    An empty/minimal seed is acceptable -- unknown terms simply stay
    unresolved rather than the loader failing closed.
    """
    resource = path or _DEFAULT_RESOURCE
    if not resource.exists():
        return AcronymDictionary(version=0, entries={})
    return _parse(json.loads(resource.read_text(encoding="utf-8")))


def detect_acronym_terms(text: str) -> list[str]:
    """Scan free text for acronym-like tokens, deduplicated, in first-seen order."""
    seen: dict[str, None] = {}
    for match in _TERM_RE.finditer(text):
        seen.setdefault(match.group(0), None)
    return list(seen)


def detect_inline_acronym_definitions(text: str) -> dict[str, tuple[str, ...]]:
    """Return validated long-form-first definitions declared in one text field.

    Only a short alphabetic long form immediately followed by ``(ACRONYM)``
    qualifies. Every distinct matching expansion is retained so a conflicting
    setup-local definition remains ambiguous instead of being cleared by its
    first occurrence.
    """
    definitions: dict[str, dict[str, str]] = {}
    for match in _INLINE_DEFINITION_RE.finditer(text):
        term = match.group("term")
        if not term.isalpha():
            continue
        words = match.group("long").split()
        if len(words) < len(term):
            continue
        definition_words = words[-len(term):]
        initials = "".join(word[0] for word in definition_words).upper()
        if initials == term:
            expansion = " ".join(definition_words)
            definitions.setdefault(term, {}).setdefault(expansion.casefold(), expansion)
    return {term: tuple(expansions.values()) for term, expansions in definitions.items()}


__all__ = [
    "AcronymDictionary",
    "detect_acronym_terms",
    "detect_inline_acronym_definitions",
    "load_acronym_dictionary",
]
