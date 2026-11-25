"""Shared fakes for tool handler tests."""

from types import SimpleNamespace

import pytest

from mcp_wrike import server

HTML = (
    '<p><b>Status</b>: pending</p><ul><li>Item 1</li>'
    '<li>Item 2 <a href="https://x.io">link</a></li></ul>'
)


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
