"""Flask application factory."""
from __future__ import annotations

import logging
import os

from urllib.parse import urlsplit

from flask import Flask, abort, flash, redirect, request
from werkzeug.exceptions import NotFound

from laneforge import db
from laneforge.queries.errors import ValidationError
from laneforge.web import ddragon
from laneforge.web.blueprints import (
    back_path, close_conn, current_user, dataset, render,
)
from laneforge.web.blueprints import account, builds, pages

log = logging.getLogger(__name__)
DEV_SECRET_KEY = "laneforge-dev-only-not-secret"

# Request-size ceilings. The largest legitimate form is a saved build:
# a 60-char name, 2,000 chars of notes, four enemies and three items.
MAX_CONTENT_LENGTH = 64 * 1024
MAX_FORM_PARTS = 64
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _secret_key(testing: bool) -> str:
    """The signing key for the session cookie, which is the whole sign-in
    mechanism. Outside tests a missing key is a deployment error, not a
    warning: a known key lets anyone forge a session for any user."""
    key = os.environ.get("FLASK_SECRET_KEY")
    if key:
        return key
    if testing:
        return DEV_SECRET_KEY
    raise RuntimeError("FLASK_SECRET_KEY is not set; put a long random value in .env")


def _register_origin_check(app: Flask) -> None:
    """Defence in depth next to SameSite=Lax: a state-changing request whose
    Origin (or Referer) names another host is refused."""
    @app.before_request
    def reject_cross_site_writes():
        if request.method not in STATE_CHANGING_METHODS:
            return None
        source = request.headers.get("Origin") or request.headers.get("Referer") or ""
        source_host = urlsplit(source).netloc.lower()
        if source_host and source_host != request.host.lower():
            abort(403, description="Cross-site form submissions are not accepted.")
        return None


def _register_errors(app: Flask) -> None:
    @app.errorhandler(ValidationError)
    def bad_input(err: ValidationError):
        if request.method == "POST":
            flash(err.message, "error")
            return redirect(back_path("/"))
        return render("errors/400.html", status=400, message=err.message)

    @app.errorhandler(403)
    def forbidden(err):
        return render("errors/400.html", status=403, message=err.description)

    @app.errorhandler(413)
    def too_large(err):
        return render("errors/400.html", status=413,
                      message="That request was too large. Notes are limited to 2,000 characters.")

    @app.errorhandler(404)
    def not_found(err: NotFound):
        message = err.description if err.description != NotFound.description else \
            "There is nothing at this address."
        return render("errors/404.html", status=404, message=message)


def _register_context(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        return {"current_user": current_user(), "dataset": dataset()}


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL=db.dsn(),
        TOLERATE_MISSING_TEMPLATES=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("LANEFORGE_HTTPS") == "1",
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
        MAX_FORM_MEMORY_SIZE=MAX_CONTENT_LENGTH,
        MAX_FORM_PARTS=MAX_FORM_PARTS,
    )
    app.config.update(config or {})
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret_key(app.testing)
    if not app.testing:
        app.config["TOLERATE_MISSING_TEMPLATES"] = False   # never in production
    ddragon.register(app)
    for blueprint in (pages.bp, builds.bp, account.bp):
        app.register_blueprint(blueprint)
    _register_errors(app)
    _register_context(app)
    _register_origin_check(app)
    app.teardown_appcontext(close_conn)
    return app
