"""Saved builds: list, save, view, edit, delete. Only the owner sees or edits."""
from __future__ import annotations

from urllib.parse import quote

from flask import Blueprint, flash, redirect, request

from laneforge.queries import catalog, saved
from laneforge.queries.errors import ValidationError
from laneforge.web.blueprints import back_path, current_user, get_conn, not_found, render
from laneforge.web.forms import parse_flag, parse_item_ids, parse_matchup

bp = Blueprint("builds", __name__)


def _signin_redirect(next_path: str, message: str):
    flash(message)
    return redirect(f"/signin?next={quote(next_path)}")


def _owned_build(build_id: int):
    """The build if the signed-in user owns it; 404 otherwise (no existence leak)."""
    user = current_user()
    build = saved.get_build(get_conn(), build_id)
    if build is None or user is None or build.user.user_id != user.user_id:
        not_found(f"There is no saved build {build_id} in your file.")
    return build


@bp.get("/builds")
def list_builds():
    user = current_user()
    if user is None:
        return _signin_redirect("/builds", "Sign in to see your saved builds.")
    return render("builds.html", builds=saved.list_builds(get_conn(), user.user_id))


@bp.post("/builds")
def create():
    user = current_user()
    if user is None:
        return _signin_redirect(back_path("/"), "Sign in to save builds.")
    form = request.form
    query = parse_matchup(form)
    build_id = saved.create_build(
        get_conn(), user.user_id, name=form.get("name"), notes=form.get("notes"),
        champion_id=query.champion_id, role=query.role,
        opponent_champion_id=query.opponent_champion_id, enemy_champion_ids=query.enemy_ids,
        item_ids=parse_item_ids(form), observed=parse_flag(form.get("observed")),
    )
    flash("Build saved.")
    return redirect(f"/builds/{build_id}")


@bp.get("/builds/<int:build_id>")
def detail(build_id: int):
    if current_user() is None:
        return _signin_redirect(f"/builds/{build_id}", "Sign in to see your saved builds.")
    build = _owned_build(build_id)
    return render("build.html", build=build,
                  legendary_items=catalog.list_items(get_conn(), legendary_only=True))


@bp.post("/builds/<int:build_id>/items")
def update_items(build_id: int):
    _owned_build(build_id)
    saved.update_items(get_conn(), build_id, parse_item_ids(request.form))
    flash("Items updated; the build is now marked customized.")
    return redirect(f"/builds/{build_id}")


@bp.post("/builds/<int:build_id>/rename")
def rename(build_id: int):
    _owned_build(build_id)
    saved.rename_build(get_conn(), build_id, request.form.get("name"), request.form.get("notes"))
    flash("Build renamed.")
    return redirect(f"/builds/{build_id}")


@bp.post("/builds/<int:build_id>/delete")
def delete(build_id: int):
    build = _owned_build(build_id)
    if not parse_flag(request.form.get("confirm")):
        raise ValidationError("Tick the confirmation box to delete the build.")
    saved.delete_build(get_conn(), build_id)
    flash(f"Deleted “{build.name}”.")
    return redirect("/builds")
