from __future__ import annotations

import re
import unicodedata


_SEPARATORS = re.compile(r"[\s\-_.·・:'’♀♂#]+")


def normalize_alias(value: str) -> str:
    """Normalize a user-facing species alias without performing translation."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return _SEPARATORS.sub("", normalized)

