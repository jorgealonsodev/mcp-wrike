"""set_description_checkbox toggles a Wrike description checkbox by its label."""

from conftest import HTML

from mcp_wrike import server

CHECKS = (
    "<p><b>Todo</b></p>"
    '<p><input type="checkbox" /> Review header</p>'
    '<p><input type="checkbox" checked="checked" /> Update <a href="https://x.io">docs</a></p>'
    "<p><input type='checkbox'> Review form</p>"
    '<p><input type="checkbox" /> Review form layout</p>'
)


async def _call(fake, **args):
    fake.html = CHECKS
    result = await server.call_tool("set_description_checkbox", {"task_id": "T1", **args})
    return result[0].text


async def test_checks_an_unchecked_box(fake):
    text = await _call(fake, label="Review header")
    expected = CHECKS.replace(
        '<input type="checkbox" /> Review header',
        '<input type="checkbox" checked="checked" /> Review header',
    )
    assert fake.updates == [("T1", expected)]
    assert expected in text


async def test_unchecks_a_checked_box_with_formatted_label(fake):
    await _call(fake, label="Update docs", checked=False)
    assert fake.updates[0][1] == CHECKS.replace(
        '<input type="checkbox" checked="checked" /> Update',
        '<input type="checkbox" /> Update',
    )


async def test_label_must_match_whole_item_not_prefix(fake):
    await _call(fake, label="Review form")
    assert fake.updates[0][1] == CHECKS.replace(
        "<input type='checkbox'> Review form",
        "<input type='checkbox' checked=\"checked\"> Review form",
    )


async def test_already_in_state_does_not_update(fake):
    text = await _call(fake, label="Update docs", checked=True)
    assert fake.updates == []
    assert "already checked" in text


async def test_unknown_label_lists_available_checkboxes(fake):
    text = await _call(fake, label="Missing")
    assert fake.updates == []
    assert text.startswith("Error:")
    assert "Review header" in text and "Update docs" in text


async def test_duplicate_label_does_not_update(fake):
    fake_html = CHECKS + '<p><input type="checkbox" /> Review header</p>'
    fake.html = fake_html
    result = await server.call_tool(
        "set_description_checkbox", {"task_id": "T1", "label": "Review header"}
    )
    assert fake.updates == []
    assert "2 checkboxes" in result[0].text


async def test_description_without_checkboxes(fake):
    fake.html = HTML
    result = await server.call_tool(
        "set_description_checkbox", {"task_id": "T1", "label": "x"}
    )
    assert fake.updates == []
    assert "no checkboxes" in result[0].text


def test_tool_is_listed_and_write_tools_document_checkbox_markup():
    import asyncio

    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert tools["set_description_checkbox"].inputSchema["required"] == ["task_id", "label"]
    for name in ("create_task", "update_task", "edit_task_description"):
        assert '<input type="checkbox" />' in str(tools[name].inputSchema), name
