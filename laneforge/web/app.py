"""Flask application factory."""
from __future__ import annotations

import logging
import os

from flask import Flask, flash, redirect, request
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


def _secret_key() -> str:
    key = os.environ.get("FLASK_SECRET_KEY")
    if not key:
        log.warning("FLASK_SECRET_KEY is not set; using an insecure development key")
        return DEV_SECRET_KEY
    return key


def _register_errors(app: Flask) -> None:
    @app.errorhandler(ValidationError)
    def bad_input(err: ValidationError):
        if request.method == "POST":
            flash(err.message, "error")
            return redirect(back_path("/"))
        return render("errors/400.html", status=400, message=err.message)

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
    )
    app.config.update(config or {})
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret_key()
    if not app.testing:
        app.config["TOLERATE_MISSING_TEMPLATES"] = False   # never in production
    ddragon.register(app)
    for blueprint in (pages.bp, builds.bp, account.bp):
        app.register_blueprint(blueprint)
    _register_errors(app)
    _register_context(app)
    app.teardown_appcontext(close_conn)
    return app
