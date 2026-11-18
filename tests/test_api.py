import io
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
           json={"reviewer": "someone-else", "decision": "approve"})
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
