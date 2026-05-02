"""Tests for PRISM Mission UI — Session 6.

Covers:
  - 4 proxy route handlers in PrismService (missions_catalog, missions_all_runs,
    mission_schema, mission_run_workflow)
  - prism.html structural checks (nav-rail, switchSurface, JS functions, CSS)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

HTML_PATH = Path(__file__).parent.parent / "cascadia" / "dashboard" / "prism.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _make_svc(tmp_path):
    """Return a minimal PrismService instance without running __init__."""
    from cascadia.dashboard.prism import PrismService
    svc = PrismService.__new__(PrismService)
    svc._ports = {"mission_manager": 6207}
    return svc


# ── Proxy route: missions_catalog ─────────────────────────────────────────────

class TestMissionsCatalog:

    def test_returns_200_with_data(self, tmp_path):
        svc = _make_svc(tmp_path)
        fake = {"missions": [{"id": "revenue_desk", "name": "Revenue Desk"}]}
        with patch("cascadia.dashboard.prism._http_get", return_value=fake):
            code, body = svc.missions_catalog({})
        assert code == 200
        assert body["missions"][0]["id"] == "revenue_desk"

    def test_returns_503_when_unavailable(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value=None):
            code, body = svc.missions_catalog({})
        assert code == 503
        assert body["error"] == "mission_manager_unavailable"

    def test_503_includes_empty_missions_list(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value=None):
            _, body = svc.missions_catalog({})
        assert "missions" in body
        assert body["missions"] == []

    def test_proxies_to_port_6207(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value={}) as mock:
            svc.missions_catalog({})
        mock.assert_called_once_with(6207, "/api/missions/catalog")


# ── Proxy route: missions_all_runs ────────────────────────────────────────────

class TestMissionsAllRuns:

    def test_returns_200_with_runs(self, tmp_path):
        svc = _make_svc(tmp_path)
        fake = {"runs": [{"id": "r1", "status": "completed"}]}
        with patch("cascadia.dashboard.prism._http_get", return_value=fake):
            code, body = svc.missions_all_runs({})
        assert code == 200
        assert len(body["runs"]) == 1

    def test_returns_503_when_unavailable(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value=None):
            code, body = svc.missions_all_runs({})
        assert code == 503
        assert body["runs"] == []

    def test_passes_mission_id_query_param(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value={"runs": []}) as mock:
            svc.missions_all_runs({"mission_id": "revenue_desk"})
        call_path = mock.call_args[0][1]
        assert "mission_id=revenue_desk" in call_path

    def test_no_mission_id_omits_query_param(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value={"runs": []}) as mock:
            svc.missions_all_runs({})
        call_path = mock.call_args[0][1]
        assert "?" not in call_path


# ── Proxy route: mission_schema ───────────────────────────────────────────────

class TestMissionSchema:

    def test_returns_200_with_schema(self, tmp_path):
        svc = _make_svc(tmp_path)
        fake = {"sections": [{"type": "stat_row", "stats": []}]}
        with patch("cascadia.dashboard.prism._http_get", return_value=fake):
            code, body = svc.mission_schema({"mission_id": "revenue_desk"})
        assert code == 200
        assert "sections" in body

    def test_returns_503_when_unavailable(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value=None):
            code, body = svc.mission_schema({"mission_id": "revenue_desk"})
        assert code == 503

    def test_includes_mission_id_in_path(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_get", return_value={}) as mock:
            svc.mission_schema({"mission_id": "growth_desk"})
        call_path = mock.call_args[0][1]
        assert "growth_desk" in call_path


# ── Proxy route: mission_run_workflow ─────────────────────────────────────────

class TestMissionRunWorkflow:

    def test_returns_200_on_success(self, tmp_path):
        svc = _make_svc(tmp_path)
        fake = {"mission_run_id": "run-xyz", "status": "running"}
        with patch("cascadia.dashboard.prism._http_post", return_value=fake):
            code, body = svc.mission_run_workflow(
                {"mission_id": "revenue_desk", "workflow_id": "qualify_lead"}
            )
        assert code == 200
        assert body["mission_run_id"] == "run-xyz"

    def test_returns_503_when_unavailable(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_post", return_value=None):
            code, body = svc.mission_run_workflow(
                {"mission_id": "revenue_desk", "workflow_id": "qualify_lead"}
            )
        assert code == 503

    def test_posts_workflow_id_in_body(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_post", return_value={}) as mock:
            svc.mission_run_workflow(
                {"mission_id": "growth_desk", "workflow_id": "send_campaign"}
            )
        _, _, posted_body = mock.call_args[0]
        assert posted_body.get("workflow_id") == "send_campaign"

    def test_posts_to_correct_mission_path(self, tmp_path):
        svc = _make_svc(tmp_path)
        with patch("cascadia.dashboard.prism._http_post", return_value={}) as mock:
            svc.mission_run_workflow(
                {"mission_id": "operations_desk_lite", "workflow_id": "daily_report"}
            )
        call_path = mock.call_args[0][1]
        assert "operations_desk_lite" in call_path


# ── HTML structural checks ────────────────────────────────────────────────────

class TestPrismHtmlMissionsNav:

    def test_nav_missions_button_exists(self):
        assert 'id="nav-missions"' in _html()

    def test_nav_missions_calls_switch_surface(self):
        html = _html()
        assert "switchSurface('missions')" in html

    def test_nav_missions_tooltip_is_missions(self):
        html = _html()
        btn_start = html.index('id="nav-missions"')
        btn_end = html.index("</button>", btn_start) + len("</button>")
        snippet = html[btn_start:btn_end]
        assert "Missions" in snippet

    def test_nav_runs_tooltip_updated_to_run_history(self):
        html = _html()
        idx = html.index('id="nav-runs"')
        snippet = html[idx:idx+200]
        assert "Run History" in snippet


class TestPrismHtmlMissionsJS:

    def test_render_missions_surface_function_exists(self):
        assert "function renderMissionsSurface" in _html() or \
               "async function renderMissionsSurface" in _html()

    def test_select_mission_function_exists(self):
        assert "function selectMission" in _html()

    def test_launch_mission_workflow_function_exists(self):
        assert "function launchMissionWorkflow" in _html()

    def test_refresh_mission_catalog_function_exists(self):
        assert "function refreshMissionCatalog" in _html()

    def test_switch_surface_handles_missions(self):
        html = _html()
        assert "surface==='missions'" in html or "surface === 'missions'" in html

    def test_all_seven_section_types_handled(self):
        html = _html()
        for t in ('health_card', 'stat_row', 'table', 'approval_cards',
                  'run_list', 'action_buttons', 'brief_card'):
            assert t in html, f"section type '{t}' not handled in JS"

    def test_set_approval_mission_filter_function_exists(self):
        assert "function setApprovalMissionFilter" in _html()

    def test_approval_mission_filter_options_present(self):
        html = _html()
        for key in ('revenue_desk', 'growth_desk', 'operations_desk', '__high_risk__'):
            assert key in html, f"mission filter key '{key}' missing"

    def test_missions_sidebar_group_in_render_sidebar(self):
        html = _html()
        assert "'missions'" in html or '"missions"' in html
        assert "buildSbGroup" in html
        idx = html.index("function renderSidebar")
        sidebar_fn = html[idx:idx+3000]
        assert "Missions" in sidebar_fn

    def test_missions_catalog_polled_on_init(self):
        html = _html()
        assert "refreshMissionCatalog" in html
        init_block = html[html.rfind("(async()=>{"):]
        assert "refreshMissionCatalog" in init_block

    def test_mission_filter_css_classes_present(self):
        html = _html()
        assert ".mission-card" in html
        assert ".mission-section" in html
        assert ".mission-filter" in html
