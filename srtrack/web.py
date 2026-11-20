"""Browser UI for srtrack (Flask).

Configuration (environment variables):
  SRT_DB              path of the SQLite database
  SRT_PASSWORD        password for HTTP basic auth (required when served publicly)
  SRT_USERNAME        username for basic auth (default: srt)
  SRT_SECRET_KEY      session signing key (default: generated and kept next to the DB)
"""

from __future__ import annotations

import csv
import hmac
import io
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .io import export_csv, export_json, import_csv
from .reports import agenda, stats
from .store import SEVERITIES, STATUSES, SRError, Store, default_db_path


def _secret_key(db_path: str) -> str:
    env = os.environ.get("SRT_SECRET_KEY")
    if env:
        return env
    if db_path == ":memory:":
        return secrets.token_hex(32)
    key_file = Path(db_path).with_name("secret.key")
    try:
        return key_file.read_text().strip()
    except FileNotFoundError:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        key_file.write_text(key)
        key_file.chmod(0o600)
        return key


def create_app(
    db_path: Optional[str] = None,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Flask:
    db_path = str(db_path or default_db_path())
    username = username or os.environ.get("SRT_USERNAME") or "srt"
    password = password if password is not None else os.environ.get("SRT_PASSWORD") or None

    app = Flask(__name__)
    app.config["SRT_DB"] = db_path
    app.secret_key = _secret_key(db_path)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    # Initialise the schema once at start-up.
    Store(db_path).close()

    # -- request plumbing ---------------------------------------------------

    def store() -> Store:
        if "store" not in g:
            g.store = Store(app.config["SRT_DB"])
        return g.store

    @app.teardown_appcontext
    def _close_store(exc):
        s = g.pop("store", None)
        if s is not None:
            s.close()

    @app.before_request
    def _auth_and_csrf():
        if request.endpoint in ("healthz", "static"):
            return None
        if password:
            auth = request.authorization
            ok = (
                auth is not None
                and auth.type == "basic"
                and hmac.compare_digest((auth.username or "").encode(), username.encode())
                and hmac.compare_digest((auth.password or "").encode(), password.encode())
            )
            if not ok:
                return Response(
                    "Authentication required", 401, {"WWW-Authenticate": 'Basic realm="srtrack"'}
                )
        if request.method == "POST":
            # Reject cross-site form posts (browsers always send Origin on them).
            source = request.headers.get("Origin") or request.headers.get("Referer")
            if source and urlsplit(source).netloc != request.host:
                abort(403, "cross-site request rejected")
        return None

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.errorhandler(SRError)
    def _sr_error(exc):
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("index"))

    @app.context_processor
    def _globals():
        return {
            "STATUSES": STATUSES,
            "SEVERITIES": SEVERITIES,
            "today": date.today(),
            "owner_default": os.environ.get("SRT_OWNER", ""),
        }

    @app.template_filter("days_until")
    def _days_until(iso: Optional[str]) -> Optional[int]:
        return (date.fromisoformat(iso) - date.today()).days if iso else None

    # -- pages --------------------------------------------------------------

    @app.get("/healthz")
    def healthz():
        store().conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    def index():
        stale = request.args.get("stale", 7, type=int)
        return render_template("index.html", ag=agenda(store(), stale), stale=stale)

    @app.get("/srs")
    def sr_list():
        args = request.args
        scope = args.get("scope", "open")
        filters = dict(
            include_closed=scope == "all",
            only_closed=scope == "closed",
            statuses=args.getlist("status") or None,
            severities=args.getlist("sev") or None,
            waiting_on=args.get("waiting") or None,
            product=args.get("product") or None,
            owner=args.get("owner") or None,
            tag=args.get("tag") or None,
            text=args.get("q") or None,
            sort=args.get("sort", "priority"),
        )
        if args.get("due"):
            filters["due_by"] = date.today().isoformat()
        srs = store().query(**filters)
        return render_template("list.html", srs=srs, args=args, scope=scope)

    @app.route("/srs/new", methods=["GET", "POST"])
    def sr_new():
        if request.method == "GET":
            return render_template("new.html", form={})
        f = request.form
        try:
            sr = store().add(
                f.get("number", ""),
                f.get("title", ""),
                product=f.get("product", ""),
                severity=f.get("severity", 3),
                status=f.get("status", "NEW"),
                owner=f.get("owner", ""),
                oracle_engineer=f.get("oracle_engineer", ""),
                environment=f.get("environment", ""),
                next_action=f.get("next_action", ""),
                follow_up=f.get("follow_up") or None,
                tags=f.get("tags", ""),
                bugs=f.get("bugs", ""),
                opened_at=f.get("opened_at") or None,
                note=f.get("note", ""),
            )
        except SRError as exc:
            flash(str(exc), "error")
            return render_template("new.html", form=f), 400
        flash(f"Added {sr.number}", "ok")
        return redirect(url_for("sr_detail", number=sr.number))

    @app.get("/srs/<number>")
    def sr_detail(number):
        try:
            sr = store().get(number)
        except SRError:
            abort(404)
        if sr.number != number:
            return redirect(url_for("sr_detail", number=sr.number))
        events = list(reversed(store().events(sr.number)))
        return render_template("sr.html", sr=sr, events=events)

    @app.post("/srs/<number>/edit")
    def sr_edit(number):
        f = request.form
        fields = (
            "title", "product", "severity", "status", "owner", "oracle_engineer",
            "environment", "next_action", "follow_up", "tags", "bugs", "resolution", "opened_at",
        )
        changes = {k: f[k] for k in fields if k in f}
        sr, descriptions = store().update(number, **changes)
        flash(f"Saved: {'; '.join(descriptions)}" if descriptions else "Nothing changed", "ok")
        return redirect(url_for("sr_detail", number=sr.number))

    @app.post("/srs/<number>/note")
    def sr_note(number):
        f = request.form
        text = f.get("text", "").strip()
        number = store().resolve(number)
        extra = {}
        if f.get("status"):
            extra["status"] = f["status"]
        if f.get("follow_up"):
            extra["follow_up"] = f["follow_up"]
        if f.get("next_action"):
            extra["next_action"] = f["next_action"]
        if not text and not extra:
            raise SRError("write a note or change something")
        if extra:
            store().update(number, **extra)
        if text:
            store().add_note(number, text)
        flash("Update recorded", "ok")
        return redirect(url_for("sr_detail", number=number))

    @app.post("/srs/<number>/close")
    def sr_close(number):
        changes = {"status": "CLO", "follow_up": "", "next_action": ""}
        if request.form.get("resolution", "").strip():
            changes["resolution"] = request.form["resolution"]
        sr, _ = store().update(number, **changes)
        flash(f"Closed {sr.number} after {sr.age_days()} days", "ok")
        return redirect(url_for("sr_detail", number=sr.number))

    @app.post("/srs/<number>/delete")
    def sr_delete(number):
        if request.form.get("confirm") != "yes":
            raise SRError("tick the box to confirm deletion")
        deleted = store().delete(number)
        flash(f"Deleted {deleted}", "ok")
        return redirect(url_for("sr_list"))

    @app.get("/go")
    def go():
        """Jump box: an SR number/suffix opens it, anything else searches."""
        q = request.args.get("q", "").strip()
        if not q:
            return redirect(url_for("index"))
        try:
            return redirect(url_for("sr_detail", number=store().resolve(q)))
        except SRError:
            return redirect(url_for("sr_list", q=q, scope="all"))

    @app.get("/stats")
    def stats_page():
        return render_template("stats.html", st=stats(store()))

    @app.get("/export.<fmt>")
    def export(fmt):
        srs = store().query(include_closed=True, sort="number")
        buf = io.StringIO()
        if fmt == "csv":
            export_csv(srs, buf)
            mime = "text/csv"
        elif fmt == "json":
            export_json(store(), srs, buf)
            mime = "application/json"
        else:
            abort(404)
        name = f"oracle-srs-{date.today().isoformat()}.{fmt}"
        return Response(
            buf.getvalue(), mimetype=mime, headers={"Content-Disposition": f"attachment; filename={name}"}
        )

    @app.route("/import", methods=["GET", "POST"])
    def import_page():
        if request.method == "GET":
            return render_template("import.html", result=None)
        upload = request.files.get("file")
        if not upload or not upload.filename:
            raise SRError("choose a CSV file")
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SRError("the file is not UTF-8 text; save it as CSV UTF-8") from None
        try:
            result = import_csv(store(), io.StringIO(text, newline=""), update_existing=bool(request.form.get("update")))
        except csv.Error as exc:
            raise SRError(f"could not read CSV: {exc}") from None
        return render_template("import.html", result=result)

    return app
