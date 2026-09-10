"""Work items: the core issue/task/epic record.

`state__group` is a valid `group_by` value for the count action but is NOT a
filterable PQL field. The two vocabularies are documented separately below;
merging them teaches the model to filter on a field the API rejects.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, get_args

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.enums import PriorityEnum
from plane.models.query_params import (
    RetrieveQueryParams,
    WorkItemCountQueryParams,
    WorkItemQueryParams,
)
from plane.models.work_items import (
    CreateWorkItem,
    PaginatedWorkItemResponse,
    UpdateWorkItem,
    WorkItem,
    WorkItemDetail,
    WorkItemSearch,
)
from pydantic import ConfigDict, Field

from plane_mcp.client import get_plane_client_context
from plane_mcp.pql_reference import PQL_FIELD_HINT
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    dump_results,
    envelope,
    ids_of,
    missing,
    one_of,
    opt,
    pql_failure,
    rich_text,
)

logger = get_logger(__name__)

NAME = "workitem"
TITLE = "Work items"

PRIORITIES = get_args(PriorityEnum)

WRITE_FIELDS = (
    "name",
    "assignees",
    "labels",
    "type_id",
    "point",
    "description_html",
    "description_stripped",
    "priority",
    "start_date",
    "target_date",
    "sort_order",
    "is_draft",
    "parent",
    "state",
    "estimate_point",
    "external_source",
    "external_id",
)
QUERY_FIELDS = ("order_by", "per_page", "cursor", "expand", "fields", "external_id", "external_source")
SELF_HOSTED_MAX_PER_PAGE = 1000


class SelfHostedWorkItemQueryParams(WorkItemQueryParams):
    """Plane Community accepts 1000 although the SDK currently caps this field at 100."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    per_page: int | None = Field(None, ge=1, le=SELF_HOSTED_MAX_PER_PAGE)


ACTIONS = (
    Action(
        "list",
        optional=("project_id", "pql", "assignee_id", "state_id", *QUERY_FIELDS),
        note="omit project_id to search the whole workspace",
        read=True,
    ),
    Action(
        "list_mine",
        ("project_id",),
        ("assignee_id", "state_id", *QUERY_FIELDS),
        note=(
            "filters one Plane API page to the authenticated user's tasks and optionally to state_id; "
            "stop when filter_complete=true and request next_cursor only when filter_complete=false. "
            "Without assignee_id, resolving the authenticated user costs one additional API request"
        ),
        read=True,
    ),
    Action(
        "list_my_cards",
        ("state_name",),
        ("project_id", "project_identifier", "assignee_id", "order_by", "expand", "fields"),
        note=(
            "preferred for natural-language requests for my cards/tasks in a board column. "
            "Pass the spoken column name directly (for example Спринт or В работе); this action scans only real "
            "pages, returns all matches with human identifiers such as DEVTELE-1278, and needs no project, state, "
            "cycle, or PQL discovery when PLANE_DEFAULT_PROJECT_ID and PLANE_DEFAULT_PROJECT_IDENTIFIER are set"
        ),
        read=True,
    ),
    Action(
        "list_archived",
        ("project_id",),
        ("pql", "assignee_id", "state_id", *QUERY_FIELDS),
        read=True,
    ),
    Action(
        "retrieve",
        ("project_id", "workitem_id"),
        ("expand", "fields", "external_id", "external_source", "order_by"),
        read=True,
    ),
    Action(
        "retrieve_by_identifier",
        ("workitem_identifier",),
        ("expand", "fields", "external_id", "external_source", "order_by"),
        note="identifier is PROJECT-N, e.g. ENG-42",
        read=True,
    ),
    Action("search", ("query",), ("expand", "fields", "external_id", "external_source", "order_by"), read=True),
    Action(
        "count",
        optional=("project_id", "pql", "group_by", "sub_group_by"),
        note="counts the whole workspace unless project_id narrows it",
        read=True,
    ),
    Action("create", ("project_id", "name"), WRITE_FIELDS[1:]),
    Action("update", ("project_id", "workitem_id"), WRITE_FIELDS, note="only the fields you pass are changed"),
    Action("delete", ("project_id", "workitem_id"), destructive=True),
    Action(
        "archive",
        ("project_id", "workitem_id"),
        ("archive",),
        note="archive defaults to true; pass archive=false to unarchive. Only completed or "
        "cancelled items can be archived",
    ),
    Action(
        "manage_assignee",
        ("project_id", "workitem_id"),
        ("add_user_id", "remove_user_id"),
        note="each takes one id or several; the list is merged, not replaced, and removals apply first",
    ),
    Action(
        "manage_label",
        ("project_id", "workitem_id"),
        ("add_label_id", "remove_label_id"),
        note="each takes one id or several; the list is merged, not replaced, and removals apply first",
    ),
)

GROUP_BY_VALUES = (
    "state_id",
    "state__group",
    "priority",
    "project_id",
    "type_id",
    "labels__id",
    "assignees__id",
    "issue_module__module_id",
    "release_work_items__release_id",
    "cycle_id",
    "milestone_id",
    "created_by",
    "target_date",
    "start_date",
)


FOOTER = (
    f"priority: {', '.join(PRIORITIES)}.\n"
    "UUID fields (assignees, labels, state, parent, type_id) need UUIDs -- list the relevant "
    "resource first if you only have a name.\n"
    "description_stripped is plain text and is wrapped into HTML on save; description_html wins "
    "if both are given.\n"
    "fields is a sparse fieldset: use `project`, not project_id, and `description_html`, not "
    "description.\n"
    f"count group_by and sub_group_by accept: {', '.join(GROUP_BY_VALUES)}. These are grouping "
    "keys only -- they are not PQL filter fields, and filtering on state__group is rejected."
)

LEGACY = {
    "list_work_items": "list",
    "list_archived_work_items": "list_archived",
    "retrieve_work_item": "retrieve",
    "retrieve_work_item_by_identifier": "retrieve_by_identifier",
    "search_work_items": "search",
    "count_work_items": "count",
    "create_work_item": "create",
    "update_work_item": "update",
    "delete_work_item": "delete",
    "manage_work_item_archive": "archive",
    "manage_work_item_assignee": "manage_assignee",
    "manage_work_item_label": "manage_label",
}


def _scoped_pql(pql: str, project_id: str) -> str:
    """Narrow a PQL filter to one project, since the count endpoint is workspace-wide."""
    if not project_id:
        return pql
    scope = f'project = "{project_id}"'
    return f"({pql}) AND {scope}" if pql else scope


def _with_fields(value: str, *required: str) -> str:
    """Add fields needed for local compatibility filtering without duplicates."""
    fields = [part.strip() for part in value.split(",") if part.strip()]
    for field in required:
        if field not in fields:
            fields.append(field)
    return ",".join(fields)


def _related_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id")
    return getattr(value, "id", None)


def _related_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return getattr(value, "name", None)


def _mine_on_page(items: Any, assignee_id: str, state_id: str, state_name: str = "") -> list[Any]:
    """Filter one Plane page without issuing a retrieve call per work item."""
    matches = []
    for item in items or []:
        assignees = getattr(item, "assignees", []) or []
        if assignee_id not in {_related_id(assignee) for assignee in assignees}:
            continue
        if state_id and _related_id(getattr(item, "state", None)) != state_id:
            continue
        if state_name and (_related_name(getattr(item, "state", None)) or "").casefold() != state_name.casefold():
            continue
        matches.append(item)
    return matches


def _project_identifier(item: Any, fallback: str) -> str:
    project = getattr(item, "project", None)
    if isinstance(project, dict):
        return project.get("identifier") or fallback
    return getattr(project, "identifier", None) or fallback


def _dump_my_cards(items: list[Any], fields: str, project_identifier: str) -> list[Any]:
    dumped = dump_results(items, opt(fields))
    for item, result in zip(items, dumped, strict=True):
        sequence_id = getattr(item, "sequence_id", None)
        identifier = _project_identifier(item, project_identifier)
        if isinstance(result, dict) and identifier and sequence_id is not None:
            result["identifier"] = f"{identifier}-{sequence_id}"
    return dumped


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Work items -- issues, tasks and epics.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem(  # noqa: PLR0911, PLR0912 - one branch per action is the point
        action: Literal[
            "list",
            "list_mine",
            "list_my_cards",
            "list_archived",
            "retrieve",
            "retrieve_by_identifier",
            "search",
            "count",
            "create",
            "update",
            "delete",
            "archive",
            "manage_assignee",
            "manage_label",
        ],
        project_id: str = "",
        workitem_id: str = "",
        workitem_identifier: str = "",
        query: str = "",
        pql: Annotated[str, Field(description=PQL_FIELD_HINT)] = "",
        assignee_id: str = "",
        state_id: str = "",
        state_name: str = "",
        project_identifier: str = "",
        group_by: str = "",
        sub_group_by: str = "",
        name: str = "",
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        type_id: str = "",
        point: int = 0,
        description_html: str = "",
        description_stripped: str = "",
        priority: str = "",
        start_date: str = "",
        target_date: str = "",
        sort_order: float = 0,
        parent: str = "",
        state: str = "",
        estimate_point: str = "",
        add_user_id: str = "",
        remove_user_id: str = "",
        add_label_id: str = "",
        remove_label_id: str = "",
        external_source: str = "",
        external_id: str = "",
        order_by: str = "",
        expand: str = "",
        fields: str = "",
        cursor: str = "",
        per_page: int = 0,
        # Tri-state: False publishes a draft, unset leaves the flag alone.
        is_draft: bool | None = None,
        archive: bool = True,
    ) -> WorkItem | WorkItemDetail | WorkItemSearch | dict[str, Any] | list[Any] | str | None:
        client, workspace_slug = get_plane_client_context()

        if error := one_of("priority", priority, PRIORITIES):
            return error
        if error := one_of("group_by", group_by, GROUP_BY_VALUES):
            return error
        if error := one_of("sub_group_by", sub_group_by, GROUP_BY_VALUES):
            return error

        def retrieve_params() -> RetrieveQueryParams:
            return RetrieveQueryParams(
                expand=opt(expand),
                fields=opt(fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
                order_by=opt(order_by),
            )

        def write_payload() -> dict[str, Any]:
            return {
                "name": opt(name),
                "assignees": coerce_list(assignees),
                "labels": coerce_list(labels),
                "type_id": opt(type_id),
                "point": opt(point),
                "description_html": rich_text(description_html, description_stripped),
                "priority": opt(priority),
                "start_date": opt(start_date),
                "target_date": opt(target_date),
                "sort_order": opt(sort_order),
                "is_draft": is_draft,
                "external_source": opt(external_source),
                "external_id": opt(external_id),
                "parent": opt(parent),
                "state": opt(state),
                "estimate_point": opt(estimate_point),
            }

        if action in ("list", "list_mine", "list_my_cards", "list_archived"):
            if action == "list_archived" and not project_id:
                return missing(action, "project_id")
            if action == "list_mine" and not project_id:
                return missing(action, "project_id")
            if action == "list_my_cards":
                if not state_name:
                    return missing(action, "state_name")
                project_id = project_id or os.getenv("PLANE_DEFAULT_PROJECT_ID", "")
                project_identifier = project_identifier or os.getenv("PLANE_DEFAULT_PROJECT_IDENTIFIER", "")
                if not project_id:
                    return "Error: list_my_cards needs project_id or PLANE_DEFAULT_PROJECT_ID."
                if not project_identifier:
                    return "Error: list_my_cards needs project_identifier or PLANE_DEFAULT_PROJECT_IDENTIFIER."
            if action in ("list_mine", "list_my_cards"):
                assignee_id = assignee_id or os.getenv("PLANE_CURRENT_USER_ID", "")
                assignee_id = assignee_id or client.users.get_me().id or ""
                if not assignee_id:
                    return "Error: Plane returned the authenticated user without an id."
            if action == "list_my_cards":
                required_fields = ("assignees", "state", "sequence_id", "project")
                request_fields = _with_fields(fields, *required_fields) if fields else fields
                request_expand = _with_fields(expand, "assignees", "state", "project")
                matches: list[Any] = []
                page_cursor = ""
                seen_cursors: set[str] = set()
                pages_scanned = 0
                while True:
                    response = client.work_items.list(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        params=SelfHostedWorkItemQueryParams(
                            order_by=opt(order_by),
                            per_page=SELF_HOSTED_MAX_PER_PAGE,
                            cursor=opt(page_cursor),
                            expand=opt(request_expand),
                            fields=opt(request_fields),
                        ),
                    )
                    pages_scanned += 1
                    matches.extend(_mine_on_page(response.results, assignee_id, "", state_name))
                    if not bool(response.next_page_results):
                        break
                    next_cursor = response.next_cursor
                    if not next_cursor or next_cursor in seen_cursors:
                        raise RuntimeError("Plane reported another work-item page without a usable new cursor.")
                    seen_cursors.add(next_cursor)
                    page_cursor = next_cursor
                return {
                    "results": _dump_my_cards(matches, fields, project_identifier),
                    "count": len(matches),
                    "filter_complete": True,
                    "pages_scanned": pages_scanned,
                    "assignee_id": assignee_id,
                    "state_name": state_name,
                    "project_identifier": project_identifier,
                }
            request_fields = fields
            request_expand = expand
            if action == "list_mine":
                request_fields = _with_fields(fields, "assignees", "state") if fields else fields
                request_expand = _with_fields(expand, "assignees", "state")
            params = WorkItemQueryParams(
                pql=opt(pql),
                order_by=opt(order_by),
                per_page=opt(per_page),
                cursor=opt(cursor),
                expand=opt(request_expand),
                fields=opt(request_fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
            )
            try:
                if action == "list_archived":
                    response = client.work_items.list_archived(
                        workspace_slug=workspace_slug, project_id=project_id, params=params
                    )
                elif project_id:
                    response: PaginatedWorkItemResponse = client.work_items.list(
                        workspace_slug=workspace_slug, project_id=project_id, params=params
                    )
                else:
                    response = client.work_items.list_workspace(workspace_slug=workspace_slug, params=params)
            except HttpError as exc:
                failure = pql_failure("workitem", action, pql, exc)
                if failure:
                    return failure
                raise
            result = envelope(response, opt(fields))
            if action == "list_mine":
                matches = _mine_on_page(response.results, assignee_id, state_id)
                result["results"] = dump_results(matches, opt(fields))
                result["count"] = len(matches)
                result["total_count"] = None
                has_next_page = bool(response.next_page_results)
                result["next_cursor"] = response.next_cursor if has_next_page else None
                result["next_page_results"] = has_next_page
                result["prev_page_results"] = bool(response.prev_page_results)
                result["filter_complete"] = not has_next_page
                result["assignee_id"] = assignee_id
            return result

        if action == "count":
            scoped = _scoped_pql(pql, project_id)
            try:
                response = client.work_items.count_workspace(
                    workspace_slug=workspace_slug,
                    params=WorkItemCountQueryParams(
                        pql=opt(scoped), group_by=opt(group_by), sub_group_by=opt(sub_group_by)
                    ),
                )
            except HttpError as exc:
                failure = pql_failure("workitem", action, scoped, exc)
                if failure:
                    return failure
                raise
            return response.model_dump()

        if action == "search":
            if not query:
                return missing(action, "query")
            return client.work_items.search(workspace_slug=workspace_slug, query=query, params=retrieve_params())

        if action == "retrieve_by_identifier":
            if not workitem_identifier:
                return missing(action, "workitem_identifier")
            head, _, sequence = workitem_identifier.rpartition("-")
            if not head or not sequence.isdigit():
                return (
                    f"Error: invalid work item identifier {workitem_identifier!r}. "
                    "Expected PROJECT-N, for example ENG-42."
                )
            return client.work_items.retrieve_by_identifier(
                workspace_slug=workspace_slug,
                project_identifier=head,
                issue_identifier=int(sequence),
                params=retrieve_params(),
            )

        if not project_id:
            return missing(action, "project_id")

        if action == "create":
            if not name:
                return missing(action, "name")
            return client.work_items.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateWorkItem(**write_payload()),
            )

        if not workitem_id:
            return missing(action, "workitem_id")

        if action == "retrieve":
            return client.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                params=retrieve_params(),
            )

        if action == "update":
            return client.work_items.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                data=UpdateWorkItem(**write_payload()),
            )

        if action == "delete":
            client.work_items.delete(workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id)
            return None

        if action == "archive":
            operation = client.work_items.archive if archive else client.work_items.unarchive
            operation(workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id)
            return {"workitem_id": workitem_id, "archived": archive}

        # manage_assignee / manage_label: read the current set, mutate it, write it back.
        add, remove, field = (
            (add_user_id, remove_user_id, "assignees")
            if action == "manage_assignee"
            else (add_label_id, remove_label_id, "labels")
        )
        if not add and not remove:
            return missing(action, f"add_{field[:-1]}_id or remove_{field[:-1]}_id")
        # Either side takes one id or several, so adding three assignees is one call.
        adding, removing = coerce_list(add) or [], coerce_list(remove) or []
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
        )
        ids = [value for value in ids_of(getattr(current, field)) if value not in removing]
        ids += [value for value in adding if value not in ids]
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            data=UpdateWorkItem(**{field: ids}),
        )
