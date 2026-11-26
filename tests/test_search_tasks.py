"""Advanced search_tasks filters (PRD F2)."""

import pytest
from conftest import call, params

ME = {"id": "KUAQNMYS", "firstName": "Jorge", "lastName": "Alonso", "profiles": []}
TASK = {"id": "T1", "title": "Overdue", "status": "Active", "dates": {"due": "2026-11-20"}}
FIELDS = (
    '["parentIds","responsibleIds","customFields","superTaskIds","description",'
    '"briefDescription","subTaskIds","hasAttachments"]'
)


def _setup(api):
    api.on("GET", "/contacts", [ME])
    api.on("GET", "/tasks", [TASK])
    api.on("GET", "/folders/IEAFOLDER/tasks", [TASK])


def _query(api, path="/api/v4/tasks"):
    return params(api.calls("GET", path)[0])


async def test_my_overdue_tasks_in_one_call(api):
    _setup(api)
    text = await call(
        "search_tasks", assignees=["me"], status="Active", due_end="2026-11-26"
    )
    assert "Found 1 tasks" in text
    q = _query(api)
    assert q["responsibles"] == ["KUAQNMYS"]
    assert q["status"] == "Active"
    assert q["dueDate"] == {"end": "2026-11-26"}
    assert len(api.calls("GET", "/api/v4/tasks")) == 1


async def test_every_new_filter_is_sent_and_combined(api):
    _setup(api)
    await call(
        "search_tasks",
        folder_id="IEAFOLDER",
        assignees=["KUAAAAA1"],
        authors=["me"],
        due_start="2026-11-01", due_end="2026-11-30",
        completed_start="2026-11-16", completed_end="2026-11-22",
        start_start="2026-10-01", start_end="2026-10-31",
        custom_statuses=["IEACSTAT1"],
        custom_fields=[
            {"id": "IEACF1", "value": "Acme"},
            {"id": "IEACF2", "comparator": "IsEmpty"},
            {"id": "IEACF3", "comparator": "InRange", "min_value": "1", "max_value": "5"},
            {"id": "IEACF4", "comparator": "ContainsAny", "value": ["a", "b"]},
        ],
        importance="High",
        sort_field="DueDate",
    )
    q = _query(api, "/api/v4/folders/IEAFOLDER/tasks")
    assert q["responsibles"] == ["KUAAAAA1"]
    assert q["authors"] == ["KUAQNMYS"]
    assert q["dueDate"] == {"start": "2026-11-01", "end": "2026-11-30"}
    assert q["completedDate"] == {
        "start": "2026-11-16T00:00:00Z", "end": "2026-11-22T23:59:59Z"
    }
    assert q["startDate"] == {"start": "2026-10-01", "end": "2026-10-31"}
    assert q["customStatuses"] == ["IEACSTAT1"]
    assert q["customFields"] == [
        {"id": "IEACF1", "comparator": "EqualTo", "value": "Acme"},
        {"id": "IEACF2", "comparator": "IsEmpty"},
        {"id": "IEACF3", "comparator": "InRange", "minValue": "1", "maxValue": "5"},
        {"id": "IEACF4", "comparator": "ContainsAny", "values": ["a", "b"]},
    ]
    assert q["importance"] == "High"
    assert q["sortField"] == "DueDate"


async def test_old_parameters_build_the_same_query_as_before(api):
    _setup(api)
    await call(
        "search_tasks", title="Aviso", status="Completed", limit=10,
        created_start="2026-01-01", updated_end="2026-02-01",
        sort_field="CreatedDate", sort_order="Desc",
    )
    raw = dict(api.calls("GET", "/api/v4/tasks")[0].url.params)
    assert raw == {
        "pageSize": "10",
        "fields": FIELDS,
        "title": "Aviso",
        "status": "Completed",
        "createdDate": '{"start": "2026-01-01T00:00:00Z"}',
        "updatedDate": '{"end": "2026-02-01T23:59:59Z"}',
        "sortField": "CreatedDate",
        "sortOrder": "Desc",
    }


async def test_no_parameters_sends_only_paging_and_fields(api):
    _setup(api)
    await call("search_tasks")
    assert dict(api.calls("GET", "/api/v4/tasks")[0].url.params) == {
        "pageSize": "50", "fields": FIELDS,
    }


@pytest.mark.parametrize(
    "args,message",
    [
        ({"due_end": "26/11/2026"}, "due_end must be a date in YYYY-MM-DD format"),
        ({"completed_start": "2026-02-30"}, "completed_start must be a date"),
        ({"start_start": "2026-1-5"}, "start_start must be a date"),
        ({"custom_fields": [{"id": "X", "comparator": "Like", "value": "a"}]},
         "custom_fields[0].comparator 'Like' is not one of"),
        ({"custom_fields": [{"id": "X"}]}, "custom_fields[0].value is required for EqualTo"),
        ({"custom_fields": [{"id": "X", "comparator": "InRange", "min_value": "1"}]},
         "custom_fields[0] with InRange needs min_value and max_value"),
        ({"custom_fields": [{"comparator": "IsEmpty"}]}, "custom_fields[0].id is required"),
        ({"importance": "Urgent"}, "importance must be High, Normal or Low"),
    ],
)
async def test_invalid_filters_fail_before_the_api_call(api, args, message):
    _setup(api)
    text = await call("search_tasks", **args)
    assert text.startswith("Error: ") and message in text
    assert api.calls("GET", "/api/v4/tasks") == []


async def test_empty_result_names_the_new_filters(api):
    api.on("GET", "/tasks", [])
    text = await call("search_tasks", importance="Low")
    assert text == "No tasks found (importance='Low')."


async def test_empty_result_without_filters_is_unchanged(api):
    api.on("GET", "/tasks", [])
    assert await call("search_tasks") == "No tasks found (no filters)."


def test_description_gives_the_overdue_recipe():
    import asyncio

    from mcp_wrike import server

    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "search_tasks")
    assert "status='Active' plus due_end=<today>" in tool.description
    assert tool.inputSchema["properties"]["sort_field"]["enum"] == [
        "CreatedDate", "UpdatedDate", "DueDate", "CompletedDate",
    ]
