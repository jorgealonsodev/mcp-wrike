# PRD: Feature parity with the official Wrike MCP

- **Status:** Draft
- **Date:** 2026-11-26
- **Owner:** Jorge Alonso
- **Scope:** `src/mcp_wrike/client.py`, `src/mcp_wrike/server.py`, `tests/`, `README.md`

## 1. Context

`mcp-wrike` is a local stdio MCP server built on the Wrike REST API v4. It is stronger than the official hosted Wrike MCP (`https://mcp.wrike.com/v2`) for daily work. It can log time (`create_timelog`, `get_task_timelogs`, `get_timelog_report`), edit description fragments, toggle checkboxes, delete items, and keep the token in the system keychain.

The official MCP is better at finding and navigating work. A side-by-side review on 2026-11-26 found these gaps:

1. **Permalink IDs are rejected.** Every tool needs the API ID (`MAAAAAETC8Ae`). The ID users actually have is the one in the link, `open.htm?id=4614504478`. Passing the numeric ID to `get_task_timelogs` returned `400 Bad Request`. Finding the API ID took several calls.
2. **Search is limited.** `search_tasks` filters only by title, status, folder, created date and updated date. It cannot answer "my overdue tasks" or "tasks assigned to X".
3. **There is no user lookup or hierarchy navigation.** Users can only be found through `discover_account`. There is no "who am I", no space listing, and no way to see the direct children of an item.
4. **Approvals, blueprints, inbox and item history are missing.**
5. **Timelog comments come back HTML-escaped.** A comment sent as `Redacción aviso Navidad + HTML` was returned as `Redacción aviso Navidad &#43; HTML`.

## 2. Goals

- **G1.** Accept a permalink, a numeric permalink ID or an API ID wherever a task, folder, project or user ID is expected.
- **G2.** Let an agent answer "my overdue tasks", "tasks X completed last week" and "tasks where custom field Y = Z" in one call.
- **G3.** Let an agent resolve "me" and a person's name to a user ID, and walk spaces, folders and projects level by level.
- **G4.** Cover the approvals, blueprints, inbox and history features of the official MCP, where the public REST API v4 supports them.
- **G5.** Show timelog comments exactly as the user typed them.

## 3. Non-goals

- Replacing the official MCP's hosted OAuth flow. Authentication stays a permanent token in the keychain or `WRIKE_ACCESS_TOKEN`.
- Changing the names, parameters or output of existing tools. This work only adds tools or adds optional parameters.
- Features the REST API v4 does not expose. Those are recorded in §8 and dropped.
- Whiteboards and Klaxoon integration.

## 4. Users and use cases

The user is an agent (Claude Code) working for a Wrike user who manages client work and logs billable time.

| ID | Use case | Today | After |
|---|---|---|---|
| UC1 | "Log 30 min on https://www.wrike.com/open.htm?id=4614504478" | Search by title or date first, then log | One `create_timelog` call with the link |
| UC2 | "Which of my tasks are overdue?" | Not possible | `search_tasks(assignees=["me"], status="Active", due_end=today)` |
| UC3 | "Assign this to Laura" | Read `discover_account` output by hand | `search_users(query="Laura")`, then `update_task` |
| UC4 | "What's inside R Andrade?" | `list_folders`, then filter by hand | `get_item_children(item_id=<link>)` |
| UC5 | "Request approval from X for this task" | Not possible | `create_approval` |
| UC6 | "Create the project from the onboarding blueprint" | Not possible | `launch_blueprint` |

## 5. Requirements

Priorities: **P0** is the first release, **P1** is next, **P2** depends on API support (see §8).

### F1. Permalink IDs and timelog fix (P0)

**F1.1 ID resolution helper.** Add `WrikeClient.resolve_id(raw: str, kind: Literal["task", "folder", "user"]) -> str`.

It accepts:
- An API ID (for example `MAAAAAETC8Ae`, `IEABFIRDI4EANWBJ`, `KUAQNMYS`). It is returned unchanged.
- A numeric permalink ID (for example `4614504478`).
- A full permalink (`https://www.wrike.com/open.htm?id=4614504478`).
- The official MCP form (`w:itm:4614504478`).

Numeric IDs are converted with `GET /ids?ids=[N]&type=ApiV2Task|ApiV2Folder|ApiV2User`. Folders and projects use `ApiV2Folder`, and spaces are folders in v4.

Results are cached for the lifetime of the client. An unknown ID raises a clear error, for example `Could not resolve Wrike ID '4614504478' as task`, not a bare HTTP 400.

**F1.2 Apply the helper everywhere.** Every existing tool argument that takes an ID goes through `resolve_id` before the API call: `task_id`, `folder_id`, `parent_id`, `project_id`, `assignees`, `add_parents`, `remove_parents`, and so on. Each tool's input schema description states that links and numeric IDs are accepted.

**F1.3 Timelog comment encoding.** Return `comment` values HTML-unescaped (`html.unescape`) in `_parse_timelog`, and use them that way in every tool that prints timelogs.

Before changing what is sent, open the timelog in the Wrike UI. If it shows a literal `&#43;`, the escaping happens on write, and the fix is to change how the request body is sent.

Acceptance:
- `create_timelog(task_id="https://www.wrike.com/open.htm?id=4614504478", hours=0.5, ...)` succeeds and prints the API ID.
- `get_task("4614504478")` and `get_task("MAAAAAETC8Ae")` return the same task.
- A comment containing `+`, `&`, `<` and `ñ` comes back exactly as typed.
- Existing tests still pass.

### F2. Advanced task search (P0)

Extend `search_tasks` with these optional parameters, all mapped to `GET /tasks` query parameters:

| Parameter | Type | Wrike API mapping |
|---|---|---|
| `assignees` | list[str] (IDs, links or `"me"`) | `responsibles=[...]` |
| `authors` | list[str] (IDs, links or `"me"`) | `authors=[...]` |
| `due_start`, `due_end` | YYYY-MM-DD | `dueDate={"start":..,"end":..}` |
| `completed_start`, `completed_end` | YYYY-MM-DD | `completedDate={...}` |
| `start_start`, `start_end` | YYYY-MM-DD | `startDate={...}` |
| `custom_statuses` | list[str] | `customStatuses=[...]` |
| `custom_fields` | list[{id, comparator, value}] | `customFields=[...]` |
| `importance` | High, Normal, Low | `importance` |
| `sort_field` | add `DueDate` and `CompletedDate` | `sortField` |

Rules:
- `"me"` resolves through `GET /contacts?me=true`. The value is cached.
- The tool description gives the recipe for overdue tasks: `status="Active"` plus `due_end=<today>`.
- Invalid dates or comparators fail before the API call and name the bad field.

Acceptance:
- UC2 returns only active tasks that are assigned to the current user and due before today.
- Filters combine with AND.
- Existing callers that pass none of the new parameters get identical results.

### F3. Users and hierarchy (P0)

| Tool | Behaviour | API |
|---|---|---|
| `get_me` | The current user: ID, name, email, role. | `GET /contacts?me=true` |
| `search_users` | Case-insensitive match on first name, last name or email. Excludes deleted users unless `include_deleted=true`. Returns ID, name, email and type (person or group). | `GET /contacts`, filtered on the client, cached |
| `list_spaces` | Spaces the user can access, with ID, title and access type. | `GET /spaces` |
| `get_item_children` | Direct children of a space, folder, project or task: ID, title, type, status for tasks, and permalink. Accepts any ID form from F1. Has `limit` (default 50) and `include_tasks` (default true). | `GET /folders/{id}` (`childIds`), `GET /folders/{id}/folders?descendants=false`, `GET /folders/{id}/tasks?descendants=false`, `GET /tasks/{id}` (`subTaskIds`) |

Acceptance:
- UC3 finds a user by a partial name.
- UC4 lists `Tareas`, `Proyectos`, `Incidencias`, `Propuestas`, `Reuniones` and the direct tasks of `R Andrade` (`open.htm?id=134666279`) in a single call.

### F4. Approvals, blueprints, inbox, history (P1/P2)

| Tool | Priority | API (verify in §8) |
|---|---|---|
| `get_approvals` (by task, folder, or all with status filter) | P1 | `GET /approvals`, `GET /tasks/{id}/approvals`, `GET /folders/{id}/approvals` |
| `create_approval` (approvers, due date, description) | P1 | `POST /tasks/{id}/approvals`, `POST /folders/{id}/approvals` |
| `update_approval` (approvers, due date, description, cancel) | P1 | `PUT /approvals/{id}` |
| `set_approval_decision` (approve or reject with comment, as the current user) | P1 | `PUT /approvals/{id}` with a decision payload |
| `list_blueprints` (folder and task blueprints) | P1 | `GET /folder_blueprints`, `GET /task_blueprints` |
| `launch_blueprint` (into a parent, with title and optional date shift) | P1 | `POST /folder_blueprints/{id}/launch_async`, `POST /task_blueprints/{id}/launch_async`, then poll `GET /async_job/{id}` |
| `get_inbox` (current user's notifications) | P2 | Not confirmed in REST v4 |
| `get_item_history` (field changes of a task) | P2 | Not confirmed in REST v4; the audit log may be Enterprise only |

Rules:
- `set_approval_decision` and `launch_blueprint` are writes. Their tool descriptions must say so plainly.
- `launch_blueprint` waits for the async job, up to a timeout (default 60 s). It returns the created item's ID and permalink, or the job ID if it is still running.

## 6. Technical design

- **Client layer (`client.py`).** One method per API call, plus the private helpers `resolve_id`, `_me_id` and `_contacts_cache`. Use the existing `_request`. For list parameters, JSON-encode the value into the query string the way Wrike expects (for example `responsibles=["KUAQNMYS"]`).
- **Server layer (`server.py`).** Add each new `Tool(...)` to `list_tools()` and an `elif` branch to `call_tool()`, following the existing pattern. Formatting helpers go next to `_format_task`.
- **ID resolution in one place.** Resolve IDs at the top of each `call_tool` branch, never inside the formatting code.
- **Size.** `server.py` is about 2,100 lines. Splitting tools into modules is optional and out of scope, unless a slice exceeds about 400 changed lines and splitting makes review easier.
- **Dependencies.** No new runtime dependencies. `html` comes from the standard library.

## 7. Testing

- **Unit tests** in `tests/`, following the patterns in `conftest.py`. Mock HTTP, with no live calls in CI.
  - `resolve_id`: every input form, the cache, and the unknown-ID error.
  - `search_tasks` query building: each new filter, the `"me"` expansion, and a regression test for the old parameters.
  - `search_users` filtering. `get_item_children` merging folders, tasks and subtasks.
  - The timelog comment round trip with `+ & < ñ`.
  - Approvals and blueprint payloads, and async job polling with a fake job.
- **Manual smoke test** against the real account, read-only except one timelog on a test task. Use the use cases in §4 as the checklist.
- **README.** Document each new tool and parameter. Fix the README's tool list, which is missing `create_timelog`, `get_task_timelogs`, `get_timelog_report`, `create_comment`, `create_project`, `get_project`, `update_project`, `list_request_forms` and `submit_request_form`.

## 8. Open questions and risks

1. **Inbox and history.** Confirm whether REST API v4 exposes the user inbox and the field history of an item. If it does not, drop `get_inbox` and `get_item_history`, and record that in the README.
2. **Approval decision payload.** Confirm the exact `PUT /approvals/{id}` body for approving or rejecting as the current user.
3. **Where the `&#43;` comes from.** Confirm whether it happens on write or read (see F1.3).
4. **Contacts volume.** `GET /contacts` returns the whole account. Cache it per client and refresh on demand.
5. **Rate limits.** `resolve_id` adds one call per new numeric ID. The cache keeps it to one per ID per session.
6. **Destructive tools.** This PRD does not change `delete_task`, `delete_folder` or `delete_space`. Asking for confirmation before deleting stays with the caller.

## 9. Delivery plan

Each slice is a separate commit with its tests and README changes.

| Slice | Content | Priority |
|---|---|---|
| S1 | F1: `resolve_id`, applied to all tools, plus the timelog comment fix | P0 |
| S2 | F3: `get_me`, `search_users`, `list_spaces`, `get_item_children` | P0 |
| S3 | F2: advanced `search_tasks` (uses `"me"` from S2) | P0 |
| S4 | F4: approvals | P1 |
| S5 | F4: blueprints with async launch | P1 |
| S6 | F4: inbox and history, if §8.1 confirms API support | P2 |
| S7 | README tool list and docs pass | P0 |
