"""Car-class thumbnail helpers (website).

Each selectable car class ships one thumbnail in ``static/classes/`` named
``<slug>.png`` — the same slug convention as location thumbnails (lowercased
label, spaces and punctuation turned into hyphens).  The source file names use
underscores/case, so they are renamed to the slug at copy time; a class label
with no shipped thumbnail simply resolves to '' instead of 404ing."""
from __future__ import annotations

from pathlib import Path

from .locations import _slug

_CLASS_DIR = Path(__file__).resolve().parent.parent / 'static' / 'classes'

# Every thumbnail that actually ships with the site, so an unknown or legacy
# class label simply resolves to '' (no image) instead of 404ing.
CLASS_THUMB_SLUGS = {p.stem for p in _CLASS_DIR.glob('*.png')} if _CLASS_DIR.is_dir() else set()


def class_thumb_url(label: str) -> str:
    """Static URL of the thumbnail for a car-class label; '' when unknown.

    Only called from templates or request-scoped helpers (uses ``url_for``)."""
    from flask import url_for

    slug = _slug(label)
    if slug and slug in CLASS_THUMB_SLUGS:
        return url_for('static', filename=f'classes/{slug}.png')
    return ''
