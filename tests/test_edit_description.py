"""edit_task_description replaces text inside the stored HTML, keeping all markup."""

from conftest import HTML

from mcp_wrike import server


async def _call(**args):
    result = await server.call_tool("edit_task_description", {"task_id": "T1", **args})
    return result[0].text


async def test_replaces_text_and_keeps_markup(fake):
    text = await _call(old_text="pending", new_text="done")
    expected = HTML.replace("pending", "done")
    assert fake.updates == [("T1", expected)]
    assert expected in text


async def test_replaces_text_inside_nested_tags(fake):
    await _call(old_text="Item 2", new_text="Second item")
    assert fake.updates[0][1] == HTML.replace("Item 2", "Second item")


async def test_not_found_does_not_update(fake):
    text = await _call(old_text="missing", new_text="x")
    assert fake.updates == []
    assert text.startswith("Error:") and "not found" in text


async def test_ambiguous_match_does_not_update(fake):
    text = await _call(old_text="Item", new_text="Entry")
    assert fake.updates == []
    assert text.startswith("Error:") and "2 times" in text


async def test_replace_all_replaces_every_match(fake):
    await _call(old_text="Item", new_text="Entry", replace_all=True)
    assert fake.updates[0][1] == HTML.replace("Item", "Entry")


def test_tool_is_listed():
    import asyncio

    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == "edit_task_description")
    assert tool.inputSchema["required"] == ["task_id", "old_text", "new_text"]
