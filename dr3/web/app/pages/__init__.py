from __future__ import annotations

from flask import Flask

from .clubs import clubs_bp
from .views import pages_bp


def register_page_blueprints(app: Flask) -> None:
    app.register_blueprint(pages_bp)
    app.register_blueprint(clubs_bp)
