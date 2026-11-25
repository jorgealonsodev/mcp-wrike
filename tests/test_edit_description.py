"""edit_task_description replaces text inside the stored HTML, keeping all markup."""

from types import SimpleNamespace

import pytest

from mcp_wrike import server

HTML = '<p><b>Status</b>: pending</p><ul><li>Item 1</li><li>Item 2 <a href="https://x.io">link</a></li></ul>'


class FakeClient:
    def __init__(self, html):
        self.html = html
        self.updates = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_task(self, task_id):
        return SimpleNamespace(description=self.html)

    async def update_task(self, task_id, description=None, **_):
        self.updates.append((task_id, description))
        self.html = description
        return SimpleNamespace(
            title="T", id=task_id, status="Active", custom_status_name=None,
            importance=None, dates=None, created_date=None, updated_date=None,
            completed_date=None, parent_ids=[], super_task_ids=[],
            responsible_ids=[], custom_status_id=None, custom_fields=[],
            permalink=None, description=description,
        )


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient(HTML)
    monkeypatch.setattr(server, "get_access_token", lambda: "token")
    monkeypatch.setattr(server, "WrikeClient", lambda token: client)
    return client


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
