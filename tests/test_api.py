import io
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import create_app

STORY = {
    "key": "LIMS-142",
    "title": "Reviewer e-signature on batch results",
    "description": "As a QA reviewer I want to sign off batch test results electronically.",
    "acceptance_criteria": [
        "Signature requires re-entering the password",
        "Signed results cannot be edited",
    ],
    "requirement_id": "URS-017",
    "actor": "zane",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AVB_MOCK_AI", "1")
    monkeypatch.delenv("AVB_USERS", raising=False)  # a local .env must not switch on login
    db_path = str(tmp_path / "test.db")
    c = TestClient(create_app(db_path))
    c.db_path = db_path
    return c


def test_story_is_rated_and_logged(client):
    s = client.post("/api/stories", json=STORY).json()
    res = s["analysis"]["result"]
    assert res["risk"] == "High"
    assert res["test_approach"] == "scripted"
    assert res["part11_relevant"] is True
    assert len(res["test_cases"]) == 2
    actions = [e["action"] for e in client.get("/api/audit").json()]
    assert actions == ["analysis.suggested", "story.created"]


def test_low_risk_story(client):
    s = client.post("/api/stories", json={"key": "UI-9", "title": "Change button colour",
                                          "acceptance_criteria": ["Button is blue"]}).json()
    assert s["analysis"]["result"]["risk"] == "Low"


def test_override_needs_comment_and_is_audited(client):
    s = client.post("/api/stories", json=STORY).json()
    aid = s["analysis"]["id"]
    r = client.post(f"/api/analyses/{aid}/review",
                    json={"reviewer": "qa.lead", "decision": "override", "final_risk": "Medium"})
    assert r.status_code == 422
    r = client.post(f"/api/analyses/{aid}/review",
                    json={"reviewer": "qa.lead", "decision": "override", "final_risk": "Medium",
                          "comment": "Downstream review step catches this"})
    assert r.json()["analysis"]["final_risk"] == "Medium"
    last = client.get("/api/audit").json()[0]
    assert last["action"] == "analysis.override"
    assert last["detail"]["suggested_risk"] == "High"


def test_audit_log_is_append_only(client):
    client.post("/api/stories", json=STORY)
    conn = sqlite3.connect(client.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM audit_log")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE audit_log SET actor = 'someone else'")


def test_rtm_exports(client):
    s = client.post("/api/stories", json=STORY).json()
    client.post(f"/api/analyses/{s['analysis']['id']}/review",
                json={"reviewer": "qa.lead", "decision": "approve"})
    csv_text = client.get("/api/rtm.csv").text
    assert "URS-017" in csv_text and "TC-02" in csv_text
    wb = load_workbook(io.BytesIO(client.get("/api/rtm.xlsx").content))
    ws = wb["Traceability"]
    assert ws.max_row == 3
    assert ws["G2"].value == "High"


def test_login_required_when_users_set(tmp_path, monkeypatch):
    monkeypatch.setenv("AVB_MOCK_AI", "1")
    monkeypatch.setenv("AVB_USERS", "qa.lead:pw1")
    c = TestClient(create_app(str(tmp_path / "auth.db")))
    assert c.get("/api/health").status_code == 200
    home = c.get("/", follow_redirects=False)
    assert home.status_code == 303 and home.headers["location"] == "/login"
    assert "www-authenticate" not in c.get("/api/stories").headers  # no browser popup
    assert c.get("/api/stories", auth=("qa.lead", "wrong")).status_code == 401
    s = c.post("/api/stories", json=STORY, auth=("qa.lead", "pw1")).json()
    c.post(f"/api/analyses/{s['analysis']['id']}/review", auth=("qa.lead", "pw1"),
           json={"reviewer": "someone-else", "decision": "approve", "password": "pw1"})
    actors = [e["actor"] for e in c.get("/api/audit", auth=("qa.lead", "pw1")).json()]
    assert actors[0] == "qa.lead" and actors[-1] == "qa.lead"


def test_sign_in_page_sets_session(tmp_path, monkeypatch):
    monkeypatch.setenv("AVB_MOCK_AI", "1")
    monkeypatch.setenv("AVB_USERS", "qa.lead:pw1")
    c = TestClient(create_app(str(tmp_path / "login.db")))
    assert "Sign in" in c.get("/login").text
    bad = c.post("/login", data={"username": "qa.lead", "password": "nope"}, follow_redirects=False)
    assert bad.headers["location"] == "/login?error=1" and "avb_session" not in c.cookies
    ok = c.post("/login", data={"username": "qa.lead", "password": "pw1"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    assert c.get("/").status_code == 200
    assert c.get("/api/me").json() == {"user": "qa.lead"}
    good = c.cookies["avb_session"]
    c.cookies.clear()
    c.cookies.set("avb_session", good[:-2] + ("00" if not good.endswith("00") else "11"))  # tampered
    assert c.get("/api/me").status_code == 401
    c.cookies.clear()
    c.post("/login", data={"username": "qa.lead", "password": "pw1"})
    assert c.get("/api/me").status_code == 200
    c.get("/logout")
    assert c.get("/api/me").status_code == 401


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL (an unpooled URL) to test Postgres")
def test_postgres_flow_and_append_only(monkeypatch):
    import uuid

    import psycopg

    base = os.environ["TEST_DATABASE_URL"]
    schema = f"avb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    try:
        sep = "&" if "?" in base else "?"
        url = f"{base}{sep}options=-csearch_path%3D{schema}"
        monkeypatch.setenv("AVB_MOCK_AI", "1")
        monkeypatch.delenv("AVB_USERS", raising=False)
        monkeypatch.setenv("DATABASE_URL", url)
        c = TestClient(create_app())
        s = c.post("/api/stories", json=STORY).json()
        assert s["id"] and s["analysis"]["result"]["risk"] == "High"
        r = c.post(f"/api/analyses/{s['analysis']['id']}/review",
                   json={"reviewer": "qa", "decision": "override", "final_risk": "Medium", "comment": "ok"})
        assert r.json()["analysis"]["final_risk"] == "Medium"
        assert [e["action"] for e in c.get("/api/audit").json()][0] == "analysis.override"
        assert c.get("/api/rtm.csv").status_code == 200
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO signatures (analysis_id, signer, meaning, signed_at, record_hash)"
                         " VALUES (%s, 'qa', 'Approved', 'now', 'h')", (s["analysis"]["id"],))
            for sql in ("DELETE FROM audit_log", "UPDATE audit_log SET actor = 'x'", "TRUNCATE audit_log",
                        "DELETE FROM signatures", "UPDATE signatures SET meaning = 'x'", "TRUNCATE signatures"):
                with pytest.raises(psycopg.errors.RaiseException):
                    conn.execute(sql)
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def test_repeated_wrong_passwords_lock_the_account(tmp_path, monkeypatch):
    monkeypatch.setenv("AVB_MOCK_AI", "1")
    monkeypatch.setenv("AVB_USERS", "qa.lead:pw1")
    c = TestClient(create_app(str(tmp_path / "limit.db")))
    for _ in range(5):
        r = c.post("/login", data={"username": "qa.lead", "password": "guess"}, follow_redirects=False)
        assert r.headers["location"] == "/login?error=1"
    # even the right password is refused while locked, on the page and for scripts
    r = c.post("/login", data={"username": "qa.lead", "password": "pw1"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=locked"
    assert c.get("/api/me", auth=("qa.lead", "pw1")).status_code == 429


def test_previews_use_their_own_database(monkeypatch):
    from app import db

    monkeypatch.setenv("DATABASE_URL", "postgresql://prod")
    monkeypatch.setenv("PREVIEW_DATABASE_URL", "postgresql://preview")
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert db.database_url() == "postgresql://prod"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert db.database_url() == "postgresql://preview"
    monkeypatch.delenv("VERCEL_ENV")
    assert db.database_url() == "postgresql://prod"


def _login_client(tmp_path, monkeypatch, name="sig.db"):
    monkeypatch.setenv("AVB_MOCK_AI", "1")
    monkeypatch.setenv("AVB_USERS", "qa.lead:pw1,dev:pw2")
    c = TestClient(create_app(str(tmp_path / name)))
    c.post("/login", data={"username": "qa.lead", "password": "pw1"})
    return c


def test_review_is_an_electronic_signature(tmp_path, monkeypatch):
    c = _login_client(tmp_path, monkeypatch)
    aid = c.post("/api/stories", json=STORY).json()["analysis"]["id"]

    no_pw = c.post(f"/api/analyses/{aid}/review", json={"reviewer": "x", "decision": "approve"})
    assert no_pw.status_code == 403
    wrong = c.post(f"/api/analyses/{aid}/review",
                   json={"reviewer": "x", "decision": "approve", "password": "pw2"})  # dev's password
    assert wrong.status_code == 403
    assert c.get("/api/audit").json()[0]["action"] == "signature.failed"

    ok = c.post(f"/api/analyses/{aid}/review",
                json={"reviewer": "x", "decision": "approve", "password": "pw1", "meaning": "Reviewed"})
    assert ok.status_code == 200
    a = ok.json()["analysis"]
    sig = a["signature"]
    assert a["status"] == "approved" and a["reviewer"] == "qa.lead"
    assert sig["signer"] == "qa.lead" and sig["meaning"] == "Reviewed" and len(sig["record_hash"]) == 64

    # the hash binds the signature to exactly this record
    from app.main import signature_hash
    raw = sqlite3.connect(str(tmp_path / "sig.db"))
    raw.row_factory = sqlite3.Row
    row = dict(raw.execute("SELECT * FROM analyses WHERE id = ?", (aid,)).fetchone())
    assert signature_hash(row, "approve", "High", "Reviewed", "qa.lead", sig["signed_at"]) == sig["record_hash"]
    assert signature_hash(row, "approve", "Low", "Reviewed", "qa.lead", sig["signed_at"]) != sig["record_hash"]

    # signed once, for good
    again = c.post(f"/api/analyses/{aid}/review",
                   json={"reviewer": "x", "decision": "reject", "comment": "no", "password": "pw1"})
    assert again.status_code == 409
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE signatures SET meaning = 'Approved'")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("DELETE FROM signatures")

    rtm = c.get("/api/rtm.csv").text.splitlines()
    assert "Signature meaning" in rtm[0] and ",Reviewed," in rtm[1]


def test_rejection_is_signed_as_rejected(tmp_path, monkeypatch):
    c = _login_client(tmp_path, monkeypatch, "rej.db")
    aid = c.post("/api/stories", json=STORY).json()["analysis"]["id"]
    r = c.post(f"/api/analyses/{aid}/review", json={
        "reviewer": "x", "decision": "reject", "comment": "Tests miss the lockout rule",
        "password": "pw1", "meaning": "Approved"})
    assert r.json()["analysis"]["signature"]["meaning"] == "Rejected"


def test_wrong_signature_passwords_count_toward_lockout(tmp_path, monkeypatch):
    c = _login_client(tmp_path, monkeypatch, "lock.db")
    aid = c.post("/api/stories", json=STORY).json()["analysis"]["id"]
    for _ in range(5):
        c.post(f"/api/analyses/{aid}/review", json={"reviewer": "x", "decision": "approve", "password": "bad"})
    r = c.post(f"/api/analyses/{aid}/review", json={"reviewer": "x", "decision": "approve", "password": "pw1"})
    assert r.status_code == 429


def test_reviews_without_login_are_unsigned(client):
    aid = client.post("/api/stories", json=STORY).json()["analysis"]["id"]
    a = client.post(f"/api/analyses/{aid}/review", json={"reviewer": "zane", "decision": "approve"}).json()["analysis"]
    assert a["status"] == "approved" and a["signature"] is None
    assert client.get("/api/audit").json()[0]["detail"]["signature"] == "unsigned (login is off)"
