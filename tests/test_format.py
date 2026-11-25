"""Formatters must expose descriptions as raw HTML so agents can edit them in place."""

from types import SimpleNamespace

from mcp_wrike.server import _format_project, _format_task

HTML = (
    '<p><b>Review</b> &amp; "quotes"</p>'
    '<ul><li>Item 1</li><li>Item 2 <a href="https://example.com/?a=1&b=2">link</a></li></ul>'
)


def _task(description):
    return SimpleNamespace(
        title="T", id="ID1", status="Active", custom_status_name=None,
        importance=None, dates=None, created_date=None, updated_date=None,
        completed_date=None, parent_ids=[], super_task_ids=[],
        responsible_ids=[], custom_status_id=None, custom_fields=[],
        permalink=None, description=description,
    )


def _project(description):
    return SimpleNamespace(
        title="P", id="ID2", custom_status_name=None, owner_ids=[],
        created_date=None, updated_date=None, child_ids=[], permalink=None,
        description=description,
    )


def test_task_description_is_raw_html():
    out = _format_task(_task(HTML))
    assert f"**Description (HTML):**\n{HTML}" in out


def test_project_description_is_raw_html():
    out = _format_project(_project(HTML))
    assert f"**Description (HTML):**\n{HTML}" in out


def test_task_description_can_still_be_omitted():
    out = _format_task(_task(HTML), include_description=False)
    assert "Description" not in out


def test_empty_description_is_omitted():
    assert "Description" not in _format_task(_task("  "))
    assert "Description" not in _format_project(_project(""))
