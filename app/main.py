"""Agile-to-Validation Bridge prototype API.

Run:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import ai, auth, db

try:  # load .env when running locally; harmless if python-dotenv is missing
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

STATIC = Path(__file__).parent / "static"


class StoryIn(BaseModel):
    key: str = Field(..., examples=["LIMS-142"])
    title: str
    description: str = ""
    acceptance_criteria: list[str] = []
    requirement_id: str | None = Field(None, examples=["URS-017"])
    actor: str = "anonymous"


class ReviewIn(BaseModel):
    reviewer: str
    decision: Literal["approve", "override", "reject"]
    final_risk: Literal["High", "Medium", "Low"] | None = None
    comment: str = ""
    # Electronic signature (required when login is on): the reviewer re-enters
    # their password and states what the signature means.
    password: str = ""
    meaning: Literal["Approved", "Reviewed"] = "Approved"


def signature_hash(analysis: dict, decision: str, final_risk: str | None, meaning: str,
                   signer: str, signed_at: str) -> str:
    """Fingerprint of exactly what was signed, so a signature can't be moved
    to other content (Part 11 §11.70 signature/record linking)."""
    payload = {
        "analysis_id": analysis["id"], "story_id": analysis["story_id"],
        "result": analysis["result"], "decision": decision, "final_risk": final_risk,
        "meaning": meaning, "signer": signer, "signed_at": signed_at,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _story_row(conn, story_id: int) -> dict:
    s = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Story not found")
    a = conn.execute(
        "SELECT * FROM analyses WHERE story_id = ? ORDER BY id DESC LIMIT 1", (story_id,)
    ).fetchone()
    story = dict(s)
    story["acceptance_criteria"] = json.loads(story["acceptance_criteria"])
    if a:
        analysis = dict(a)
        analysis["result"] = json.loads(analysis["result"])
        sig = conn.execute("SELECT signer, meaning, signed_at, record_hash FROM signatures"
                           " WHERE analysis_id = ?", (analysis["id"],)).fetchone()
        analysis["signature"] = dict(sig) if sig else None
        story["analysis"] = analysis
    return story


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Agile-to-Validation Bridge", version="0.1.0")
    conn = db.connect(db_path)
    users = auth.load_users()
    limiter = auth.LoginLimiter(conn)
    if users:
        app.add_middleware(auth.LoginMiddleware, users=users, limiter=limiter)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        if not users:
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC / "login.html")

    @app.post("/login", include_in_schema=False)
    async def login(request: Request):
        # Parse the form by hand so we don't need python-multipart.
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        name = form.get("username", [""])[0].strip()
        pw = form.get("password", [""])[0]
        if not users:
            return RedirectResponse("/", status_code=303)
        ip = auth.client_ip(request)
        if limiter.blocked(name, ip):
            return RedirectResponse("/login?error=locked", status_code=303)
        if not auth.check_password(users, name, pw):
            limiter.failed(name, ip)
            return RedirectResponse("/login?error=1", status_code=303)
        limiter.succeeded(name)
        res = RedirectResponse("/", status_code=303)
        res.set_cookie(auth.COOKIE, auth.make_session(name), max_age=auth.SESSION_SECONDS,
                       httponly=True, samesite="lax", secure=request.url.scheme == "https"
                       or request.headers.get("x-forwarded-proto") == "https")
        return res

    @app.get("/logout", include_in_schema=False)
    def logout():
        res = RedirectResponse("/login" if users else "/", status_code=303)
        res.delete_cookie(auth.COOKIE)
        return res

    @app.get("/api/health")
    def health():
        return {"ok": True, "mock_ai": ai.use_mock(), "login": bool(users)}

    @app.get("/api/me")
    def me(request: Request):
        return {"user": auth.current_user(request)}

    @app.post("/api/stories")
    def create_story(story: StoryIn, request: Request):
        story.actor = auth.current_user(request) or story.actor
        cur = conn.execute(
            "INSERT INTO stories (key, title, description, acceptance_criteria, requirement_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (story.key, story.title, story.description, json.dumps(story.acceptance_criteria),
             story.requirement_id, db.now()),
        )
        story_id = cur.lastrowid
        conn.commit()
        db.log(conn, story.actor, "story.created", story_id, {"key": story.key, "title": story.title})

        try:
            result = ai.analyze_story(story.title, story.description, story.acceptance_criteria)
        except Exception as exc:  # surface model errors without losing the story
            db.log(conn, "system", "analysis.failed", story_id, {"error": str(exc)})
            raise HTTPException(502, f"AI analysis failed: {exc}") from exc

        model = "mock" if ai.use_mock() else os.environ.get("AVB_MODEL", ai.DEFAULT_MODEL)
        conn.execute(
            "INSERT INTO analyses (story_id, result, model, created_at) VALUES (?, ?, ?, ?)",
            (story_id, json.dumps(result), model, db.now()),
        )
        conn.commit()
        db.log(conn, f"ai:{model}", "analysis.suggested", story_id,
               {"risk": result["risk"], "test_cases": len(result["test_cases"])})
        return _story_row(conn, story_id)

    @app.get("/api/stories")
    def list_stories():
        ids = [r["id"] for r in conn.execute("SELECT id FROM stories ORDER BY id DESC")]
        return [_story_row(conn, i) for i in ids]

    @app.get("/api/stories/{story_id}")
    def get_story(story_id: int):
        return _story_row(conn, story_id)

    @app.post("/api/analyses/{analysis_id}/review")
    def review(analysis_id: int, body: ReviewIn, request: Request):
        signer = auth.current_user(request)
        body.reviewer = signer or body.reviewer
        a = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not a:
            raise HTTPException(404, "Analysis not found")
        if a["status"] != "pending":
            raise HTTPException(409, "This analysis has already been reviewed and signed")
        suggested = json.loads(a["result"])["risk"]
        if body.decision == "override" and not body.final_risk:
            raise HTTPException(422, "An override needs final_risk")
        if body.decision != "approve" and not body.comment.strip():
            raise HTTPException(422, "Overrides and rejections need a comment")

        final = {"approve": suggested, "override": body.final_risk, "reject": None}[body.decision]
        status = {"approve": "approved", "override": "approved", "reject": "rejected"}[body.decision]
        meaning = "Rejected" if body.decision == "reject" else body.meaning

        signature = None
        if signer:  # login on: the review is an electronic signature
            ip = auth.client_ip(request)
            if limiter.blocked(signer, ip):
                raise HTTPException(429, "Too many failed signatures. Try again in 15 minutes.")
            if not auth.check_password(users, signer, body.password):
                limiter.failed(signer, ip)
                db.log(conn, signer, "signature.failed", a["story_id"], {"analysis_id": analysis_id})
                raise HTTPException(403, "Signature not applied: the password is wrong")
            signed_at = db.now()
            record_hash = signature_hash(dict(a), body.decision, final, meaning, signer, signed_at)
            try:  # UNIQUE(analysis_id) also stops two signatures racing
                conn.execute(
                    "INSERT INTO signatures (analysis_id, signer, meaning, signed_at, record_hash)"
                    " VALUES (?, ?, ?, ?, ?)", (analysis_id, signer, meaning, signed_at, record_hash))
            except Exception as exc:
                if db.is_unique_violation(exc):
                    raise HTTPException(409, "This analysis has already been signed") from exc
                raise
            signature = {"meaning": meaning, "signed_at": signed_at, "record_hash": record_hash}

        conn.execute(
            "UPDATE analyses SET status = ?, final_risk = ?, reviewer = ? WHERE id = ?",
            (status, final, body.reviewer, analysis_id),
        )
        conn.commit()
        db.log(conn, body.reviewer, f"analysis.{body.decision}", a["story_id"],
               {"analysis_id": analysis_id, "suggested_risk": suggested,
                "final_risk": final, "comment": body.comment,
                "signature": signature or "unsigned (login is off)"})
        return _story_row(conn, a["story_id"])

    @app.get("/api/audit")
    def audit(story_id: int | None = None):
        q, args = "SELECT * FROM audit_log", ()
        if story_id is not None:
            q, args = q + " WHERE story_id = ?", (story_id,)
        rows = conn.execute(q + " ORDER BY id DESC", args).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def rtm_rows() -> list[list[str]]:
        rows = []
        for s in reversed(list_stories()):
            a = s.get("analysis")
            if not a:
                continue
            res = a["result"]
            sig = a.get("signature") or {}
            for tc in res["test_cases"]:
                rows.append([
                    s.get("requirement_id") or "", s["key"], s["title"], tc["covers_criterion"],
                    tc["id"], tc["title"], a.get("final_risk") or f"{res['risk']} (unreviewed)",
                    res["test_approach"], a["status"], a.get("reviewer") or "",
                    sig.get("meaning", ""), sig.get("signed_at", ""), "Not run",
                ])
        return rows

    RTM_HEADER = ["Requirement", "Story", "Story title", "Acceptance criterion", "Test ID",
                  "Test title", "GxP risk", "Test approach", "Review status", "Reviewer",
                  "Signature meaning", "Signed at (UTC)", "Result"]

    @app.get("/api/rtm.csv")
    def rtm_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(RTM_HEADER)
        w.writerows(rtm_rows())
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=traceability-matrix.csv"},
        )

    @app.get("/api/rtm.xlsx")
    def rtm_xlsx():
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "Traceability"
        ws.append(RTM_HEADER)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in rtm_rows():
            ws.append(r)
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(
                50, max(12, *(len(str(c.value or "")) + 2 for c in col)))
        ws.freeze_panes = "A2"
        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        return StreamingResponse(
            out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=traceability-matrix.xlsx"},
        )

    return app


app = create_app()
