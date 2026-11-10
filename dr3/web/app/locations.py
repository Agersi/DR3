"""Location thumbnail helpers (website).

Each championship location ships one thumbnail in ``static/locations/``
named ``<slug>.png``, where the slug is the lowercased display name with
spaces turned into hyphens (diacritics stripped — "Höljes" → "holjes").
The two file names that do not match their display name are the mapping:
Monaco.png serves Monte Carlo and USA.png serves New England."""
from __future__ import annotations

import unicodedata
from pathlib import Path

_LOC_DIR = Path(__file__).resolve().parent.parent / 'static' / 'locations'


def _slug(name: str) -> str:
    s = unicodedata.normalize('NFKD', name or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    out = []
    for ch in s.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != '-':
            out.append('-')
    return ''.join(out).strip('-')


# Every thumbnail that actually ships with the site, so an unknown or
# legacy location name simply resolves to '' (no image) instead of 404ing.
THUMB_SLUGS = {p.stem for p in _LOC_DIR.glob('*.png')} if _LOC_DIR.is_dir() else set()


def location_thumb_url(display_name: str) -> str:
    """Static URL of the thumbnail for a location display name; '' when unknown.

    Only called from templates or request-scoped helpers (uses ``url_for``)."""
    from flask import url_for

    slug = _slug(display_name)
    if slug and slug in THUMB_SLUGS:
        return url_for('static', filename=f'locations/{slug}.png')
    return ''
