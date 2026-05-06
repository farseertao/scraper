from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlencode

from sqlalchemy.engine import Engine
from wsgiref.simple_server import make_server

from .config import Settings
from .db import (
    fetch_clue_pool_admin,
    fetch_dashboard_stats,
    fetch_job_runs,
    fetch_recent_failures,
    fetch_source_admin_rows,
    set_source_active,
)
from .jobs import run_job_recorded, run_source_operation, run_weekly_job
from .notifications import notify_clue_pool

logger = logging.getLogger(__name__)


def _require_admin_credentials(settings: Settings) -> None:
    if not settings.admin_username or not settings.admin_password:
        raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD must be configured before starting the admin server.")


def _cookie_secret(settings: Settings) -> bytes:
    secret = settings.app_secret_key or settings.admin_password
    if not secret:
        raise RuntimeError("APP_SECRET_KEY or ADMIN_PASSWORD must be configured.")
    return secret.encode("utf-8")


def _make_session_token(settings: Settings) -> str:
    issued_at = str(int(time.time()))
    payload = f"{settings.admin_username}|{issued_at}"
    signature = hmac.new(_cookie_secret(settings), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode("ascii")


def _is_authenticated(settings: Settings, environ: dict) -> bool:
    cookie_header = environ.get("HTTP_COOKIE", "")
    cookies = {}
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        cookies[name] = value
    token = cookies.get("zj_agent_session")
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, issued_at_text, signature = raw.split("|", 2)
        payload = f"{username}|{issued_at_text}"
    except Exception:
        return False
    expected = hmac.new(_cookie_secret(settings), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    if username != settings.admin_username:
        return False
    max_age = settings.admin_session_hours * 3600
    return int(time.time()) - int(issued_at_text) <= max_age


def _parse_form(environ: dict) -> dict[str, str]:
    content_length = int(environ.get("CONTENT_LENGTH") or "0")
    body = environ["wsgi.input"].read(content_length).decode("utf-8") if content_length else ""
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _html_page(title: str, body: str, *, flash: str | None = None) -> str:
    flash_html = f"<div class='flash'>{html.escape(flash)}</div>" if flash else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f6f7fb; margin: 0; color: #1f2937; }}
    header {{ background: #111827; color: white; padding: 16px 24px; }}
    nav a {{ color: #dbeafe; margin-right: 16px; text-decoration: none; }}
    main {{ padding: 24px; max-width: 1400px; margin: 0 auto; }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .card {{ background: white; border-radius: 12px; padding: 16px; box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08); }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
    th {{ background: #f3f4f6; font-weight: 600; }}
    form.inline {{ display: inline; }}
    .muted {{ color: #6b7280; font-size: 13px; }}
    .flash {{ background: #dcfce7; color: #166534; padding: 12px 16px; border-radius: 10px; margin-bottom: 16px; }}
    .danger {{ color: #b91c1c; }}
    .pill {{ display: inline-block; padding: 2px 10px; border-radius: 999px; background: #e5e7eb; font-size: 12px; }}
    .pill.ok {{ background: #dcfce7; color: #166534; }}
    .pill.warn {{ background: #fee2e2; color: #991b1b; }}
    input, select {{ padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 8px; }}
    button {{ padding: 8px 12px; border-radius: 8px; border: 0; background: #2563eb; color: white; cursor: pointer; }}
    button.secondary {{ background: #374151; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }}
    .login {{ max-width: 360px; margin: 72px auto; }}
    .login input {{ width: 100%; box-sizing: border-box; }}
    pre {{ white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body>
  {body if "<main>" in body else f"<main>{flash_html}{body}</main>"}
</body>
</html>"""


def _layout(title: str, inner: str, *, flash: str | None = None) -> str:
    body = f"""
<header>
  <div style="display:flex;justify-content:space-between;align-items:center;gap:16px;">
    <div>
      <strong>zj-agent admin</strong>
      <div class="muted" style="color:#9ca3af;">Linux single-node operations console</div>
    </div>
    <nav>
      <a href="/">Dashboard</a>
      <a href="/sources">Sources</a>
      <a href="/clues">Clue Pool</a>
      <a href="/jobs">Jobs</a>
      <a href="/logout">Logout</a>
    </nav>
  </div>
</header>
<main>
  {f"<div class='flash'>{html.escape(flash)}</div>" if flash else ""}
  {inner}
</main>
"""
    return _html_page(title, body)


@dataclass
class Response:
    status: str
    body: str
    headers: list[tuple[str, str]]


class AdminApp:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings

    def __call__(self, environ: dict, start_response: Callable):
        try:
            response = self._dispatch(environ)
        except Exception as exc:
            logger.exception("Admin server error")
            response = Response(
                status="500 Internal Server Error",
                body=_html_page("Server Error", f"<main><h1>Server error</h1><pre>{html.escape(str(exc))}</pre></main>"),
                headers=[("Content-Type", "text/html; charset=utf-8")],
            )
        start_response(response.status, response.headers)
        return [response.body.encode("utf-8")]

    def _dispatch(self, environ: dict) -> Response:
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        flash = query.get("msg", [None])[0]

        if path == "/login":
            if method == "POST":
                return self._handle_login(environ)
            return Response(
                status="200 OK",
                body=self._render_login(query.get("error", [None])[0]),
                headers=[("Content-Type", "text/html; charset=utf-8")],
            )
        if path == "/logout":
            return Response(
                status="302 Found",
                body="",
                headers=[
                    ("Location", "/login"),
                    ("Set-Cookie", "zj_agent_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"),
                ],
            )

        if not _is_authenticated(self.settings, environ):
            return Response(
                status="302 Found",
                body="",
                headers=[("Location", "/login")],
            )

        if method == "POST":
            return self._handle_action(path, environ)
        if path == "/":
            return self._dashboard(flash)
        if path == "/sources":
            return self._sources_page(flash)
        if path == "/clues":
            return self._clues_page(query, flash)
        if path == "/jobs":
            return self._jobs_page(flash)
        return Response(
            status="404 Not Found",
            body=_html_page("Not Found", "<main><h1>Not Found</h1></main>"),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    def _render_login(self, error: str | None) -> str:
        error_html = f"<p class='danger'>{html.escape(error)}</p>" if error else ""
        return _html_page(
            "Login",
            f"""
<main class="login">
  <div class="card">
    <h1>zj-agent admin</h1>
    <p class="muted">Sign in with the admin account configured in <code>.env</code>.</p>
    {error_html}
    <form method="post" action="/login">
      <label>Username</label><br>
      <input name="username" autocomplete="username"><br><br>
      <label>Password</label><br>
      <input name="password" type="password" autocomplete="current-password"><br><br>
      <button type="submit">Login</button>
    </form>
  </div>
</main>
""",
        )

    def _handle_login(self, environ: dict) -> Response:
        form = _parse_form(environ)
        if (
            form.get("username", "").strip() != self.settings.admin_username
            or form.get("password", "") != self.settings.admin_password
        ):
            return Response(
                status="302 Found",
                body="",
                headers=[("Location", "/login?error=Invalid+credentials")],
            )
        token = _make_session_token(self.settings)
        return Response(
            status="302 Found",
            body="",
            headers=[
                ("Location", "/"),
                ("Set-Cookie", f"zj_agent_session={token}; Path=/; HttpOnly; SameSite=Lax"),
            ],
        )

    def _dashboard(self, flash: str | None) -> Response:
        stats = fetch_dashboard_stats(self.engine)
        jobs = fetch_job_runs(self.engine, limit=8)
        cards = f"""
<div class="grid">
  <div class="card"><h3>Active sources</h3><div>{stats['active_sources']} / {stats['total_sources']}</div></div>
  <div class="card"><h3>Pending failures</h3><div>{stats['pending_failures']}</div></div>
  <div class="card"><h3>Pending clue_pool</h3><div>{stats['pending_pool_items']}</div></div>
  <div class="card"><h3>Ready to push</h3><div>{stats['ready_to_push']}</div></div>
</div>
"""
        controls = """
<div class="card" style="margin-top:16px;">
  <h3>Quick actions</h3>
  <div class="toolbar">
    <form class="inline" method="post" action="/actions/run-weekly-job"><button type="submit">Run weekly job now</button></form>
    <form class="inline" method="post" action="/actions/resend-notify"><input type="hidden" name="source_key" value=""><button class="secondary" type="submit">Resend weekly summary</button></form>
  </div>
</div>
"""
        rows = []
        for job in jobs:
            status_class = "ok" if job["status"] == "succeeded" else "warn"
            rows.append(
                f"<tr><td>{job['started_at']}</td><td>{html.escape(job['job_type'])}</td>"
                f"<td>{html.escape(job['source_key'] or '-')}</td>"
                f"<td><span class='pill {status_class}'>{html.escape(job['status'])}</span></td>"
                f"<td>{html.escape(job['trigger_mode'])}</td>"
                f"<td><pre>{html.escape(str(job.get('summary') or ''))}</pre></td></tr>"
            )
        jobs_html = (
            "<div class='card' style='margin-top:16px;'><h3>Recent jobs</h3><table><thead>"
            "<tr><th>Started</th><th>Job</th><th>Source</th><th>Status</th><th>Trigger</th><th>Summary</th></tr>"
            "</thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
        return Response(
            status="200 OK",
            body=_layout("Dashboard", cards + controls + jobs_html, flash=flash),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    def _sources_page(self, flash: str | None) -> Response:
        rows = fetch_source_admin_rows(self.engine)
        body_rows = []
        for row in rows:
            active = bool(row["is_active"])
            toggle_label = "Disable" if active else "Enable"
            run_buttons = (
                f"<form class='inline' method='post' action='/actions/run-source'>"
                f"<input type='hidden' name='source_key' value='{html.escape(row['source_key'])}'>"
                f"<input type='hidden' name='operation' value='inspect'>"
                f"<button class='secondary' type='submit'>Inspect</button></form> "
                f"<form class='inline' method='post' action='/actions/run-source'>"
                f"<input type='hidden' name='source_key' value='{html.escape(row['source_key'])}'>"
                f"<input type='hidden' name='operation' value='weekly'>"
                f"<button class='secondary' type='submit'>Run weekly</button></form> "
                f"<form class='inline' method='post' action='/actions/retry-failures'>"
                f"<input type='hidden' name='source_key' value='{html.escape(row['source_key'])}'>"
                f"<button class='secondary' type='submit'>Retry failures</button></form>"
            )
            toggle_form = (
                f"<form class='inline' method='post' action='/actions/toggle-source'>"
                f"<input type='hidden' name='source_key' value='{html.escape(row['source_key'])}'>"
                f"<input type='hidden' name='is_active' value='{'0' if active else '1'}'>"
                f"<button type='submit'>{toggle_label}</button></form>"
            )
            body_rows.append(
                "<tr>"
                f"<td>{html.escape(row['name'])}<div class='muted'>{html.escape(row['source_key'])}</div></td>"
                f"<td><span class='pill {'ok' if active else 'warn'}'>{'active' if active else 'inactive'}</span></td>"
                f"<td><pre>{html.escape(row['entry_urls'] or '')}</pre></td>"
                f"<td>{row['candidate_count']} / {row['raw_doc_count']} / {row['pool_count']}</td>"
                f"<td>{row['pending_failure_count']}</td>"
                f"<td>{html.escape(str(row['last_seen_published_at'] or '-'))}</td>"
                f"<td>{toggle_form}<div style='margin-top:8px'>{run_buttons}</div></td>"
                "</tr>"
            )
        body = (
            "<div class='card'><h2>Sources</h2><p class='muted'>Counts are candidate / raw document / clue pool.</p>"
            "<table><thead><tr><th>Source</th><th>Status</th><th>Entry URLs</th><th>Counts</th><th>Failures</th><th>Last published</th><th>Actions</th></tr></thead><tbody>"
            + "".join(body_rows)
            + "</tbody></table></div>"
        )
        return Response(
            status="200 OK",
            body=_layout("Sources", body, flash=flash),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    def _clues_page(self, query: dict[str, list[str]], flash: str | None) -> Response:
        source_key = query.get("source", [""])[0] or None
        pool_status = query.get("status", [""])[0] or None
        days_text = query.get("days", ["7"])[0]
        limit_text = query.get("limit", ["50"])[0]
        rows = fetch_clue_pool_admin(
            self.engine,
            source_key=source_key,
            pool_status=pool_status,
            days=int(days_text) if days_text else None,
            limit=int(limit_text),
        )
        form = f"""
<div class="card">
  <h2>Clue pool</h2>
  <form class="toolbar" method="get" action="/clues">
    <label>Source <input name="source" value="{html.escape(source_key or '')}"></label>
    <label>Status
      <select name="status">
        <option value="" {'selected' if not pool_status else ''}>all</option>
        <option value="pending" {'selected' if pool_status == 'pending' else ''}>pending</option>
        <option value="confirmed" {'selected' if pool_status == 'confirmed' else ''}>confirmed</option>
        <option value="ignored" {'selected' if pool_status == 'ignored' else ''}>ignored</option>
      </select>
    </label>
    <label>Days <input name="days" value="{html.escape(days_text)}" size="4"></label>
    <label>Limit <input name="limit" value="{html.escape(limit_text)}" size="4"></label>
    <button type="submit">Filter</button>
  </form>
"""
        table_rows = []
        for row in rows:
            table_rows.append(
                "<tr>"
                f"<td>{html.escape(row['source_name'])}</td>"
                f"<td>{html.escape(_safe_text(row['published_at']))}</td>"
                f"<td><a href='{html.escape(row['page_url'])}' target='_blank'>{html.escape(row['title'])}</a></td>"
                f"<td>{html.escape(str(row['investment_relevance'] or '-'))} / {html.escape(str(row['relevance_score'] or 0))}</td>"
                f"<td>{html.escape(row['core_technology'] or '-')}</td>"
                f"<td>{html.escape(row['summary'] or '-')}</td>"
                f"<td>{html.escape(str(row['pushed_at'] or '-'))}</td>"
                "</tr>"
            )
        body = form + (
            "<table><thead><tr><th>Source</th><th>Published</th><th>Title</th><th>Score</th><th>Core technology</th><th>Summary</th><th>Pushed</th></tr></thead><tbody>"
            + "".join(table_rows)
            + "</tbody></table></div>"
        )
        return Response(
            status="200 OK",
            body=_layout("Clue Pool", body, flash=flash),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    def _jobs_page(self, flash: str | None) -> Response:
        jobs = fetch_job_runs(self.engine, limit=30)
        failures = fetch_recent_failures(self.engine, limit=20)
        job_rows = []
        for job in jobs:
            job_rows.append(
                "<tr>"
                f"<td>{html.escape(_safe_text(job['started_at']))}</td>"
                f"<td>{html.escape(job['job_type'])}</td>"
                f"<td>{html.escape(job['source_key'] or '-')}</td>"
                f"<td>{html.escape(job['status'])}</td>"
                f"<td>{html.escape(job['trigger_mode'])}</td>"
                f"<td><pre>{html.escape(str(job.get('summary') or job.get('error_message') or ''))}</pre></td>"
                "</tr>"
            )
        failure_rows = []
        for row in failures:
            failure_rows.append(
                "<tr>"
                f"<td>{html.escape(row['source_name'])}</td>"
                f"<td>{html.escape(_safe_text(row['last_error_at']))}</td>"
                f"<td>{html.escape(row['error_stage'])}</td>"
                f"<td><a href='{html.escape(row['page_url'])}' target='_blank'>{html.escape(row['page_url'])}</a></td>"
                f"<td>{html.escape(str(row['retry_count']))}</td>"
                f"<td>{html.escape(row['error_message'][:180])}</td>"
                "</tr>"
            )
        body = (
            "<div class='card'><h2>Recent jobs</h2><table><thead><tr><th>Started</th><th>Job</th><th>Source</th><th>Status</th><th>Trigger</th><th>Summary</th></tr></thead><tbody>"
            + "".join(job_rows)
            + "</tbody></table></div>"
            "<div class='card' style='margin-top:16px;'><h2>Pending failures</h2><table><thead><tr><th>Source</th><th>Time</th><th>Stage</th><th>URL</th><th>Retry</th><th>Error</th></tr></thead><tbody>"
            + "".join(failure_rows)
            + "</tbody></table></div>"
        )
        return Response(
            status="200 OK",
            body=_layout("Jobs", body, flash=flash),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    def _handle_action(self, path: str, environ: dict) -> Response:
        form = _parse_form(environ)
        if path == "/actions/toggle-source":
            source_key = form["source_key"]
            is_active = form.get("is_active") == "1"
            set_source_active(self.engine, source_key, is_active)
            return self._redirect("/sources", f"{source_key} updated")
        if path == "/actions/run-weekly-job":
            summary = run_job_recorded(
                self.engine,
                job_type="weekly-job",
                trigger_mode="admin",
                source_key=None,
                runner=lambda: run_weekly_job(self.engine, self.settings),
            )
            return self._redirect("/", f"weekly job finished: {summary['notify'].get('sent_docs', 0)} clues pushed")
        if path == "/actions/run-source":
            source_key = form["source_key"]
            operation = form["operation"]
            summary = run_job_recorded(
                self.engine,
                job_type=f"source-{operation}",
                trigger_mode="admin",
                source_key=source_key,
                runner=lambda: run_source_operation(
                    self.engine,
                    self.settings,
                    source_key=source_key,
                    operation=operation,
                ),
            )
            return self._redirect("/sources", f"{source_key} {operation} finished: {summary}")
        if path == "/actions/retry-failures":
            source_key = form["source_key"]
            summary = run_job_recorded(
                self.engine,
                job_type="retry-failures",
                trigger_mode="admin",
                source_key=source_key,
                runner=lambda: run_source_operation(
                    self.engine,
                    self.settings,
                    source_key=source_key,
                    operation="retry-failures",
                ),
            )
            return self._redirect("/jobs", f"{source_key} retry complete: {summary}")
        if path == "/actions/resend-notify":
            source_key = form.get("source_key") or None
            stats = notify_clue_pool(
                self.engine,
                self.settings,
                source_key=source_key,
                limit=self.settings.weekly_notify_limit,
                resend=True,
            )
            return self._redirect("/", f"resend complete: {stats['sent_docs']} clues in {stats['sent_messages']} message")
        return Response(
            status="404 Not Found",
            body=_html_page("Not Found", "<main><h1>Not Found</h1></main>"),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    def _redirect(self, location: str, message: str | None = None) -> Response:
        if message:
            location = f"{location}?{urlencode({'msg': message})}"
        return Response(status="302 Found", body="", headers=[("Location", location)])


def _safe_text(value) -> str:
    return "-" if value in (None, "") else str(value)


def serve_admin(engine: Engine, settings: Settings, *, host: str, port: int) -> None:
    _require_admin_credentials(settings)
    app = AdminApp(engine, settings)
    logger.info("Starting admin server on %s:%s", host, port)
    with make_server(host, port, app) as server:
        server.serve_forever()
