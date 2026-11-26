"""Timelog comments come back exactly as typed (PRD F1.3)."""

from conftest import call

TYPED = "Redacción aviso Navidad + HTML & <b> ñ"
ESCAPED = "Redacción aviso Navidad &#43; HTML &amp; &lt;b&gt; ñ"


def _timelog(comment):
    return {
        "id": "TL1", "taskId": "T1", "userId": "U1", "hours": 0.5,
        "trackedDate": "2026-11-26", "comment": comment,
    }


async def test_created_timelog_comment_is_unescaped(api):
    api.on("POST", "/tasks/T1/timelogs", [_timelog(ESCAPED)])
    text = await call("create_timelog", task_id="T1", hours=0.5,
                      tracked_date="2026-11-26", comment=TYPED)
    assert f"- Comment: {TYPED}\n" in text
    sent = api.calls("POST", "/api/v4/tasks/T1/timelogs")[0]
    assert TYPED in sent.content.decode()


async def test_task_timelogs_comment_is_unescaped(api):
    api.on("GET", "/tasks/T1/timelogs", [_timelog(ESCAPED)])
    api.on("GET", "/contacts", [])
    text = await call("get_task_timelogs", task_id="T1")
    assert text.endswith(f" — {TYPED}")


async def test_plain_comment_is_unchanged(api):
    api.on("GET", "/tasks/T1/timelogs", [_timelog("Plain"), _timelog(None)])
    api.on("GET", "/contacts", [])
    text = await call("get_task_timelogs", task_id="T1")
    assert " — Plain" in text
