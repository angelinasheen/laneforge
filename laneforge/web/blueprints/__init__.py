"""Helpers shared by the blueprints: the per-request connection, the signed-in
user, and template rendering."""
from __future__ import annotations

import logging

from flask import abort, current_app, g, render_template, session
from jinja2 import TemplateNotFound

from laneforge import db
from laneforge.queries import catalog, users
from laneforge.queries.models import DatasetSummary, UserRef

log = logging.getLogger(__name__)


def get_conn():
    """One connection per request, opened on first use, closed in teardown."""
    if "db_conn" not in g:
        g.db_conn = db.connect(current_app.config["DATABASE_URL"])
    return g.db_conn


def close_conn(exc: BaseException | None = None) -> None:
    conn = g.pop("db_conn", None)
    if conn is None:
        return
    try:
        if exc is not None:
            conn.rollback()
    finally:
        conn.close()


def current_user() -> UserRef | None:
    if "current_user" not in g:
        user_id = session.get("user_id")
        user = users.get_user(get_conn(), user_id) if isinstance(user_id, int) else None
        if user is None and user_id is not None:
            session.pop("user_id", None)       # account vanished: sign out quietly
        g.current_user = user
    return g.current_user


def dataset() -> DatasetSummary:
    if "dataset" not in g:
        g.dataset = catalog.dataset_summary(get_conn())
    return g.dataset


def render(template: str, status: int = 200, **context):
    """render_template, except that tests may run before the UI templates exist.

    TOLERATE_MISSING_TEMPLATES is only ever set by the test suite."""
    try:
        return render_template(template, **context), status
    except TemplateNotFound as missing:
        if not current_app.config.get("TOLERATE_MISSING_TEMPLATES"):
            raise
        log.warning("template %s missing; returning a placeholder (tests only)", missing)
        body = f"[missing template {template}] context: {', '.join(sorted(context))}"
        return body, status, {"Content-Type": "text/plain; charset=utf-8"}


def not_found(message: str):
    abort(404, description=message)


def back_path(default: str = "/") -> str:
    """The referring page's path if it is on this site, else `default`."""
    from urllib.parse import urlsplit

    from flask import request

    ref = urlsplit(request.referrer or "")
    if ref.netloc and ref.netloc != request.host:
        return default
    path = ref.path or ""
    if not path.startswith("/") or path.startswith("//"):
        return default
    return path + (f"?{ref.query}" if ref.query else "")
