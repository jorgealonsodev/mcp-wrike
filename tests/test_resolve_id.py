"""Permalinks and numeric IDs are accepted wherever an ID is expected (PRD F1)."""

import asyncio

import httpx
import pytest
from conftest import call, params

from mcp_wrike import server

TASK = {"id": "MAAAAAETC8Ae", "title": "Aviso Navidad", "status": "Active"}


def _ids(api, mapping):
    """Serve GET /ids from {(numeric, type): api_id}."""

    def handle(request):
        p = params(request)
        api_id = mapping.get((str(p["ids"][0]), p["type"]))
        if api_id is None:
            return httpx.Response(400, json={"errorDescription": "Invalid ids"})
        return [{"id": api_id, "apiV2Id": str(p["ids"][0])}]

    api.on("GET", "/ids", handle)


@pytest.mark.parametrize(
    "raw",
    [
        "4614504478",
        "https://www.wrike.com/open.htm?id=4614504478",
        "w:itm:4614504478",
        "W:ITM:4614504478",
        " https://www.wrike.com/workspace.htm?acc=1#path=folder&id=4614504478 ",
    ],
)
async def test_numeric_forms_convert_to_api_id(api, raw):
    _ids(api, {("4614504478", "ApiV2Task"): "MAAAAAETC8Ae"})
    async with api.client() as client:
        assert await client.resolve_id(raw, "task") == "MAAAAAETC8Ae"


@pytest.mark.parametrize("raw", ["MAAAAAETC8Ae", "IEABFIRDI4EANWBJ", "KUAQNMYS"])
async def test_api_ids_are_returned_unchanged_without_a_call(api, raw):
    async with api.client() as client:
        assert await client.resolve_id(raw, "folder") == raw
    assert api.requests == []


@pytest.mark.parametrize(
    "kind,api_type", [("task", "ApiV2Task"), ("folder", "ApiV2Folder"), ("user", "ApiV2User")]
)
async def test_kind_selects_the_ids_type(api, kind, api_type):
    _ids(api, {("42", api_type): "X"})
    async with api.client() as client:
        assert await client.resolve_id("42", kind) == "X"


async def test_conversion_is_cached_per_client(api):
    _ids(api, {("4614504478", "ApiV2Task"): "MAAAAAETC8Ae"})
    async with api.client() as client:
        await client.resolve_id("4614504478", "task")
        await client.resolve_id("https://www.wrike.com/open.htm?id=4614504478", "task")
    assert len(api.calls("GET", "/api/v4/ids")) == 1


async def test_unknown_id_raises_a_clear_error(api):
    _ids(api, {})
    async with api.client() as client:
        with pytest.raises(ValueError, match="Could not resolve Wrike ID '4614504478' as task"):
            await client.resolve_id("4614504478", "task")


async def test_get_task_accepts_numeric_and_api_id(api):
    _ids(api, {("4614504478", "ApiV2Task"): "MAAAAAETC8Ae"})
    api.on("GET", "/tasks/MAAAAAETC8Ae", [TASK])
    by_numeric = await call("get_task", task_id="4614504478")
    by_api_id = await call("get_task", task_id="MAAAAAETC8Ae")
    assert by_numeric == by_api_id
    assert "Aviso Navidad" in by_numeric


async def test_create_timelog_accepts_a_link_and_prints_the_api_id(api):
    _ids(api, {("4614504478", "ApiV2Task"): "MAAAAAETC8Ae"})
    api.on("POST", "/tasks/MAAAAAETC8Ae/timelogs", [
        {"id": "TL1", "taskId": "MAAAAAETC8Ae", "hours": 0.5, "trackedDate": "2026-11-26"}
    ])
    text = await call(
        "create_timelog",
        task_id="https://www.wrike.com/open.htm?id=4614504478",
        hours=0.5,
        tracked_date="2026-11-26",
    )
    assert "`MAAAAAETC8Ae`" in text
    assert "Error" not in text


async def test_unknown_link_is_reported_without_calling_the_tool_endpoint(api):
    _ids(api, {})
    text = await call("get_task", task_id="https://www.wrike.com/open.htm?id=999")
    assert text == "Error: Could not resolve Wrike ID '999' as task"


async def test_list_arguments_are_resolved_by_kind(api):
    _ids(api, {("7", "ApiV2Task"): "T7", ("8", "ApiV2Folder"): "F8", ("9", "ApiV2User"): "U9"})
    async with api.client() as client:
        resolved = await server._resolve_id_args(client, {
            "task_id": "7",
            "add_parents": ["8", "IEAFOLDER"],
            "add_responsibles": ["9"],
            "title": "123",
        })
    assert resolved == {
        "task_id": "T7",
        "add_parents": ["F8", "IEAFOLDER"],
        "add_responsibles": ["U9"],
        "title": "123",
    }


def test_every_id_argument_documents_links_and_numeric_ids():
    tools = asyncio.run(server.list_tools())
    seen = 0
    for tool in tools:
        for arg, prop in tool.inputSchema.get("properties", {}).items():
            if arg in server.ID_ARGS:
                seen += 1
                assert "numeric ID" in prop["description"], (tool.name, arg)
                assert "link" in prop["description"], (tool.name, arg)
    assert seen > 30


async def test_blank_folder_id_does_not_widen_search(api):
    api.on("GET", "/tasks", [{"id": "A", "title": "x", "status": "Active"}])
    api.on("GET", "/folders/   /tasks", [])
    await call("search_tasks", folder_id="   ")
    assert api.calls("GET", "/api/v4/tasks") == []
