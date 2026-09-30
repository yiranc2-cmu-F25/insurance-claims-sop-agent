"""Regression checks for entry points and assets affected by file relocation."""
import json
import runpy

from fastapi.testclient import TestClient

from api import app as entry_app
from claim_agent.agent import graph as entry_graph
from claim_agent.api.app import app
from claim_agent.paths import FIXTURES_DIR, PROJECT_ROOT
from claim_agent.tools import get_claim_for_action, verify_identity
from claim_agent.workflow.graph import graph


def test_legacy_entry_points_share_the_canonical_instances():
    assert entry_app is app
    assert entry_graph is graph


def test_langgraph_config_still_resolves_to_the_same_graph():
    config = json.loads((PROJECT_ROOT / "langgraph.json").read_text())
    relative_path, export_name = config["graphs"]["insurance_claims"].split(":")
    exports = runpy.run_path(str(PROJECT_ROOT / relative_path))
    assert exports[export_name] is graph


def test_page_loads_separate_styles_and_script():
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'href="/static/styles.css"' in page.text
        assert 'src="/static/app.js"' in page.text
        assert '<button id="send-email"' in page.text
        assert '<button id="transfer-to-human"' in page.text
        css = client.get("/static/styles.css")
        script = client.get("/static/app.js")
        assert css.status_code == 200 and "text/css" in css.headers["content-type"]
        assert script.status_code == 200 and "javascript" in script.headers["content-type"]
        assert "#email-choice" in css.text
        assert 'fetch("/api/email-choice"' in script.text
        assert 'handoffAction("/api/handoff")' in script.text


def test_resource_paths_do_not_depend_on_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert FIXTURES_DIR.is_dir()
    party_id, matches = verify_identity({"name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"})
    assert party_id == "P9" and len(matches) == 3
    claim = get_claim_for_action(party_id, "CL-2048", "read_claim_status")
    assert claim["case_id"] == "CL-2048"
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200


def test_http_paths_are_unchanged():
    paths = app.openapi()["paths"]
    assert set(paths) == {
        "/", "/api/health", "/api/llm-status", "/api/session", "/api/conversation",
        "/api/chat", "/api/handoff", "/api/email-choice", "/api/email-status", "/api/handoff/resume",
    }
    assert "delete" in paths["/api/conversation"]
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.post("/api/session").status_code == 200
        data = client.get("/api/conversation").json()
        assert data["phase"] == "VERIFY_ID"
        assert not data["verified"] and not data["email_offer"]["pending"]
        assert data["handoff"]["status"] == "none"


def test_frontend_files_are_revalidated_on_every_load():
    with TestClient(app) as client:
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
        assert "cache-control" not in {k.lower() for k in client.get("/api/health").headers}
