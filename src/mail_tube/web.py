from __future__ import annotations

from datetime import datetime, timezone
import html
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse

from .db import Database
from .refresh import refresh_all_profiles, refresh_profile
from .youtube import build_embed_url

RESOURCES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "resources"))
ALLOWED_RESOURCE_FILES = {"Email-logo.png", "Youtube-logo.png", "MailTube-logo_v0.png"}


def _to_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _safe_return_to(value: str | None, *, default: str = "/inbox") -> str:
    if not value:
        return default
    candidate = value.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    return candidate


def _relative_published_label(value: str | None) -> str:
    if not value:
        return "unknown time"

    try:
        raw = value.replace("Z", "+00:00")
        published_at = datetime.fromisoformat(raw)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        published_day = published_at.astimezone(timezone.utc).date()
        now_day = datetime.now(timezone.utc).date()
        day_delta = (now_day - published_day).days
    except ValueError:
        return "unknown time"

    if day_delta <= 0:
        return "today"
    if day_delta == 1:
        return "yesterday"
    if day_delta < 30:
        return f"{day_delta} days ago"
    if day_delta < 365:
        months = day_delta // 30
        unit = "month" if months == 1 else "months"
        return f"{months} {unit} ago"
    years = day_delta // 365
    unit = "year" if years == 1 else "years"
    return f"{years} {unit} ago"


def _safe_display_text(value: str | None) -> str:
    if value is None:
        return ""
    return html.escape(html.unescape(value))


EYE_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M2 12s3.8-6 10-6 10 6 10 6-3.8 6-10 6-10-6-10-6"></path>'
    '<circle cx="12" cy="12" r="3"></circle>'
    "</svg>"
)

TRASH_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M3 6h18"></path>'
    '<path d="M8 6V4h8v2"></path>'
    '<path d="M19 6l-1 14H6L5 6"></path>'
    '<path d="M10 11v6"></path>'
    '<path d="M14 11v6"></path>'
    "</svg>"
)

STAR_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M12 3.8l2.5 5.1 5.6.8-4.1 4 1 5.7L12 16.8 7 19.4l1-5.7-4.1-4 5.6-.8L12 3.8"></path>'
    "</svg>"
)

STAR_FILLED_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path fill="currentColor" stroke="none" d="M12 3.8l2.5 5.1 5.6.8-4.1 4 1 5.7L12 16.8 7 19.4l1-5.7-4.1-4 5.6-.8L12 3.8"></path>'
    "</svg>"
)

AVATAR_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<circle cx="12" cy="8" r="3.2"></circle>'
    '<path d="M5.5 19c.9-3 3.4-5 6.5-5s5.6 2 6.5 5"></path>'
    '<circle cx="12" cy="12" r="9.2"></circle>'
    "</svg>"
)

BACK_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M15 18l-6-6 6-6"></path>'
    "</svg>"
)

FILTER_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M4 6h16l-6.5 7.5V19l-3 1v-6.5L4 6"></path>'
    "</svg>"
)


def _base_layout(title: str, body: str) -> str:
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      --bg: #0f1f36;
      --panel: #182a44;
      --panel-2: #1f3452;
      --border: #314b70;
      --text: #f2f7ff;
      --muted: #b8c9e2;
      --primary: #4cc9f0;
      --warning: #ff7b72;
      --ok: #4caf50;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at top, #19325a, var(--bg) 58%);
      color: var(--text);
      font-family: Merriweather, "Iowan Old Style", Georgia, "Times New Roman", serif;
      min-height: 100vh;
    }}
    .app-header {{
      position: fixed;
      top: 8px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 950;
      font-family: "Google Sans", "Product Sans", "HelveticaInseratLTStd-Condensed", "Helvetica Neue Condensed Bold", "Arial Narrow", "Nimbus Sans Narrow", "Liberation Sans Narrow", sans-serif;
      font-size: 2.75rem;
      letter-spacing: 0.01em;
      font-weight: 700;
      font-stretch: normal;
      line-height: 1.08;
      padding: 0 0.08em;
      color: #e8f2ff;
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.2);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      pointer-events: none;
    }}
    .app-header-inner {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
    }}
    .app-header-text {{
      display: inline-flex;
      align-items: baseline;
      gap: 0;
      white-space: nowrap;
    }}
    .app-header-logo {{
      width: 52px;
      height: 52px;
      object-fit: contain;
      opacity: 0.95;
      transform: translateY(-5px);
    }}
    .app-header-mail {{
      color: #e8f2ff;
    }}
    .app-header-tube {{
      color: #89a3c2;
    }}
    main {{
      width: min(1100px, 95vw);
      margin: 94px auto 40px auto;
      display: grid;
      gap: 16px;
      padding-bottom: 84px;
    }}
    .card {{
      background: linear-gradient(180deg, var(--panel-2), var(--panel));
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
    }}
    .muted {{ color: var(--muted); }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      text-align: left;
      border-top: 1px solid var(--border);
      padding: 10px 8px;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    input, select, button {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
      background: #14243c;
      color: var(--text);
    }}
    .minimal-select {{
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      min-width: 150px;
      padding: 8px 34px 8px 12px;
      border-radius: 999px;
      border-color: #3a567f;
      background-color: rgba(20, 36, 60, 0.95);
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'%3E%3Cpath d='M3 5l4 4 4-4' fill='none' stroke='%23c5d4ea' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 10px center;
      background-size: 12px 12px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
      transition: border-color 120ms ease, background-color 120ms ease, box-shadow 120ms ease;
      cursor: pointer;
      line-height: 1.2;
    }}
    .minimal-select:hover {{
      border-color: #4a6993;
      background-color: rgba(25, 45, 72, 0.98);
    }}
    .minimal-select:focus {{
      outline: none;
      border-color: #7fb2de;
      box-shadow: 0 0 0 3px rgba(127, 178, 222, 0.18);
    }}
    .minimal-select option {{
      background: #162843;
      color: var(--text);
    }}
    button {{
      cursor: pointer;
      background: #1d3e60;
      border-color: #2d5a87;
    }}
    .danger {{ color: var(--warning); }}
    .ok {{ color: var(--ok); }}
    .banner {{
      border: 1px solid #6e3c3c;
      background: #2a1f24;
      border-radius: 10px;
      padding: 10px 12px;
    }}
    .inbox-list {{
      display: grid;
      gap: 10px;
    }}
    .inbox-item {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: rgba(255, 255, 255, 0.04);
      padding: 12px;
    }}
    .item-main a {{
      color: var(--text);
      text-decoration: none;
      font-weight: 650;
      line-height: 1.35;
    }}
    .item-main a:hover {{
      text-decoration: underline;
    }}
    .item-meta {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 6px;
    }}
    .item-actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }}
    .icon-button {{
      padding: 4px 8px;
      border-radius: 999px;
      min-width: 30px;
      line-height: 1;
      font-size: 14px;
      background: #1f4469;
      display: grid;
      place-items: center;
    }}
    .icon-button svg {{
      width: 15px;
      height: 15px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .icon-button.starred {{
      color: #f7d774;
      border-color: #d6b85b;
    }}
    .icon-link-button {{
      width: 34px;
      height: 34px;
      display: inline-grid;
      place-items: center;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #1d3e60;
      color: var(--text);
      text-decoration: none;
    }}
    .icon-link-button svg {{
      width: 15px;
      height: 15px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .floating-back {{
      position: fixed;
      top: 12px;
      left: 12px;
      z-index: 1000;
    }}
    .floating-actions {{
      position: fixed;
      top: 12px;
      left: 12px;
      z-index: 1000;
      display: flex;
      gap: 8px;
    }}
    .floating-actions button,
    .floating-actions a {{
      width: 36px;
      height: 36px;
      padding: 0;
      border-radius: 999px;
      font-size: 16px;
      display: grid;
      place-items: center;
      line-height: 1;
    }}
    .floating-actions a {{
      text-decoration: none;
      color: var(--text);
      border: 1px solid var(--border);
      background: #1d3e60;
    }}
    .floating-actions .icon-link-button {{
      width: 34px;
      height: 34px;
    }}
    .floating-actions a svg,
    .floating-actions button svg {{
      width: 15px;
      height: 15px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .profile-badge {{
      position: fixed;
      top: 12px;
      right: 12px;
      z-index: 1000;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 4px 0;
      color: var(--text);
      font-size: 0.92rem;
      opacity: 0.95;
    }}
    .profile-avatar {{
      width: 18px;
      height: 18px;
      display: inline-grid;
      place-items: center;
    }}
    .profile-badge-link {{
      text-decoration: none;
      padding: 2px 4px;
      border-radius: 14px;
      border: 1px solid transparent;
      transition: background-color 120ms ease, border-color 120ms ease;
    }}
    .profile-badge-link:hover,
    .profile-badge-link:focus-visible {{
      background: rgba(255, 255, 255, 0.08);
      border-color: var(--border);
    }}
    .profile-avatar svg {{
      width: 16px;
      height: 16px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .profile-menu {{
      position: fixed;
      top: 12px;
      right: 12px;
      z-index: 1100;
    }}
    .profile-menu details {{
      position: relative;
    }}
    .profile-menu summary {{
      list-style: none;
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      color: var(--text);
      padding: 2px 4px;
      border-radius: 14px;
      border: 1px solid transparent;
      transition: background-color 120ms ease, border-color 120ms ease;
      user-select: none;
    }}
    .profile-menu summary::-webkit-details-marker {{
      display: none;
    }}
    .profile-menu summary:hover,
    .profile-menu details[open] summary,
    .profile-menu summary:focus-visible {{
      background: rgba(255, 255, 255, 0.08);
      border-color: var(--border);
    }}
    .profile-menu-panel {{
      position: absolute;
      top: 38px;
      right: 0;
      width: min(310px, 88vw);
      border: 1px solid var(--border);
      border-radius: 12px;
      background: linear-gradient(180deg, var(--panel-2), var(--panel));
      padding: 10px;
      display: grid;
      gap: 10px;
      box-shadow: 0 16px 32px rgba(0, 0, 0, 0.28);
    }}
    .profile-menu-list {{
      display: grid;
      gap: 4px;
      max-height: 220px;
      overflow: auto;
    }}
    .profile-menu-item {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 4px 2px;
    }}
    .profile-menu-item form {{
      margin: 0;
    }}
    .profile-menu-create {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
    }}
    .profile-menu-create input {{
      min-width: 0;
      width: 100%;
    }}
    .list-switcher-bar {{
      display: flex;
      gap: 6px;
      align-items: center;
      margin-bottom: 14px;
    }}
    .list-switcher-bar a {{
      display: inline-block;
      text-decoration: none;
      color: var(--text);
      padding: 4px 10px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.02);
    }}
    .list-switcher-bar a.active {{
      background: #224a70;
      border: 1px solid var(--primary);
    }}
    .pagination-footer {{
      position: fixed;
      left: 50%;
      bottom: 10px;
      transform: translateX(-50%);
      width: min(1100px, 95vw);
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      align-items: center;
      gap: 10px;
      margin-top: 2px;
      background: rgba(15, 31, 54, 0.9);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 8px 12px;
      z-index: 900;
    }}
    .pagination-footer .page-info {{
      margin: 0;
      text-align: center;
    }}
    .pagination-footer .next {{
      text-align: right;
    }}
    .watch-below-actions {{
      display: flex;
      justify-content: flex-end;
      margin-top: -6px;
    }}
    .watch-below-actions .icon-button svg {{
      width: 17px;
      height: 17px;
    }}
    iframe {{
      width: 100%;
      aspect-ratio: 16 / 9;
      border: 0;
      border-radius: 10px;
    }}
  </style>
</head>
<body>
  <header class="app-header">
    <span class="app-header-inner">
      <img class="app-header-logo" src="/resources/MailTube-logo_v0.png" alt="" aria-hidden="true">
      <span class="app-header-text"><span class="app-header-mail">Mail</span><span class="app-header-tube">Tube</span></span>
    </span>
  </header>
  <main>
    {body}
  </main>
</body>
</html>"""


def _profile_badge(name: str | None, *, switch_href: str | None = None) -> str:
    label = _safe_display_text(name) if name else "No profile"
    if switch_href:
        return (
            f'<a class="profile-badge profile-badge-link" href="{html.escape(switch_href, quote=True)}" '
            f'title="Switch profile" aria-label="Switch profile"><span class="profile-avatar">{AVATAR_ICON_SVG}</span><span>{label}</span></a>'
        )
    return f'<div class="profile-badge"><span class="profile-avatar">{AVATAR_ICON_SVG}</span><span>{label}</span></div>'


def _profile_menu(
    current_name: str | None,
    profiles: list[sqlite3.Row],
    *,
    current_profile_id: int,
    return_to: str,
) -> str:
    label = _safe_display_text(current_name) if current_name else "No profile"
    safe_return_to = html.escape(return_to, quote=True)
    items: list[str] = []
    for row in profiles:
        profile_id = int(row["id"])
        profile_name = _safe_display_text(row["name"])
        if profile_id == current_profile_id:
            action = '<span class="muted">Active</span>'
        else:
            action = f"""
            <form method="post" action="/profiles/set-active">
              <input type="hidden" name="profile_id" value="{profile_id}">
              <input type="hidden" name="return_to" value="{safe_return_to}">
              <button type="submit">Switch</button>
            </form>
            """
        items.append(
            f"""
            <div class="profile-menu-item">
              <span>{profile_name}</span>
              {action}
            </div>
            """
        )
    return f"""
    <div class="profile-menu">
      <details>
        <summary title="Switch profile" aria-label="Switch profile">
          <span class="profile-avatar">{AVATAR_ICON_SVG}</span>
          <span>{label}</span>
        </summary>
        <div class="profile-menu-panel">
          <div class="profile-menu-list">
            {''.join(items)}
          </div>
          <form method="post" action="/profiles/create" class="profile-menu-create">
            <input type="hidden" name="return_to" value="{safe_return_to}">
            <input type="text" name="name" placeholder="New profile" required>
            <button type="submit">Add</button>
          </form>
        </div>
      </details>
    </div>
    """


class MailTubeHandler(BaseHTTPRequestHandler):
    db: Database
    page_size: int

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._redirect("/inbox")
            return
        if path == "/inbox":
            self._render_inbox(query)
            return
        if path == "/filters":
            self._render_filters(query)
            return
        if path.startswith("/resources/"):
            self._serve_resource(path)
            return
        if path == "/profiles":
            self._render_profiles(query)
            return
        if path.startswith("/watch/"):
            self._render_watch(parsed)
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        form = self._parse_form_data()

        if path == "/inbox/refresh":
            self._post_refresh(form)
            return
        if path.startswith("/inbox/item/") and path.endswith("/watch"):
            self._post_mark_watch(path, form, watched=True)
            return
        if path.startswith("/inbox/item/") and path.endswith("/unwatch"):
            self._post_mark_watch(path, form, watched=False)
            return
        if path.startswith("/inbox/item/") and path.endswith("/trash"):
            self._post_mark_trash(path, form)
            return
        if path.startswith("/inbox/item/") and path.endswith("/star"):
            self._post_mark_star(path, form, starred=True)
            return
        if path.startswith("/inbox/item/") and path.endswith("/unstar"):
            self._post_mark_star(path, form, starred=False)
            return
        if path == "/filters/add":
            self._post_add_filter(form)
            return
        if path == "/filters/delete":
            self._post_delete_filter(form)
            return
        if path == "/profiles/create":
            self._post_create_profile(form)
            return
        if path == "/profiles/set-active":
            self._post_set_active(form)
            return

        self.send_error(404, "Not Found")

    def _parse_form_data(self) -> dict[str, str]:
        length = _to_int(self.headers.get("Content-Length"), default=0)
        body = self.rfile.read(max(0, length)).decode("utf-8")
        parsed = parse_qs(body, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    def _send_html(self, body: str, *, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_bytes(self, payload: bytes, *, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_resource(self, path: str) -> None:
        filename = path.removeprefix("/resources/")
        if "/" in filename or filename not in ALLOWED_RESOURCE_FILES:
            self.send_error(404, "Not Found")
            return
        resource_path = os.path.join(RESOURCES_DIR, filename)
        try:
            with open(resource_path, "rb") as handle:
                payload = handle.read()
        except OSError:
            self.send_error(404, "Not Found")
            return
        content_type = mimetypes.guess_type(resource_path)[0] or "application/octet-stream"
        self._send_bytes(payload, content_type=content_type)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _selected_profile(self, requested: int | None) -> sqlite3.Row | None:
        if requested is not None:
            profile = self.db.get_profile_by_id(requested)
            if profile:
                return profile
        return self.db.get_active_profile()

    def _render_no_profiles(self, *, section: str) -> None:
        target = "/inbox" if section == "inbox" else "/filters"
        body = _base_layout(
            "mail-tube",
            f"""
            {_profile_badge(None)}
            <section class="card">
              <h1>mail-tube</h1>
              <p class="muted">No profiles yet. Create one below to start syncing an inbox.</p>
              <form method="post" action="/profiles/create" class="row">
                <input type="hidden" name="return_to" value="{target}">
                <input type="text" name="name" placeholder="Profile name" required>
                <button type="submit">Create profile</button>
              </form>
            </section>
            """,
        )
        self._send_html(body)

    def _render_inbox(self, query: dict[str, list[str]]) -> None:
        profiles = self.db.list_profiles()
        if not profiles:
            self._render_no_profiles(section="inbox")
            return

        requested_profile = _to_int((query.get("profile") or [None])[0], default=-1)
        profile = self._selected_profile(None if requested_profile < 0 else requested_profile)
        if not profile:
            self._render_no_profiles(section="inbox")
            return

        profile_id = int(profile["id"])
        active_list = (query.get("list") or ["inbox"])[0]
        if active_list not in {"inbox", "watched", "trash", "starred"}:
            active_list = "inbox"

        starred_only = False
        if active_list == "watched":
            statuses = ("watched",)
        elif active_list == "trash":
            statuses = ("dismissed",)
        elif active_list == "starred":
            statuses = ("new", "watched")
            starred_only = True
        else:
            statuses = ("new",)

        page = max(1, _to_int((query.get("page") or [None])[0], default=1))
        offset = (page - 1) * self.page_size
        total = self.db.count_inbox_items(profile_id, statuses=statuses, starred_only=starred_only)
        items = self.db.list_inbox_items(
            profile_id,
            limit=self.page_size,
            offset=offset,
            statuses=statuses,
            starred_only=starred_only,
        )
        latest_run = self.db.latest_refresh_run(profile_id)
        return_to = f"/inbox?{urlencode({'profile': profile_id, 'list': active_list, 'page': page})}"
        profile_switch_return = f"/inbox?{urlencode({'list': active_list, 'page': page})}"

        rows = []
        for item in items:
            inbox_item_id = int(item["inbox_item_id"])
            title = _safe_display_text(item["title"])
            channel = _safe_display_text(item["channel_title"] or "Unknown channel")
            published = _relative_published_label(item["published_at"])
            status = str(item["status"])
            is_starred = bool(item["is_starred"])
            open_link = f"/watch/{inbox_item_id}?{urlencode({'profile': profile_id, 'list': active_list, 'page': page})}"
            actions: list[str] = []
            if status == "new":
                actions.append(
                    f"""
                    <form method="post" action="/inbox/item/{inbox_item_id}/watch">
                      <input type="hidden" name="return_to" value="{return_to}">
                      <button class="icon-button" type="submit" title="Move to watched" aria-label="Move to watched">{EYE_ICON_SVG}</button>
                    </form>
                    """
                )
            elif status == "watched":
                actions.append(
                    f"""
                    <form method="post" action="/inbox/item/{inbox_item_id}/unwatch">
                      <input type="hidden" name="return_to" value="{return_to}">
                      <button class="icon-button" type="submit" title="Move to inbox" aria-label="Move to inbox">↺</button>
                    </form>
                    """
                )
            else:
                actions.append(
                    f"""
                    <form method="post" action="/inbox/item/{inbox_item_id}/unwatch">
                      <input type="hidden" name="return_to" value="{return_to}">
                      <button class="icon-button" type="submit" title="Restore to inbox" aria-label="Restore to inbox">↺</button>
                    </form>
                    """
                )

            if status in {"new", "watched"}:
                star_action = "unstar" if is_starred else "star"
                star_title = "Remove star" if is_starred else "Save item"
                star_icon = STAR_FILLED_ICON_SVG if is_starred else STAR_ICON_SVG
                starred_class = " starred" if is_starred else ""
                actions.append(
                    f"""
                    <form method="post" action="/inbox/item/{inbox_item_id}/{star_action}">
                      <input type="hidden" name="return_to" value="{return_to}">
                      <button class="icon-button{starred_class}" type="submit" title="{star_title}" aria-label="{star_title}">{star_icon}</button>
                    </form>
                    """
                )

            if status != "dismissed":
                actions.append(
                    f"""
                    <form method="post" action="/inbox/item/{inbox_item_id}/trash">
                      <input type="hidden" name="return_to" value="{return_to}">
                      <button class="icon-button" type="submit" title="Move to trash" aria-label="Move to trash">{TRASH_ICON_SVG}</button>
                    </form>
                    """
                )
            rows.append(
                f"""
                <article class="inbox-item">
                  <div class="item-main">
                    <a href="{open_link}">{title}</a>
                    <div class="item-meta">{channel} · {html.escape(published)}</div>
                  </div>
                  <div class="item-actions">
                    {''.join(actions)}
                  </div>
                </article>
                """
            )

        if not rows:
            empty_labels = {
                "inbox": "No inbox items yet. Add filters and run refresh.",
                "watched": "No watched items yet.",
                "trash": "Trash is empty.",
                "starred": "No starred items yet.",
            }
            empty_text = html.escape(empty_labels.get(active_list, "No items."))
            rows.append(
                f"""
                <div class="muted">{empty_text}</div>
                """
            )

        prev_link = ""
        if page > 1:
            prev_link = (
                f'<a href="/inbox?{urlencode({"profile": profile_id, "list": active_list, "page": page - 1})}">'
                "Previous</a>"
            )
        next_link = ""
        if offset + len(items) < total:
            next_link = (
                f'<a href="/inbox?{urlencode({"profile": profile_id, "list": active_list, "page": page + 1})}">'
                "Next</a>"
            )

        banner = ""
        if latest_run and latest_run["status"] != "ok":
            error_text = html.escape(latest_run["error_message"] or "Refresh failed.")
            banner = f'<div class="banner"><strong>Refresh issue:</strong> {error_text}</div>'

        body = _base_layout(
            "mail-tube inbox",
            f"""
            {_profile_menu(profile["name"], profiles, current_profile_id=profile_id, return_to=profile_switch_return)}
            <div class="floating-actions">
              <form method="post" action="/inbox/refresh">
                <input type="hidden" name="profile_id" value="{profile_id}">
                <input type="hidden" name="return_to" value="{return_to}">
                <button type="submit" title="Refresh now" aria-label="Refresh now">↻</button>
              </form>
              <a href="/filters?{urlencode({'profile': profile_id})}" title="Edit filters" aria-label="Edit filters">{FILTER_ICON_SVG}</a>
            </div>
            <section>
              <nav class="list-switcher-bar">
                <a class="{'active' if active_list == 'inbox' else ''}" href="/inbox?{urlencode({'profile': profile_id, 'list': 'inbox'})}">Inbox</a>
                <a class="{'active' if active_list == 'watched' else ''}" href="/inbox?{urlencode({'profile': profile_id, 'list': 'watched'})}">Watched</a>
                <a class="{'active' if active_list == 'starred' else ''}" href="/inbox?{urlencode({'profile': profile_id, 'list': 'starred'})}">Starred</a>
                <a class="{'active' if active_list == 'trash' else ''}" href="/inbox?{urlencode({'profile': profile_id, 'list': 'trash'})}">Trash</a>
              </nav>
              {banner}
              <div class="inbox-list">
                {''.join(rows)}
              </div>
              <div class="pagination-footer">
                <div>{prev_link}</div>
                <p class="muted page-info">Page {page}</p>
                <div class="next">{next_link}</div>
              </div>
            </section>
            """,
        )
        self._send_html(body)

    def _render_filters(self, query: dict[str, list[str]]) -> None:
        profiles = self.db.list_profiles()
        if not profiles:
            self._render_no_profiles(section="filters")
            return

        requested_profile = _to_int((query.get("profile") or [None])[0], default=-1)
        profile = self._selected_profile(None if requested_profile < 0 else requested_profile)
        if not profile:
            self._render_no_profiles(section="filters")
            return

        profile_id = int(profile["id"])
        profile_switch_link = f"/profiles?{urlencode({'return_to': '/filters'})}"
        filters = self.db.list_filters(profile_id)
        rows = []
        for row in filters:
            filter_id = int(row["id"])
            channel_input = html.escape(row["channel_input"])
            channel_title = html.escape(row["channel_title"] or "")
            keyword = html.escape(row["keyword"] or "")
            duration = html.escape(row["duration_bucket"] or "any")
            since_mode = row["since_mode"] or "anytime"
            since_label = "from now" if since_mode == "from_now" else "anytime"
            validity = "ok" if row["is_valid"] else "invalid"
            rows.append(
                f"""
                <tr>
                  <td>{filter_id}</td>
                  <td>{channel_input}</td>
                  <td>{channel_title}</td>
                  <td>{keyword}</td>
                  <td>{duration}</td>
                  <td>{html.escape(since_label)}</td>
                  <td>{validity}</td>
                  <td>
                    <form method="post" action="/filters/delete">
                      <input type="hidden" name="profile_id" value="{profile_id}">
                      <input type="hidden" name="filter_id" value="{filter_id}">
                      <button type="submit">Delete</button>
                    </form>
                  </td>
                </tr>
                """
            )

        if not rows:
            rows.append('<tr><td colspan="8" class="muted">No filters yet.</td></tr>')

        body = _base_layout(
            "mail-tube filters",
            f"""
            {_profile_badge(profile["name"], switch_href=profile_switch_link)}
            <a class="icon-link-button floating-back" href="/inbox?{urlencode({'profile': profile_id})}" title="Back to inbox" aria-label="Back to inbox">{BACK_ICON_SVG}</a>
            <section>
              <h2>Add filter</h2>
              <form method="post" action="/filters/add" class="row">
                <input type="hidden" name="profile_id" value="{profile_id}">
                <input type="text" name="channel_input" placeholder="Channel URL or @handle" required>
                <input type="text" name="keyword" placeholder="Optional keyword">
                <select class="minimal-select" name="duration_bucket">
                  <option value="">Any length</option>
                  <option value="short">Short (&lt;5m)</option>
                  <option value="medium">Medium (5-20m)</option>
                  <option value="long">Long (&gt;20m)</option>
                </select>
                <select class="minimal-select" name="since_mode">
                  <option value="anytime">Anytime</option>
                  <option value="from_now">From now</option>
                </select>
                <button type="submit">Add</button>
              </form>
            </section>
            <section>
              <h2>Current filters</h2>
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Channel Input</th>
                    <th>Resolved Channel</th>
                    <th>Keyword</th>
                    <th>Length</th>
                    <th>Time</th>
                    <th>State</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(rows)}
                </tbody>
              </table>
            </section>
            """,
        )
        self._send_html(body)

    def _render_profiles(self, query: dict[str, list[str]]) -> None:
        profiles = self.db.list_profiles()
        active_profile = self.db.get_active_profile()
        active_name = active_profile["name"] if active_profile else None
        return_to = _safe_return_to((query.get("return_to") or [None])[0], default="/inbox")

        rows = []
        for row in profiles:
            profile_id = int(row["id"])
            profile_name = _safe_display_text(row["name"])
            if int(row["is_active"]) == 1:
                action_html = '<span class="muted">Active</span>'
            else:
                action_html = f"""
                    <form method="post" action="/profiles/set-active">
                      <input type="hidden" name="profile_id" value="{profile_id}">
                      <input type="hidden" name="return_to" value="{html.escape(return_to, quote=True)}">
                      <button type="submit">Switch</button>
                    </form>
                """
            rows.append(
                f"""
                <tr>
                  <td>{profile_name}</td>
                  <td>{action_html}</td>
                </tr>
                """
            )
        if not rows:
            rows.append('<tr><td colspan="2" class="muted">No profiles yet.</td></tr>')

        body = _base_layout(
            "mail-tube profiles",
            f"""
            {_profile_badge(active_name)}
            <a class="icon-link-button floating-back" href="{html.escape(return_to, quote=True)}" title="Back" aria-label="Back">{BACK_ICON_SVG}</a>
            <section>
              <table>
                <thead>
                  <tr>
                    <th>Profile</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(rows)}
                </tbody>
              </table>
            </section>
            <section>
              <h2>Create profile</h2>
              <form method="post" action="/profiles/create" class="row">
                <input type="hidden" name="return_to" value="{html.escape(return_to, quote=True)}">
                <input type="text" name="name" placeholder="Profile name" required>
                <button type="submit">Create</button>
              </form>
            </section>
            """,
        )
        self._send_html(body)

    def _render_watch(self, parsed: ParseResult) -> None:
        path = parsed.path
        query = parse_qs(parsed.query)
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2:
            self.send_error(404, "Not Found")
            return
        inbox_item_id = _to_int(parts[1], default=-1)
        if inbox_item_id <= 0:
            self.send_error(404, "Not Found")
            return

        item = self.db.get_inbox_item_with_video(inbox_item_id)
        if not item:
            self.send_error(404, "Not Found")
            return

        self.db.mark_inbox_opened(inbox_item_id)
        profile_id = int(item["profile_id"])
        profile = self.db.get_profile_by_id(profile_id)
        profile_name = profile["name"] if profile else None
        active_list = (query.get("list") or ["inbox"])[0]
        if active_list not in {"inbox", "watched", "trash", "starred"}:
            active_list = "inbox"
        page = max(1, _to_int((query.get("page") or [None])[0], default=1))
        back_link = f"/inbox?{urlencode({'profile': profile_id, 'list': active_list, 'page': page})}"
        profile_switch_return = f"/inbox?{urlencode({'list': active_list, 'page': page})}"
        profile_switch_link = f"/profiles?{urlencode({'return_to': profile_switch_return})}"
        return_to = parsed.path
        if parsed.query:
            return_to = f"{return_to}?{parsed.query}"
        status = str(item["status"])
        watch_actions = [
            f'<a class="icon-link-button" href="{back_link}" title="Back to inbox" aria-label="Back to inbox">{BACK_ICON_SVG}</a>'
        ]
        star_action_html = ""
        if status in {"new", "watched"}:
            is_starred = bool(item["is_starred"])
            star_action = "unstar" if is_starred else "star"
            star_title = "Remove star" if is_starred else "Save item"
            star_icon = STAR_FILLED_ICON_SVG if is_starred else STAR_ICON_SVG
            star_class = " starred" if is_starred else ""
            star_action_html = (
                f"""
                <form method="post" action="/inbox/item/{inbox_item_id}/{star_action}">
                  <input type="hidden" name="return_to" value="{return_to}">
                  <button class="icon-button{star_class}" type="submit" title="{star_title}" aria-label="{star_title}">{star_icon}</button>
                </form>
                """
            )
        star_section = ""
        if star_action_html:
            star_section = f"""
            <section>
              <div class="watch-below-actions">
                {star_action_html}
              </div>
            </section>
            """
        embed_url = html.escape(build_embed_url(item["youtube_video_id"], autoplay=True), quote=True)

        body = _base_layout(
            "mail-tube watch",
            f"""
            {_profile_badge(profile_name, switch_href=profile_switch_link)}
            <div class="floating-actions">
              {''.join(watch_actions)}
            </div>
            <section>
              <iframe
                src="{embed_url}"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                referrerpolicy="strict-origin-when-cross-origin"
                allowfullscreen
                title="YouTube video player"></iframe>
            </section>
            {star_section}
            """,
        )
        self._send_html(body)

    def _post_refresh(self, form: dict[str, str]) -> None:
        profile_id = _to_int(form.get("profile_id"), default=-1)
        return_to = form.get("return_to")
        if profile_id > 0:
            refresh_profile(self.db, profile_id, api_key=os.getenv("YOUTUBE_API_KEY"))
            self._redirect(return_to or f"/inbox?{urlencode({'profile': profile_id})}")
            return
        self._redirect("/inbox")

    def _post_mark_watch(self, path: str, form: dict[str, str], *, watched: bool) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4:
            self.send_error(404, "Not Found")
            return
        inbox_item_id = _to_int(parts[2], default=-1)
        if inbox_item_id <= 0:
            self.send_error(404, "Not Found")
            return
        self.db.mark_inbox_watched(inbox_item_id, watched=watched)
        self._redirect(form.get("return_to") or "/inbox")

    def _post_mark_trash(self, path: str, form: dict[str, str]) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4:
            self.send_error(404, "Not Found")
            return
        inbox_item_id = _to_int(parts[2], default=-1)
        if inbox_item_id <= 0:
            self.send_error(404, "Not Found")
            return
        self.db.mark_inbox_trashed(inbox_item_id)
        self._redirect(form.get("return_to") or "/inbox")

    def _post_mark_star(self, path: str, form: dict[str, str], *, starred: bool) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4:
            self.send_error(404, "Not Found")
            return
        inbox_item_id = _to_int(parts[2], default=-1)
        if inbox_item_id <= 0:
            self.send_error(404, "Not Found")
            return
        self.db.mark_inbox_starred(inbox_item_id, starred=starred)
        self._redirect(form.get("return_to") or "/inbox")

    def _post_add_filter(self, form: dict[str, str]) -> None:
        profile_id = _to_int(form.get("profile_id"), default=-1)
        channel_input = (form.get("channel_input") or "").strip()
        keyword = (form.get("keyword") or "").strip() or None
        duration_bucket = (form.get("duration_bucket") or "").strip().lower() or None
        since_mode = (form.get("since_mode") or "anytime").strip().lower()
        if profile_id <= 0:
            self._redirect("/filters")
            return
        if channel_input:
            try:
                self.db.add_filter(
                    profile_id,
                    channel_input,
                    keyword,
                    duration_bucket=duration_bucket,
                    since_mode=since_mode,
                )
            except ValueError:
                pass
        self._redirect(f"/filters?{urlencode({'profile': profile_id})}")

    def _post_delete_filter(self, form: dict[str, str]) -> None:
        profile_id = _to_int(form.get("profile_id"), default=-1)
        filter_id = _to_int(form.get("filter_id"), default=-1)
        if profile_id > 0 and filter_id > 0:
            self.db.remove_filter(profile_id, filter_id)
        self._redirect(f"/filters?{urlencode({'profile': profile_id})}")

    def _post_create_profile(self, form: dict[str, str]) -> None:
        name = (form.get("name") or "").strip()
        return_to = _safe_return_to(form.get("return_to"), default="/inbox")
        if not name:
            self._redirect(return_to)
            return
        try:
            profile_id = self.db.create_profile(name)
        except (ValueError, sqlite3.IntegrityError):
            self._redirect(return_to)
            return
        self.db.set_active_profile(profile_id)
        self._redirect(return_to)

    def _post_set_active(self, form: dict[str, str]) -> None:
        profile_id = _to_int(form.get("profile_id"), default=-1)
        return_to = _safe_return_to(form.get("return_to"), default="/inbox")
        if profile_id > 0:
            try:
                self.db.set_active_profile(profile_id)
            except ValueError:
                pass
        self._redirect(return_to)


def run_server(
    db: Database,
    *,
    host: str,
    port: int,
    startup_refresh: bool,
    page_size: int = 20,
) -> None:
    if startup_refresh:
        refresh_all_profiles(db, api_key=os.getenv("YOUTUBE_API_KEY"))

    class BoundHandler(MailTubeHandler):
        pass

    BoundHandler.db = db
    BoundHandler.page_size = page_size

    server = ThreadingHTTPServer((host, port), BoundHandler)
    print(f"Serving mail-tube at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
