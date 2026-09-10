"""Compatibility with self-hosted Plane versions that predate lite routes and PQL."""

from __future__ import annotations

from types import SimpleNamespace

from plane.errors.errors import HttpError
from plane.models.states import StateLite
from plane.models.users import UserLite
from plane.models.work_items import WorkItem


def _not_found() -> HttpError:
    return HttpError("Not Found", status_code=404, response={"detail": "Not found."})


def test_project_list_falls_back_when_projects_lite_is_missing(registered, spy):
    spy.returns["projects.list_lite"] = _not_found()

    registered["project"].fn(action="list")

    assert spy.recorder.methods[-2:] == ["projects.list_lite", "projects.list"]


def test_cycle_list_falls_back_when_cycles_lite_is_missing(registered, spy):
    spy.returns["cycles.list_lite"] = _not_found()

    registered["cycle"].fn(action="list", project_id="project-1", status="current")

    assert spy.recorder.methods == ["cycles.list_lite", "cycles.list"]
    assert spy.recorder.calls[-1].kwargs["params"].status == "current"


def _page(*items, next_cursor=None, next_page_results=False):
    return SimpleNamespace(
        results=list(items),
        total_count=len(items),
        count=len(items),
        next_cursor=next_cursor,
        prev_cursor=None,
        next_page_results=next_page_results,
        prev_page_results=False,
    )


def test_list_mine_returns_only_matching_items_without_per_item_calls(registered, spy):
    spy.returns["work_items.list"] = _page(
        WorkItem(id="mine", name="Mine", state="sprint-state", assignees=[UserLite(id="me")]),
        WorkItem(id="theirs", name="Theirs", state="sprint-state", assignees=[UserLite(id="other")]),
        WorkItem(id="backlog", name="Backlog", state="backlog-state", assignees=[UserLite(id="me")]),
    )

    result = registered["workitem"].fn(action="list_mine", project_id="project-1", state_id="sprint-state")

    assert spy.recorder.methods == ["users.get_me", "work_items.list"]
    assert [item["id"] for item in result["results"]] == ["mine"]
    assert result["count"] == 1
    assert result["filter_complete"] is True
    assert result["assignee_id"] == "me"


def test_list_mine_ignores_a_synthetic_cursor_when_plane_says_there_is_no_next_page(registered, spy):
    spy.returns["work_items.list"] = _page(next_cursor="100:1:0", next_page_results=False)

    result = registered["workitem"].fn(action="list_mine", project_id="project-1", assignee_id="me")

    assert result["filter_complete"] is True
    assert result["next_cursor"] is None
    assert result["next_page_results"] is False


def test_list_mine_keeps_a_cursor_only_when_plane_reports_another_page(registered, spy):
    spy.returns["work_items.list"] = _page(next_cursor="100:1:0", next_page_results=True)

    result = registered["workitem"].fn(action="list_mine", project_id="project-1", assignee_id="me")

    assert result["filter_complete"] is False
    assert result["next_cursor"] == "100:1:0"
    assert result["next_page_results"] is True


def test_list_mine_description_tells_agents_when_to_stop(registered):
    description = registered["workitem"].description

    assert "stop when filter_complete=true" in description
    assert "request next_cursor only when filter_complete=false" in description


def test_list_my_cards_uses_configured_defaults_and_returns_human_identifiers(registered, spy, monkeypatch):
    monkeypatch.setenv("PLANE_DEFAULT_PROJECT_ID", "project-1")
    monkeypatch.setenv("PLANE_DEFAULT_PROJECT_IDENTIFIER", "DEVTELE")
    monkeypatch.setenv("PLANE_CURRENT_USER_ID", "me")
    spy.returns["work_items.list"] = _page(
        WorkItem(
            id="mine", name="Mine", sequence_id=1278, state=StateLite(name="Спринт"), assignees=[UserLite(id="me")]
        ),
        WorkItem(
            id="theirs",
            name="Theirs",
            sequence_id=1279,
            state=StateLite(name="Спринт"),
            assignees=[UserLite(id="other")],
        ),
        WorkItem(
            id="started",
            name="Started",
            sequence_id=1280,
            state=StateLite(name="В работе"),
            assignees=[UserLite(id="me")],
        ),
    )

    result = registered["workitem"].fn(action="list_my_cards", state_name="спринт")

    assert spy.recorder.methods == ["work_items.list"]
    assert spy.recorder.only().kwargs["params"].per_page == 1000
    assert result["count"] == 1
    assert result["results"][0]["identifier"] == "DEVTELE-1278"
    assert result["filter_complete"] is True
    assert result["pages_scanned"] == 1


def test_list_my_cards_reads_only_pages_that_plane_marks_as_real(registered, spy):
    first = _page(
        WorkItem(id="first", sequence_id=1, state=StateLite(name="В работе"), assignees=[UserLite(id="me")]),
        next_cursor="100:1:0",
        next_page_results=True,
    )
    last = _page(
        WorkItem(id="last", sequence_id=2, state=StateLite(name="В работе"), assignees=[UserLite(id="me")]),
        next_cursor="100:2:0",
        next_page_results=False,
    )

    class Pages:
        def __init__(self):
            self.pages = [first, last]
            self.index = 0

        @property
        def results(self):
            return self.pages[self.index].results

        @property
        def next_page_results(self):
            return self.pages[self.index].next_page_results

        @property
        def next_cursor(self):
            cursor = self.pages[self.index].next_cursor
            self.index += 1
            return cursor

    spy.returns["work_items.list"] = Pages()

    result = registered["workitem"].fn(
        action="list_my_cards",
        state_name="В работе",
        project_id="project-1",
        project_identifier="DEVTELE",
        assignee_id="me",
    )

    assert spy.recorder.methods == ["work_items.list", "work_items.list"]
    assert [item["identifier"] for item in result["results"]] == ["DEVTELE-1", "DEVTELE-2"]
    assert result["pages_scanned"] == 2


def test_server_instructions_route_russian_board_requests_directly():
    from plane_mcp.instructions import SERVER_INSTRUCTIONS

    assert "мои задачи/карточки со спринта" in SERVER_INSTRUCTIONS
    assert "workitem list_my_cards" in SERVER_INSTRUCTIONS
    assert "not a Plane cycle" in SERVER_INSTRUCTIONS


def test_explicit_assignee_avoids_a_profile_request(registered, spy):
    spy.returns["work_items.list"] = _page()

    registered["workitem"].fn(
        action="list_mine",
        project_id="project-1",
        assignee_id="user-1",
        state_id="state-1",
    )

    call = spy.recorder.only()
    assert call.method == "work_items.list"


def test_list_mine_refuses_an_authenticated_user_without_an_id(registered, spy):
    spy.returns["users.get_me"] = UserLite()

    result = registered["workitem"].fn(action="list_mine", project_id="project-1")

    assert result == "Error: Plane returned the authenticated user without an id."
    assert spy.recorder.methods == ["users.get_me"]
