"""Deciding whether a search hit is actually the song you asked for.

Both providers search by title and both will return a different act's song with
the same name, so nothing is trusted until it clears these checks.
"""

from __future__ import annotations

import re

def _strip_noise(s):
    """Spotify titles often carry stuff LRCLIB doesn't have indexed."""
    s = re.sub(r"\s*[\(\[][^)\]]*(remaster|remastered|deluxe|edition|version|"
               r"mono|stereo|live|bonus|anniversary)[^)\]]*[\)\]]", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*(\d{4}\s*)?remaster(ed)?.*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*(single|radio|album)\s*(edit|version|mix)\s*$", "", s, flags=re.I)
    return s.strip()



# Artist credits get written a dozen ways: "A & B", "A feat. B", "A, B",
# "A x B". Split on all of them so a feature credit on one side and not the
# other doesn't read as a different artist.
_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|/|\+|\bfeat\.?\b|\bft\.?\b|\bwith\b|\bx\b|\bvs\.?\b|\band\b)\s*",
    re.I)


# Stylised "$" for "s" is common enough (A$AP, Ke$ha) that dropping it turns a
# match into a miss. Deliberately only this one -- mapping digits to letters
# would break names where the digit is real ("blink-182", "3OH!3").
_STYLISED = str.maketrans({"$": "s"})


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().translate(_STYLISED))


def _artist_tokens(s):
    return {t for t in (_norm(p) for p in _ARTIST_SPLIT.split(s or "")) if t}


def _artist_matches(want, got):
    """True if two artist credits plausibly name the same act.

    LRCLIB's search will happily return a different artist's song with the
    same title, so every search hit has to clear this before we trust it.
    """
    a, b = _artist_tokens(want), _artist_tokens(got)
    if not a or not b:
        return False
    # A shared credit: "Barney Bones" vs "Barney Bones & Duckwrth".
    if a & b:
        return True
    # The same name punctuated differently, where splitting produced different
    # tokens on each side: "Tyler, The Creator" vs "Tyler The Creator".
    # Deliberately equality, not containment -- containment would accept
    # "greek" as "Greek Fire", and "MiLES." as "Miles Davis".
    return bool(_norm(want)) and _norm(want) == _norm(got)



def _title_matches(want, got):
    """Equality after normalising punctuation, case, and feature credits.

    Deliberately not containment: "FOLDED" is a substring of "UNFOLDED THE
    SEQUEL", and serving the wrong song's words is the failure this whole
    verification path exists to prevent. Stripping "(feat. X)" first is what
    makes plain equality sufficient.
    """
    a, b = _norm(_strip_features(want)), _norm(_strip_features(got))
    return bool(a) and a == b


def _strip_features(s):
    return re.sub(r"\s*[\(\[]?\s*(feat\.?|ft\.?|with)\s+[^)\]]*[\)\]]?\s*$", "",
                  s or "", flags=re.I).strip()


