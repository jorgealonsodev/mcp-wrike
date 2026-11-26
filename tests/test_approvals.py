"""Approval tools (PRD F4)."""

import asyncio

from conftest import call, params

from mcp_wrike import server

LAURA = {"id": "KUAAAAA1", "firstName": "Laura", "lastName": "Gómez", "profiles": []}
APPROVAL = {
    "id": "IEAAPPR1", "title": "Logo v2", "status": "Pending", "taskId": "T1",
    "dueDate": "2026-12-01", "description": "Check the logo",
    "decisions": [
        {"approverId": "KUAAAAA1", "status": "Rejected", "comment": "Too dark"},
    ],
}


def _setup(api):
    api.on("GET", "/contacts", [LAURA])


async def test_get_approvals_of_a_task_shows_decisions(api):
    _setup(api)
    api.on("GET", "/tasks/T1/approvals", [APPROVAL])
    text = await call("get_approvals", task_id="T1")
    assert "**Logo v2** — `IEAAPPR1` | Pending" in text
    assert "- Due: 2026-12-01" in text
    assert "  - Laura Gómez (`KUAAAAA1`): Rejected — Too dark" in text


async def test_get_approvals_of_a_folder_filters_statuses_locally(api):
    _setup(api)
    approved = {**APPROVAL, "id": "A2", "status": "Approved"}
    api.on("GET", "/folders/F1/approvals", [APPROVAL, approved])
    text = await call("get_approvals", folder_id="F1", statuses=["Approved"])
    assert "`A2`" in text and "IEAAPPR1" not in text


async def test_get_approvals_account_wide_sends_status_filter(api):
    _setup(api)
    api.on("GET", "/approvals", [APPROVAL])
    await call("get_approvals", statuses=["Pending"])
    assert params(api.calls("GET", "/api/v4/approvals")[0]) == {"statuses": ["Pending"]}


async def test_get_approvals_rejects_task_and_folder_together(api):
    text = await call("get_approvals", task_id="T1", folder_id="F1")
    assert text == "Error: Pass task_id or folder_id, not both"


async def test_create_approval_sends_query_params_with_resolved_approvers(api):
    _setup(api)
    api.on("GET", "/ids", [{"id": "KUAAAAA1", "apiV2Id": "55"}])
    api.on("POST", "/tasks/T1/approvals", [APPROVAL])
    text = await call(
        "create_approval", task_id="T1", approvers=["55"],
        due_date="2026-12-01", description="Check the logo",
    )
    assert text.startswith("**Approval created:**")
    assert params(api.calls("POST", "/api/v4/tasks/T1/approvals")[0]) == {
        "approvers": ["KUAAAAA1"], "dueDate": "2026-12-01", "description": "Check the logo",
    }


async def test_create_approval_on_a_folder(api):
    _setup(api)
    api.on("POST", "/folders/F1/approvals", [{**APPROVAL, "taskId": None, "folderId": "F1"}])
    text = await call("create_approval", folder_id="F1")
    assert "- Folder: `F1`" in text


async def test_create_approval_needs_exactly_one_target(api):
    assert await call("create_approval") == "Error: Pass exactly one of task_id or folder_id"


async def test_create_approval_rejects_bad_due_date_before_calling(api):
    api.on("POST", "/tasks/T1/approvals", [APPROVAL])
    text = await call("create_approval", task_id="T1", due_date="1/12/2026")
    assert "due_date must be a date in YYYY-MM-DD format" in text
    assert api.calls("POST", "/api/v4/tasks/T1/approvals") == []


async def test_update_approval(api):
    _setup(api)
    api.on("PUT", "/approvals/IEAAPPR1", [APPROVAL])
    text = await call(
        "update_approval", approval_id="IEAAPPR1", add_approvers=["KUAAAAA1"],
        remove_approvers=["KUBBBBB2"], due_date="2026-12-05",
    )
    assert text.startswith("**Approval updated:**")
    assert params(api.calls("PUT", "/api/v4/approvals/IEAAPPR1")[0]) == {
        "addApprovers": ["KUAAAAA1"], "removeApprovers": ["KUBBBBB2"], "dueDate": "2026-12-05",
    }


async def test_update_approval_without_changes_fails(api):
    text = await call("update_approval", approval_id="IEAAPPR1")
    assert text.startswith("Error: Nothing to update")


async def test_cancel_approval_uses_delete(api):
    _setup(api)
    api.on("DELETE", "/approvals/IEAAPPR1", [{**APPROVAL, "status": "Cancelled"}])
    text = await call("update_approval", approval_id="IEAAPPR1", cancel=True)
    assert text.startswith("**Approval cancelled:**")
    assert "| Cancelled" in text
    assert len(api.calls("DELETE", "/api/v4/approvals/IEAAPPR1")) == 1


def test_write_tools_say_they_are_writes():
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    for name in ("create_approval", "update_approval"):
        assert "This is a write" in tools[name].description
