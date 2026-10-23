"""
UniBot — Admin: exam timetable upload.

  GET  /admin/exams          → upload page (shows the live exam timetable)
  POST /admin/exams/upload   → action=check:   read the file, list problems, change nothing
                               action=publish: replace the exam timetable and
                                               (optionally) tell students what changed

Same admin key as the teaching timetable (ADMIN_API_KEY).
Files with errors (unreadable dates, a DAY that doesn't match its DATE)
cannot be published — fix the Excel file first.
"""

import os
import html
import logging
import tempfile
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, File, Form, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse

from admin import _page, _key_ok, MAX_UPLOAD_BYTES
from exam_timetable import (parse_exam_excel, exam_clashes, save_exams, get_exam_info,
                            exams_for_year, describe)

logger = logging.getLogger("unibot.admin_exams")
router = APIRouter(prefix="/admin")
EAT = timezone(timedelta(hours=3))


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _current_card() -> str:
    info = get_exam_info()
    if not info:
        return ('<div class="card"><h2>Live exam timetable</h2>'
                '<p class="muted">No exam timetable has been published yet.</p></div>')
    link = (f'<p class="muted">Official copy: <a href="{_esc(info["official_link"])}">'
            f'{_esc(info["official_link"])}</a></p>' if info["official_link"] else "")
    return (f'<div class="card"><h2>Live exam timetable</h2>'
            f'<p><strong>{_esc(info["label"])}</strong> — {_esc(info["exams"])} papers</p>'
            f'<p class="muted">File: {_esc(info["filename"])} · uploaded '
            f'{_esc(str(info["uploaded_at"])[:16])} by {_esc(info["uploaded_by"])}</p>{link}</div>')


def _form() -> str:
    off = "" if os.getenv("ADMIN_API_KEY") else (
        '<div class="card bad"><strong>Uploads are switched off.</strong> '
        'Set the ADMIN_API_KEY variable on the server to turn them on.</div>')
    return off + """
<div class="card">
<h2>Upload an exam timetable</h2>
<p class="muted">Use the department's exam timetable (.xlsx). Each upload replaces the previous
version completely. Invigilator names in the file are ignored — students only see unit, date,
time and venue. Click <strong>Check</strong> first.</p>
<form method="post" action="/admin/exams/upload" enctype="multipart/form-data">
<label for="file">Exam timetable (.xlsx)</label>
<input id="file" name="file" type="file" accept=".xlsx" required>
<label for="label">Version name <span class="muted">(optional — e.g. AUGUST 2026 DRAFT 2; read from the file if empty)</span></label>
<input id="label" name="label" type="text">
<label for="official_link">Link to the official copy <span class="muted">(optional — portal, Drive or notice photo; students are told to confirm there)</span></label>
<input id="official_link" name="official_link" type="text" placeholder="https://…">
<label><input type="checkbox" name="notify" value="on" checked style="width:auto">
 Tell students about upcoming exams that are new or have changed</label>
<label for="uploaded_by">Your name</label>
<input id="uploaded_by" name="uploaded_by" type="text" required>
<label for="admin_key">Admin key</label>
<input id="admin_key" name="admin_key" type="password" required autocomplete="current-password">
<div class="buttons">
<button class="check" name="action" value="check" type="submit">Check file</button>
<button class="publish" name="action" value="publish" type="submit">Publish to students</button>
</div></form></div>
<p><a href="/admin/timetable">Teaching timetable →</a></p>"""


def _exam_table(exams) -> str:
    rows = "".join(
        f'<tr><td>{_esc(e["kind"])}</td><td>Y{_esc(e["year_group"])}</td>'
        f'<td>{_esc(e["unit_code"])} {_esc(e["unit_name"])}</td><td>{_esc(describe(e))}</td></tr>'
        for e in sorted(exams, key=lambda e: (e["kind"], e["exam_date"], e["year_group"])))
    return f'<table><tr><th></th><th>Year</th><th>Unit</th><th>When / where</th></tr>{rows}</table>'


def _upcoming(e) -> bool:
    return e["exam_date"] >= datetime.now(EAT).date().isoformat()


def notify_students(result, label, link):
    """Message each affected year about upcoming exams that are new or changed."""
    from broadcast import execute_broadcast
    sent = []
    footer = "\n\nAsk me \"my exams\" for your full list."
    if link:
        footer += f"\nConfirm on the official timetable: {link}"

    if result["first_upload"]:
        years = sorted({e["year_group"] for e in result["exams"] if e["kind"] == "MAIN"})
        for y in years:
            upcoming = [e for e in exams_for_year(y) if _upcoming(e)]
            if not upcoming:
                continue
            first = upcoming[0]
            body = (f"📝 Exam timetable published ({label}).\n"
                    f"You have {len(upcoming)} paper(s). First:\n"
                    f"{first['unit_code']} {first['unit_name']} — {describe(first)}"
                    + footer)
            sent.append((y, execute_broadcast("exams-office", "Exams (via UniBot)", "SYSTEM",
                                              str(y), body)))
        return sent

    for y, lines in sorted(result["changes"].items()):
        # Only bother students about papers that haven't happened yet
        live = [l for l in lines if "❌" in l or any(
            e["unit_code"] in l and _upcoming(e) for e in exams_for_year(y))]
        if not live:
            continue
        body = f"⚠️ Exam timetable updated ({label})\n\n" + "\n\n".join(live) + footer
        sent.append((y, execute_broadcast("exams-office", "Exams (via UniBot)", "SYSTEM",
                                          str(y), body)))
    return sent


@router.get("/exams", response_class=HTMLResponse)
def exams_page():
    return _page('<h1>Exam timetable</h1><p class="muted">Upload each new version as soon as '
                 'the department sends it.</p>' + _current_card() + _form())


@router.post("/exams/upload")
async def exams_upload(
    request: Request,
    file: UploadFile = File(...),
    admin_key: str = Form(...),
    uploaded_by: str = Form(""),
    label: str = Form(""),
    official_link: str = Form(""),
    notify: str = Form(""),
    action: str = Form("check"),
):
    wants_json = "application/json" in request.headers.get("accept", "")

    def fail(msg, status):
        if wants_json:
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        return _page(f'<div class="card bad"><h1>Not uploaded</h1><p>{_esc(msg)}</p></div>'
                     '<p><a href="/admin/exams">← Back</a></p>', status)

    if not os.getenv("ADMIN_API_KEY"):
        return fail("Uploads are switched off: ADMIN_API_KEY is not set on the server.", 503)
    if not _key_ok(admin_key):
        logger.warning("Exam upload refused: wrong admin key")
        return fail("Wrong admin key.", 403)
    if action not in ("check", "publish"):
        return fail("Unknown action.", 400)
    if not (file.filename or "").lower().endswith(".xlsx"):
        return fail("Please upload an Excel .xlsx file.", 400)
    link = official_link.strip()
    if link and not link.lower().startswith("https://"):
        return fail("The official link must start with https://", 400)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return fail("File is larger than 5 MB — that is not a normal exam timetable.", 400)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            parsed = parse_exam_excel(tmp_path, file.filename or "")
        except Exception as e:
            logger.warning(f"Exam parse failed: {e}")
            return fail("The file could not be opened as an Excel exam timetable.", 400)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if label.strip():
        parsed["label"] = label.strip().upper()
    clashes = exam_clashes(parsed["exams"])
    errors, warnings = parsed["errors"], parsed["warnings"]

    if not parsed["exams"]:
        return fail(errors[0] if errors else "No exams found.", 400)

    published, sent = False, []
    if action == "publish":
        if errors:
            return fail("This file has errors, so nothing was published. Click Check to see "
                        "them, fix the Excel file, then upload again.", 400)
        result = save_exams(parsed, file.filename or "", link, uploaded_by.strip() or "admin")
        result["exams"] = parsed["exams"]
        published = True
        if notify == "on":
            live_link = (get_exam_info() or {}).get("official_link") or ""
            sent = notify_students(result, parsed["label"], live_link)
        logger.info(f"Exam timetable published by {uploaded_by or 'admin'}; notified {len(sent)} year(s)")

    if wants_json:
        return JSONResponse({"ok": True, "action": action, "label": parsed["label"],
                             "exams": len(parsed["exams"]), "errors": errors,
                             "warnings": warnings, "clashes": clashes, "published": published,
                             "notified_years": [y for y, _ in sent]})

    if published:
        note = ("".join(f"<li>Year {y}: {_esc(r.splitlines()[0])}</li>" for y, r in sent)
                or "<li>No messages sent (no upcoming changes, or notifications off).</li>")
        head = (f'<div class="card ok"><h1>Published</h1><p>Students now get exam answers from '
                f'<strong>{_esc(parsed["label"])}</strong> ({len(parsed["exams"])} papers).</p>'
                f'<ul class="muted">{note}</ul></div>')
    else:
        state = "bad" if errors else ("warn" if clashes or warnings else "ok")
        head = (f'<div class="card {state}"><h1>Check complete — nothing changed</h1>'
                f'<p><strong>{_esc(parsed["label"])}</strong>: {len(parsed["exams"])} papers read.</p>'
                + ('<p><strong>Fix the errors below before publishing.</strong></p>' if errors else
                   '<p class="muted">If everything below looks right, upload again and click Publish.</p>')
                + '</div>')

    problems = ""
    for title, items, tag in (("Errors", errors, "bad"),
                              ("Clashes", [c["detail"] for c in clashes], "warn"),
                              ("Warnings", warnings, "warn")):
        if items:
            lis = "".join(f"<li>{_esc(i)}</li>" for i in items[:20])
            problems += f'<div class="card {tag}"><h2>{title} ({len(items)})</h2><ul>{lis}</ul></div>'

    body = (head + problems
            + f'<div class="card"><h2>What students will see</h2>{_exam_table(parsed["exams"])}</div>'
            + '<p><a href="/admin/exams">← Back to the exam upload page</a></p>')
    return _page(body)