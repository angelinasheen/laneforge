"""Display-name sign-in. The session holds user_id and nothing else."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, request, session

from laneforge.queries import users
from laneforge.web.blueprints import get_conn, render
from laneforge.web.forms import safe_next

bp = Blueprint("account", __name__)


@bp.get("/signin")
def signin_form():
    return render("signin.html")


@bp.post("/signin")
def signin():
    user = users.get_or_create_user(get_conn(), request.form.get("display_name"))
    session.clear()
    session["user_id"] = user.user_id
    flash(f"Signed in as {user.display_name}.")
    return redirect(safe_next(request.form.get("next"), "/builds"))


@bp.route("/signout", methods=("GET", "POST"))
def signout():
    session.clear()
    flash("Signed out.")
    return redirect("/")
