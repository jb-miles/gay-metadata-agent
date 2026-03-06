from __future__ import annotations

import re
import unicodedata


def strip_diacritics(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

