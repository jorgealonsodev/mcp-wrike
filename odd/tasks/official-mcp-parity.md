# Feature: official Wrike MCP parity
Objective: implement docs/PRD-official-mcp-parity.md. Branch `feat/official-mcp-parity`.
Delivery: ask-on-risk, one commit per PRD slice. Runner: `pytest` (venv outside the worktree).

## Specs

The PRD `docs/PRD-official-mcp-parity.md` is the full specification; each S# below points at its section and quotes the acceptance criteria verbatim.

- **S1** (PRD F1.1, F1.2): `WrikeClient.resolve_id(raw, kind)` accepts an API ID, a numeric permalink ID, a full permalink, and `w:itm:N`. Cached for the client's lifetime. Unknown ID error: "Could not resolve Wrike ID '4614504478' as task". Applied to every ID argument; schema descriptions say links and numeric IDs are accepted.
  - "`create_timelog(task_id="https://www.wrike.com/open.htm?id=4614504478", hours=0.5, ...)` succeeds and prints the API ID."
  - "`get_task("4614504478")` and `get_task("MAAAAAETC8Ae")` return the same task."
- **S2** (PRD F1.3): timelog comments are returned HTML-unescaped. "A comment containing `+`, `&`, `<` and `ñ` comes back exactly as typed."
- **S3** (PRD F3): `get_me`, `search_users`, `list_spaces`, `get_item_children`. "UC3 finds a user by a partial name." "UC4 lists `Tareas`, `Proyectos`, `Incidencias`, `Propuestas`, `Reuniones` and the direct tasks of `R Andrade` (`open.htm?id=134666279`) in a single call."
- **S4** (PRD F2): advanced `search_tasks` filters. "UC2 returns only active tasks that are assigned to the current user and due before today." "Filters combine with AND." "Existing callers that pass none of the new parameters get identical results." "Invalid dates or comparators fail before the API call and name the bad field."
- **S5** (PRD F4): approvals tools; `set_approval_decision` description states it is a write.
- **S6** (PRD F4): blueprints; `launch_blueprint` waits up to 60 s and returns the created item's ID and permalink, or the job ID.
- **S7** (PRD F4, §8.1): `get_inbox` / `get_item_history` only if REST v4 supports them; otherwise dropped and recorded in the README.
- **S8** (PRD §7): README documents new tools and fixes the missing tool list.

## Tasks

| ID | Specs | Route | Status | Commit |
|---|---|---|---|---|
| T1 | S1, S2 | inline | in progress | |
| T2 | S3 | inline | pending | |
| T3 | S4 | inline | pending | |
| T4 | S5 | inline | pending | |
| T5 | S6 | inline | pending | |
| T6 | S7 | inline | pending | |
| T7 | S8 | inline | pending | |

Route evidence: all slices edit `client.py` and `server.py`, so they cannot run as parallel writers; no writer trigger applies.

## Log

- **L1** (2026-11-26, user): "/mnt/480GB/Proyectos/Trabajo/Enfoquestic/20261126_mcp-wrike/20261126_mcp-wrike_git/mcp-wrike/docs/PRD-official-mcp-parity.md implementa esto"
- L2: Baseline: 18 tests pass. A read-only research worker is checking the PRD §8 API questions against the Wrike v4 docs.
