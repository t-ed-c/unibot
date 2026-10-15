"""
UniBot — Admin: timetable upload.

Lets the department timetabler replace the timetable each semester
without touching code or the server console:

  GET  /admin/timetable          → upload page (shows the live semester)
  POST /admin/timetable/upload   → action=check:   read the file and list
                                                   any clashes, change nothing
                                   action=publish: make it the live timetable

Protected by the ADMIN_API_KEY environment variable. If that variable
is not set, uploads are switched off.
"""

import os
import hmac
import html
import logging
import tempfile

from fastapi import APIRouter, File, Form, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse

from timetable import parse_timetable_excel, save_timetable, get_current_timetable_info
from conflict import scan_entries

logger = logging.getLogger("unibot.admin")
router = APIRouter(prefix="/admin")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB — a timetable is a few hundred KB


def _key_ok(given: str) -> bool:
    expected = os.getenv("ADMIN_API_KEY", "")
    return bool(expected) and hmac.compare_digest(given or "", expected)


# ─────────────────────────────────────────────────────────────
# Page layout
# ─────────────────────────────────────────────────────────────
STYLE = """
:root{--navy:#0D2137;--green:#0F6B3D;--bg:#F7F9FA;--card:#fff;--text:#1A1A1A;
--muted:#6B7C6E;--red:#B71C1C;--redbg:#FCEBEB;--amber:#E65100;--amberbg:#FAEEDA;--line:#DDE3E0}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--text);line-height:1.5}
header{background:var(--navy);color:#fff;padding:14px 20px;font-weight:600}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
.muted{color:var(--muted);font-size:14px}
label{display:block;font-size:14px;font-weight:600;margin:14px 0 6px}
input[type=text],input[type=password],input[type=file]{width:100%;padding:10px;border:1px solid var(--line);
border-radius:8px;font-size:15px;background:#fff}
.buttons{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
button{padding:11px 18px;border-radius:8px;font-size:15px;cursor:pointer;border:1px solid var(--green)}
.check{background:#fff;color:var(--green)}.publish{background:var(--green);color:#fff}
.ok{background:#EBF5EB;border-color:var(--green)}.bad{background:var(--redbg);border-color:var(--red)}
.warn{background:var(--amberbg);border-color:var(--amber)}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.tag{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:6px;
background:var(--redbg);color:var(--red)}
a{color:var(--green)}
"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>UniBot · Timetable</title><style>{STYLE}</style></head>
<body><header>UniBot · Department of Computer Science</header><main>{body}</main></body></html>""")


def _current_card() -> str:
    info = get_current_timetable_info()
    if not info:
        return ('<div class="card"><h2>Live timetable</h2>'
                '<p class="muted">No timetable has been uploaded through this page yet.</p></div>')
    e = {k: html.escape(str(v if v is not None else "")) for k, v in info.items()}
    return (f'<div class="card"><h2>Live timetable</h2>'
            f'<p><strong>{e["semester"]}</strong> — {e["sessions"]} classes, {e["units"]} units</p>'
            f'<p class="muted">File: {e["filename"]} · uploaded {e["uploaded_at"][:16]} '
            f'by {e["uploaded_by"]} · {e["conflicts"]} clash(es) at upload</p></div>')


def _form() -> str:
    disabled = "" if os.getenv("ADMIN_API_KEY") else (
        '<div class="card bad"><strong>Uploads are switched off.</strong> '
        'Set the ADMIN_API_KEY variable on the server to turn them on.</div>')
    return disabled + """
<div class="card">
<h2>Upload a new timetable</h2>
<p class="muted">Use the department's Excel timetable (.xlsx) in the usual layout.
Click <strong>Check</strong> first: it reads the file and lists any clashes without changing anything.
<strong>Publish</strong> replaces the timetable students see.</p>
<form method="post" action="/admin/timetable/upload" enctype="multipart/form-data">
<label for="file">Timetable file (.xlsx)</label>
<input id="file" name="file" type="file" accept=".xlsx" required>
<label for="semester">Semester name <span class="muted">(optional — read from the file if empty)</span></label>
<input id="semester" name="semester" type="text" placeholder="e.g. SEPTEMBER-DECEMBER 2026">
<label for="uploaded_by">Your name</label>
<input id="uploaded_by" name="uploaded_by" type="text" required>
<label for="admin_key">Admin key</label>
<input id="admin_key" name="admin_key" type="password" required autocomplete="current-password">
<div class="buttons">
<button class="check" name="action" value="check" type="submit">Check file</button>
<button class="publish" name="action" value="publish" type="submit">Publish to students</button>
</div></form></div>"""


def _result_card(action: str, parsed: dict, conflicts: list, semester: str, saved=None) -> str:
    rows = "".join(
        f'<tr><td><span class="tag">{html.escape(c["type"])}</span></td>'
        f'<td>{html.escape(c["detail"])}</td></tr>' for c in conflicts)
    clash_html = (f'<h2>{len(conflicts)} clash(es) found</h2><table>{rows}</table>'
                  if conflicts else '<h2>No clashes found</h2>')
    missing = [c for c, u in parsed["units"].items() if not u["unit_name"]]
    unread = "".join(f"<li>{html.escape(e)}</li>" for e in parsed["errors"][:15])

    if action == "publish":
        head = (f'<div class="card ok"><h1>Published</h1><p>Students now get answers from '
                f'<strong>{html.escape(saved["semester"])}</strong>: {saved["loaded"]} classes, '
                f'{saved["units"]} units.</p></div>')
    else:
        head = (f'<div class="card {"warn" if conflicts else "ok"}"><h1>Check complete — nothing changed</h1>'
                f'<p><strong>{html.escape(semester or "Semester name not found")}</strong>: '
                f'{len(parsed["sessions"])} classes and {len(parsed["units"])} units read.</p>'
                f'<p class="muted">Fix any clashes in the Excel file, then upload again and click Publish.</p></div>')

    extra = ""
    if unread:
        extra += (f'<p class="muted">Cells that could not be read (no unit code):</p>'
                  f'<ul class="muted">{unread}</ul>')
    if missing:
        extra += (f'<p class="muted">Units with no name in the file: '
                  f'{html.escape(", ".join(missing))}</p>')

    return (head + f'<div class="card {"warn" if conflicts else ""}">{clash_html}{extra}</div>'
            '<p><a href="/admin/timetable">← Back to the upload page</a></p>')


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────
@router.get("/timetable", response_class=HTMLResponse)
def timetable_page():
    return _page('<h1>Timetable</h1><p class="muted">Upload the timetable once per semester.</p>'
                 + _current_card() + _form())


@router.post("/timetable/upload")
async def timetable_upload(
    request: Request,
    file: UploadFile = File(...),
    admin_key: str = Form(...),
    uploaded_by: str = Form(""),
    semester: str = Form(""),
    action: str = Form("check"),
):
    wants_json = "application/json" in request.headers.get("accept", "")

    def fail(msg: str, status: int):
        if wants_json:
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        return _page(f'<div class="card bad"><h1>Not uploaded</h1><p>{html.escape(msg)}</p></div>'
                     '<p><a href="/admin/timetable">← Back</a></p>', status)

    if not os.getenv("ADMIN_API_KEY"):
        return fail("Uploads are switched off: ADMIN_API_KEY is not set on the server.", 503)
    if not _key_ok(admin_key):
        logger.warning("Timetable upload refused: wrong admin key")
        return fail("Wrong admin key.", 403)
    if action not in ("check", "publish"):
        return fail("Unknown action.", 400)
    if not (file.filename or "").lower().endswith(".xlsx"):
        return fail("Please upload an Excel .xlsx file.", 400)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return fail("File is larger than 5 MB — that is not a normal timetable.", 400)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            parsed = parse_timetable_excel(tmp_path)
        except Exception as e:
            logger.warning(f"Timetable parse failed: {e}")
            return fail("The file could not be opened as an Excel timetable.", 400)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if not parsed["sessions"]:
        detail = parsed["errors"][0] if parsed["errors"] else "No classes found."
        return fail(f"No classes were found in this file, so nothing was changed. {detail}", 400)

    conflicts = scan_entries(parsed["sessions"])
    semester_label = (semester or parsed["semester"]).strip().upper()
    saved = None

    if action == "publish":
        saved = save_timetable(parsed, semester=semester_label, filename=file.filename or "",
                               uploaded_by=uploaded_by.strip() or "admin",
                               conflict_count=len(conflicts))
        logger.info(f"Timetable published by {uploaded_by or 'admin'}: {saved}")

    if wants_json:
        return JSONResponse({
            "ok": True, "action": action, "semester": semester_label,
            "sessions": len(parsed["sessions"]), "units": len(parsed["units"]),
            "conflicts": conflicts, "unreadable_cells": parsed["errors"],
            "published": saved is not None,
        })
    return _page(_result_card(action, parsed, conflicts, semester_label, saved))