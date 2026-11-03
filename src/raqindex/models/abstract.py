"""Reconstruct OpenAlex abstract text from word positions."""

from collections.abc import Mapping, Sequence


def reconstruct_abstract(index: Mapping[str, Sequence[int]] | None) -> str | None:
    """Return plain text, or None when no abstract words are available."""
    if not index:
        return None
    words = {
        position: word
        for word, positions in index.items()
        for position in positions
    }
    if not words:
        return None
    return " ".join(words.get(position, "") for position in range(max(words) + 1))
