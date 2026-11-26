"""Shared fakes for tool handler tests."""

import json
from types import SimpleNamespace

import httpx
import pytest

from mcp_wrike import server
from mcp_wrike.client import WrikeClient

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

    async def resolve_id(self, raw, kind):
        return raw

    async def resolve_ids(self, raws, kind):
        return raws

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


class FakeApi:
    """Fake Wrike REST API served through httpx.MockTransport.

    Register responses with `on(method, path, data)`; `data` is the list
    returned under "data", or a callable taking the request. Every request
    is recorded in `requests`.
    """

    def __init__(self):
        self.routes = {}
        self.requests: list[httpx.Request] = []

    def on(self, method, path, data):
        self.routes[(method, path)] = data

    def calls(self, method, path):
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v4")
        route = self.routes.get((request.method, path))
        if route is None:
            return httpx.Response(400, json={"errorDescription": f"no route {path}"})
        data = route(request) if callable(route) else route
        if isinstance(data, httpx.Response):
            return data
        return httpx.Response(200, json={"kind": "x", "data": data})

    def client(self) -> WrikeClient:
        api = self

        class _Client(WrikeClient):
            async def __aenter__(self):
                self._client = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
                return self

        return _Client("token")


def params(request: httpx.Request) -> dict:
    """Query parameters of a request, with JSON values decoded."""
    out = {}
    for k, v in request.url.params.items():
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


@pytest.fixture
def api(monkeypatch):
    fake_api = FakeApi()
    fake_api.on("GET", "/workflows", [])
    fake_api.on("GET", "/spaces", [])
    monkeypatch.setattr(server, "get_access_token", lambda: "token")
    monkeypatch.setattr(server, "WrikeClient", lambda token: fake_api.client())
    server._user_cache.clear()
    return fake_api


async def call(name, **arguments) -> str:
    """Call a tool and return its text output."""
    result = await server.call_tool(name, arguments)
    return result[0].text
