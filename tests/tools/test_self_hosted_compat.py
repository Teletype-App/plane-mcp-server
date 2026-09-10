"""Compatibility with self-hosted Plane versions that predate lite routes and PQL."""

from __future__ import annotations

from types import SimpleNamespace

from plane.errors.errors import HttpError
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


def _page(*items):
    return SimpleNamespace(
        results=list(items),
        total_count=len(items),
        count=len(items),
        next_cursor=None,
        prev_cursor=None,
        next_page_results=False,
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
