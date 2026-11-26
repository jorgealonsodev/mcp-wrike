"""Blueprint tools with async launch (PRD F4)."""

import asyncio

import pytest
from conftest import call, params

from mcp_wrike import client as client_mod
from mcp_wrike import server


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    return sleeps


def _jobs(api, *states):
    """Serve GET /async_job/JOB1 with each state in turn, repeating the last."""
    seq = list(states)

    def handle(request):
        return [seq.pop(0) if len(seq) > 1 else seq[0]]

    api.on("GET", "/async_job/JOB1", handle)


async def test_list_blueprints_both_kinds_skipping_the_root(api):
    api.on("GET", "/folder_blueprints", [
        {"id": "BPROOT", "title": "Root", "scope": "BpRoot"},
        {"id": "IEABP1", "title": "Onboarding", "scope": "FolderBp",
         "permalink": "https://www.wrike.com/open.htm?id=1"},
    ])
    api.on("GET", "/task_blueprints", [
        {"id": "IEABT1", "title": "Weekly report", "scope": "TaskBp"},
    ])
    text = await call("list_blueprints")
    assert "## Folder blueprints (1)" in text
    assert "- **Onboarding** — `IEABP1` | https://www.wrike.com/open.htm?id=1" in text
    assert "## Task blueprints (1)" in text and "`IEABT1`" in text
    assert "BPROOT" not in text


async def test_list_blueprints_of_a_space_by_title(api):
    api.on("GET", "/spaces/IEASPACE/task_blueprints", [
        {"id": "A", "title": "Weekly report"}, {"id": "B", "title": "Invoice"},
    ])
    text = await call("list_blueprints", blueprint_type="task", space_id="IEASPACE",
                      title_contains="WEEKLY")
    assert "`A`" in text and "`B`" not in text and "Folder blueprints" not in text


async def test_launch_folder_blueprint_waits_and_returns_id_and_link(api, no_sleep):
    api.on("GET", "/ids", [{"id": "IEAPARENT", "apiV2Id": "77"}])
    api.on("POST", "/folder_blueprints/IEABP1/launch_async", [{"id": "JOB1", "status": "InQueue"}])
    _jobs(api,
          {"id": "JOB1", "status": "InProgress"},
          {"id": "JOB1", "status": "Completed", "result": {"folderId": "IEANEW"}})
    api.on("GET", "/folders/IEANEW", [{
        "id": "IEANEW", "title": "Acme onboarding", "project": {"ownerIds": []},
        "permalink": "https://www.wrike.com/open.htm?id=999",
    }])
    text = await call(
        "launch_blueprint", blueprint_id="IEABP1", blueprint_type="folder",
        title="Acme onboarding", parent_id="https://www.wrike.com/open.htm?id=77",
        reschedule_date="2027-01-04", reschedule_mode="Start",
    )
    assert "- Project: **Acme onboarding** — `IEANEW`" in text
    assert "- Link: https://www.wrike.com/open.htm?id=999" in text
    assert params(api.calls("POST", "/api/v4/folder_blueprints/IEABP1/launch_async")[0]) == {
        "title": "Acme onboarding", "parent": "IEAPARENT",
        "rescheduleDate": "2027-01-04", "rescheduleMode": "Start",
    }
    assert no_sleep == [2.0, 2.0]


async def test_launch_task_blueprint_as_subtasks(api):
    api.on("POST", "/task_blueprints/IEABT1/launch_async", [
        {"id": "JOB1", "status": "Completed", "result": {"taskId": "TNEW"}}
    ])
    api.on("GET", "/tasks/TNEW", [{"id": "TNEW", "title": "Weekly report", "status": "Active"}])
    text = await call("launch_blueprint", blueprint_id="IEABT1", blueprint_type="task",
                      title="Weekly report", super_task_id="TPARENT")
    assert "- Task: **Weekly report** — `TNEW`" in text
    sent = params(api.calls("POST", "/api/v4/task_blueprints/IEABT1/launch_async")[0])
    assert sent == {"title": "Weekly report", "superTaskId": "TPARENT"}


async def test_launch_still_running_returns_the_job_id(api, no_sleep):
    api.on("POST", "/folder_blueprints/IEABP1/launch_async", [{"id": "JOB1", "status": "InQueue"}])
    _jobs(api, {"id": "JOB1", "status": "InProgress", "progressPercent": 40})
    text = await call("launch_blueprint", blueprint_id="IEABP1", blueprint_type="folder",
                      title="X", parent_id="IEAPARENT", timeout_seconds=6)
    assert text.startswith("Blueprint launch still running: job `JOB1` (InProgress, 40%)")
    assert no_sleep == [2.0, 2.0, 2.0]


async def test_launch_failure_reports_the_error(api):
    api.on("POST", "/folder_blueprints/IEABP1/launch_async", [
        {"id": "JOB1", "status": "Failed", "errorMessage": "Entry limit exceeded"}
    ])
    text = await call("launch_blueprint", blueprint_id="IEABP1", blueprint_type="folder",
                      title="X", parent_id="IEAPARENT")
    assert text == "Blueprint launch Failed: Entry limit exceeded"


@pytest.mark.parametrize(
    "args,message",
    [
        ({"blueprint_type": "folder"}, "A folder blueprint needs parent_id"),
        ({"blueprint_type": "task"}, "exactly one of parent_id or super_task_id"),
        ({"blueprint_type": "task", "parent_id": "F", "super_task_id": "T"},
         "exactly one of parent_id or super_task_id"),
        ({"blueprint_type": "folder", "parent_id": "F", "reschedule_mode": "Start"},
         "reschedule_mode needs reschedule_date"),
        ({"blueprint_type": "folder", "parent_id": "F", "reschedule_date": "4/1/2027"},
         "reschedule_date must be a date"),
    ],
)
async def test_launch_validates_before_calling(api, args, message):
    text = await call("launch_blueprint", blueprint_id="IEABP1", title="X", **args)
    assert text.startswith("Error: ") and message in text
    assert [r for r in api.requests if r.method == "POST"] == []


async def test_submit_request_form_still_polls_its_job(api, no_sleep):
    api.on("GET", "/request_forms/IEAFORM", [{"id": "IEAFORM", "pages": []}])
    api.on("POST", "/request_forms/IEAFORM/submit", [{"id": "JOB1", "status": "InQueue"}])
    _jobs(api, {"id": "JOB1", "status": "Completed", "taskId": "TNEW"})
    text = await call("submit_request_form", form_id="IEAFORM", fields=[])
    assert text.startswith("**Request form job:** `JOB1` — Completed")
    assert no_sleep == [2]


def test_launch_blueprint_says_it_is_a_write():
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert "This is a write" in tools["launch_blueprint"].description
