"""Server instructions, sent to a client before it lists the tools.

The epic recipe names the tools that do each step, so `test_references.py`
follows every one of them and fails if a name stops resolving.
"""

from __future__ import annotations

SERVER_INSTRUCTIONS = """
## My board cards

For requests such as “мои задачи/карточки со спринта”, “мои задачи в работе”,
or “show my cards in Sprint/In Progress”, call `workitem list_my_cards`
directly and pass the board column as state_name. In these requests “спринт”
means the workflow column named “Спринт”, not a Plane cycle. Do not call project,
cycle, state, or get_pql_reference first: project and user defaults are configured
server-side. The result already contains human-readable identifiers such as
DEVTELE-1278. Use cycle actions only when the user explicitly asks for a Plane
cycle or a date-based iteration.

## Epics

There are no epic tools — an epic is a work item whose type is named "Epic". Work
items always belong to a project; ask which if one is not named.
1. type = `workitem_type resolve` with project_id and name="Epic" — type.id is the type_id.
2. Create: `workitem create` with project_id, type_id=type.id, name=...
3. List: `workitem list` with project_id and pql='type = "<type id>"'.
4. Read / update / delete / nest: `workitem retrieve` / `workitem update` /
   `workitem delete` by work item id (set parent=<work item id> to nest).
5. List an epic's children: `workitem list` with pql='childOf("<EPIC-IDENTIFIER>")'
   using the epic's human-readable identifier (e.g. "PROJ-12") from `workitem retrieve`.
"""
