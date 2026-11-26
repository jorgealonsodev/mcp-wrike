"""Users and hierarchy tools: get_me, search_users, list_spaces, get_item_children (PRD F3)."""

import httpx
from conftest import call, params

ME = {
    "id": "KUAQNMYS", "firstName": "Jorge", "lastName": "Alonso", "type": "Person",
    "profiles": [{"email": "jorge@example.com", "role": "User", "admin": True}],
}
CONTACTS = [
    ME,
    {"id": "KUAAAAA1", "firstName": "Laura", "lastName": "Gómez", "type": "Person",
     "profiles": [{"email": "lgomez@example.com", "role": "User"}]},
    {"id": "KUAAAAA2", "firstName": "Láura", "lastName": "Old", "type": "Person",
     "deleted": True, "profiles": [{"email": "old@example.com"}]},
    {"id": "KXGROUP1", "firstName": "Design", "lastName": "", "type": "Group", "profiles": []},
]


def _contacts(request):
    return [ME] if params(request).get("me") is True else CONTACTS


async def test_get_me(api):
    api.on("GET", "/contacts", _contacts)
    text = await call("get_me")
    assert text == (
        "**Jorge Alonso**\n- ID: `KUAQNMYS`\n- Email: jorge@example.com\n- Role: User (admin)"
    )


async def test_search_users_partial_name_excludes_deleted(api):
    api.on("GET", "/contacts", _contacts)
    text = await call("search_users", query="laur")
    assert "Found 1 users" in text
    assert "**Laura Gómez** (Person)" in text and "`KUAAAAA1`" in text
    assert "lgomez@example.com" in text
    assert "KUAAAAA2" not in text


async def test_search_users_is_accent_insensitive_and_can_include_deleted(api):
    api.on("GET", "/contacts", _contacts)
    text = await call("search_users", query="LAURA", include_deleted=True)
    assert "KUAAAAA1" in text and "KUAAAAA2" in text and "Deleted: yes" in text


async def test_search_users_matches_email_and_groups(api):
    api.on("GET", "/contacts", _contacts)
    assert "KUAAAAA1" in await call("search_users", query="lgomez@")
    assert "**Design** (Group)" in await call("search_users", query="design")


async def test_search_users_no_match(api):
    api.on("GET", "/contacts", _contacts)
    assert await call("search_users", query="zzz") == "No users match 'zzz'."


async def test_contacts_are_cached_per_client(api):
    api.on("GET", "/contacts", _contacts)
    async with api.client() as client:
        await client.list_contacts()
        await client.list_contacts()
        await client.list_contacts(refresh=True)
    assert len(api.calls("GET", "/api/v4/contacts")) == 2


async def test_me_resolves_as_a_user_id(api):
    api.on("GET", "/contacts", _contacts)
    async with api.client() as client:
        assert await client.resolve_id("me", "user") == "KUAQNMYS"
        assert await client.resolve_id("me", "user") == "KUAQNMYS"
    assert len(api.calls("GET", "/api/v4/contacts")) == 1


async def test_list_spaces(api):
    api.on("GET", "/spaces", [
        {"id": "IEABSPACE1", "title": "Clientes", "accessType": "Public"},
        {"id": "IEABSPACE2", "title": "Personal", "accessType": "Personal"},
    ])
    text = await call("list_spaces")
    assert "Found 2 spaces" in text
    assert "- **Clientes** — ID: `IEABSPACE1` | access: Public" in text


SUBFOLDERS = ["Tareas", "Proyectos", "Incidencias", "Propuestas", "Reuniones"]


def _r_andrade(api):
    api.on("GET", "/ids", lambda r: [{"id": "IEABRAND", "apiV2Id": "134666279"}]
           if params(r)["type"] == "ApiV2Folder" else httpx.Response(400, json={}))
    api.on("GET", "/folders/IEABRAND", [{
        "id": "IEABRAND", "title": "R Andrade", "scope": "WsFolder",
        "childIds": [f"IEABC{i}" for i in range(5)] + ["IEABRB"],
    }])
    folders = [{"id": f"IEABC{i}", "title": t, "scope": "WsFolder",
                "permalink": f"https://www.wrike.com/open.htm?id={i}"}
               for i, t in enumerate(SUBFOLDERS)]
    folders.append({"id": "IEABRB", "title": "Deleted", "scope": "RbFolder"})
    api.on("GET", "/folders/" + ",".join(f["id"] for f in folders), folders)
    api.on("GET", "/folders/IEABRAND/tasks", [
        {"id": "T1", "title": "Factura", "status": "Active", "parentIds": ["IEABRAND"]},
        {"id": "T2", "title": "Sub de factura", "status": "Active",
         "parentIds": ["IEABRAND"], "superTaskIds": ["T1"]},
        {"id": "T3", "title": "In a child folder", "status": "Active", "parentIds": ["IEABC0"]},
    ])


async def test_item_children_lists_subfolders_and_direct_tasks_in_one_call(api):
    _r_andrade(api)
    text = await call("get_item_children", item_id="https://www.wrike.com/open.htm?id=134666279")
    assert text.startswith("**R Andrade** (Folder) — `IEABRAND`")
    for title in SUBFOLDERS:
        assert f"- **{title}** (Folder)" in text
    assert "- **Factura** (Task | Active) — `T1`" in text
    assert "Deleted" not in text
    assert "Sub de factura" not in text and "In a child folder" not in text
    task_query = params(api.calls("GET", "/api/v4/folders/IEABRAND/tasks")[0])
    assert task_query["descendants"] is False


async def test_item_children_can_skip_tasks(api):
    _r_andrade(api)
    text = await call("get_item_children", item_id="IEABRAND", include_tasks=False)
    assert "Factura" not in text
    assert api.calls("GET", "/api/v4/folders/IEABRAND/tasks") == []


async def test_item_children_respects_limit(api):
    _r_andrade(api)
    text = await call("get_item_children", item_id="IEABRAND", limit=2)
    assert "Tareas" in text and "Proyectos" in text and "Incidencias" not in text
    assert "Showing the first 2 items" in text


async def test_item_children_of_a_task_lists_subtasks(api):
    api.on("GET", "/tasks/MTASK", [{"id": "MTASK", "title": "Parent", "status": "Active",
                                    "subTaskIds": ["S1", "S2"]}])
    api.on("GET", "/tasks/S1,S2", [
        {"id": "S1", "title": "One", "status": "Completed"},
        {"id": "S2", "title": "Two", "status": "Active", "permalink": "https://w/2"},
    ])
    text = await call("get_item_children", item_id="MTASK")
    assert text.startswith("**Parent** (Task) — `MTASK`")
    assert "- **One** (Task | Completed) — `S1`" in text
    assert "- **Two** (Task | Active) — `S2` | https://w/2" in text


async def test_item_children_unknown_item(api):
    text = await call("get_item_children", item_id="NOPE")
    assert text == "Error: 'NOPE' is not a space, folder, project or task you can access"
