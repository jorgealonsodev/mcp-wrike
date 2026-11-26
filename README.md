# mcp-wrike

MCP (Model Context Protocol) server for Wrike project management. Full CRUD support for tasks, folders, spaces, and attachments with account discovery for bootstrapping.

## Features

### Discovery and navigation
- **discover_account** — Bootstrap tool: identifies authenticated user, enumerates spaces, lists top-level folders with workflow assignments, catalogues account-level and space-scoped workflows, and optionally includes custom fields and item types
- **get_me** — The authenticated user: ID, name, email and role
- **search_users** — Find users and groups by part of a first name, last name or email (case- and accent-insensitive; deleted users only with `include_deleted=true`)
- **list_spaces** — Spaces you can access, with ID, title and access type
- **get_item_children** — Direct children of a space, folder, project or task: child folders and projects, then top-level tasks (or a task's subtasks), each with ID, type, status and link. Options: `include_tasks` (default true), `limit` (default 50)
- **list_folders** — Browse Wrike folders/projects

### Read
- **search_tasks** — Search tasks; see [Searching tasks](#searching-tasks)
- **get_task** — Get detailed task information
- **get_task_comments** — View comments/notes on a task
- **get_task_attachments** — List task attachments
- **get_task_full** — Complete view: task + comments + attachments
- **get_folder_tasks** — List tasks within a folder (supports `recursive=true` to include child folder/project tasks)
- **get_project** — Project details, including its description
- **get_workflows** — List all account-level workflows and their custom statuses
- **get_custom_fields** — List all custom field definitions
- **get_custom_item_types** — List all custom item type definitions
- **list_request_forms** — Request forms visible to the token, with field IDs, types and options

Task and project descriptions are returned as raw HTML, so agents can edit wording without losing bold, lists, links, or other formatting.

Checklist items in descriptions must be written as `<input type="checkbox" />` (add `checked="checked"` for a done item). Wrike renders them as native checkboxes; the editor's internal `data-checked` markup is stripped by the API.

### Write
- **create_task** — Create tasks with title, description, status, assignees, dates, importance, custom fields, and custom item types
- **update_task** — Update any task field including custom fields
- **edit_task_description** — Replace a fragment of a task description in place (exact match on the stored HTML), keeping all formatting intact
- **set_description_checkbox** — Check or uncheck a checklist item in a task description by its visible text
- **complete_task** — Mark a task as completed
- **move_task** — Move a task between folders (add/remove parents)
- **delete_task** — Delete a task permanently
- **create_folder** — Create subfolders
- **delete_folder** — Delete a folder permanently
- **delete_space** — Delete a space permanently
- **create_project** — Create a project inside a folder, with owners, status and custom fields
- **update_project** — Update a project's title, description, status or custom fields
- **create_comment** — Add a comment to a task (HTML allowed)
- **attach_file** — Upload local files as task attachments
- **submit_request_form** — Submit a request form and wait for Wrike's async job

### Time tracking
- **create_timelog** — Log hours against a task, with date, comment and category
- **get_task_timelogs** — Time entries logged on a task
- **get_timelog_report** — Hours by person and by task, optionally for one folder and a date range

Timelog comments are returned exactly as typed: Wrike sends them HTML-escaped (`+` as `&#43;`), and the server unescapes them.

### Approvals
- **get_approvals** — Approvals of a task (`task_id`), a folder or project (`folder_id`), or the whole account, with each approver's decision and comment. Filter with `statuses` (`Draft`, `NotRequired`, `Approved`, `Rejected`, `Cancelled`, `Pending`)
- **create_approval** — Request an approval on a task or folder, with `approvers`, `due_date` and `description`. Without approvers it is saved as a draft. This is a write: Wrike notifies the approvers
- **update_approval** — Add or remove approvers, change the due date or description, or cancel it with `cancel=true`

### Blueprints
- **list_blueprints** — Folder and task blueprints, optionally for one space (`space_id`) or by title (`title_contains`)
- **launch_blueprint** — Create items from a blueprint into `parent_id` (or, for task blueprints, as subtasks of `super_task_id`), with `title`, optional `title_prefix`, and a date shift (`reschedule_date`, `reschedule_mode` = `Start` or `End`). This is a write. It waits for Wrike's async job (`timeout_seconds`, default 60) and returns the new item's ID and link, or the job ID if it is still running

### Not available
These features of the official Wrike MCP are not in the public REST API v4, so this server does not offer them:

- **Approving or rejecting as the current user.** The approvals API can create, edit and cancel approvals, but has no input for a decision or decision comment.
- **Inbox / notifications.** No endpoint exists.
- **Item history.** The only change log is `GET /audit_log`, which is limited to Enterprise admins with the "Create user activity reports" right. `GET /tasks/{id}/tasks_history` covers budget figures only.

## Wrike IDs and links

Every argument that takes a task, folder, project, space or user ID also accepts:

- the numeric ID from a link, e.g. `4614504478`
- the full link, e.g. `https://www.wrike.com/open.htm?id=4614504478`
- the official MCP form, e.g. `w:itm:4614504478`

The server converts these with `GET /ids` and caches each conversion for the session. API IDs (e.g. `MAAAAAETC8Ae`) are used as they are. User arguments (`assignees`, `authors`, `approvers`, `add_responsibles`, ...) also accept `me`.

## Searching tasks

`search_tasks` filters combine with AND:

| Parameter | Meaning |
|-----------|---------|
| `title` | Title contains |
| `status` | `Active`, `Completed`, `Deferred` or `Cancelled` |
| `folder_id` | Only tasks in this folder or project |
| `assignees` | Assigned to any of these users (IDs, links or `me`) |
| `authors` | Created by any of these users (IDs, links or `me`) |
| `due_start`, `due_end` | Due date range (YYYY-MM-DD, inclusive) |
| `start_start`, `start_end` | Start date range |
| `completed_start`, `completed_end` | Completion date range |
| `created_start`, `created_end`, `updated_start`, `updated_end` | Created and updated date ranges |
| `custom_statuses` | Any of these custom status IDs (see `get_workflows`) |
| `custom_fields` | `[{id, comparator, value}]`; see below |
| `importance` | `High`, `Normal` or `Low` |
| `sort_field`, `sort_order` | `CreatedDate`, `UpdatedDate`, `DueDate` or `CompletedDate`; `Asc` or `Desc` |
| `limit` | Maximum results (default 50) |

`custom_fields` comparators: `EqualTo` (default), `IsEmpty`, `IsNotEmpty`, `LessThan`, `LessOrEqualTo`, `GreaterThan`, `GreaterOrEqualTo`, `Contains`, `StartsWith`, `EndsWith`, `InRange` and `NotInRange` (with `min_value` and `max_value`), `ContainsAll` and `ContainsAny` (with a list `value`). Bad dates, comparators or importance values are rejected before any request, with the field named in the error.

Examples:

- My overdue tasks: `{"assignees": ["me"], "status": "Active", "due_end": "<today>"}`
- What Laura completed last week: `{"assignees": ["<Laura's ID from search_users>"], "completed_start": "2026-11-16", "completed_end": "2026-11-22"}`
- Tasks where a field equals a value: `{"custom_fields": [{"id": "IEAB...", "value": "Acme"}]}`

## Installation

```bash
# Install from source
cd mcp-wrike
pip install -e .
```

## Configuration

### Get your Wrike Access Token

1. Go to https://www.wrike.com/frontend/apps/index.html#/api
2. Create a new "Permanent Access Token"
3. Copy the token

### Store the token

Option 1 — System keychain (recommended):
```bash
wrike-auth store
# Enter your token when prompted
```

Option 2 — Environment variable:
```bash
export WRIKE_ACCESS_TOKEN=your_token_here
```

### Check configuration
```bash
wrike-auth show
```

## Claude Code Integration

Add to your `.claude/settings.local.json` MCP configuration:

```json
{
  "mcpServers": {
    "wrike": {
      "command": "mcp-wrike"
    }
  }
}
```

Or in `claude_desktop_config.json` for Claude Desktop:

```json
{
  "mcpServers": {
    "wrike": {
      "command": "mcp-wrike"
    }
  }
}
```

## Account Discovery (Bootstrap)

The `discover_account` tool provides a comprehensive view of your Wrike account structure. Use it to identify folder IDs, workflow status IDs, custom fields, and item types needed for task management.

### What it discovers

- **Authenticated user** — name, email, contact ID
- **Spaces** — all spaces with access type and member count
- **Top-level folders per space** — filtered to direct children only (not the full recursive tree), with workflow assignments resolved by name
- **Space-scoped workflows** — workflows belonging to specific spaces (not returned by the account-level workflows endpoint)
- **Account-level workflows** — all 40+ shared workflows with custom status IDs
- **Custom fields** (optional) — field definitions with types and allowed values
- **Custom item types** (optional) — task type definitions (Project, Task, Bug, Spike, Epic, etc.)

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `space_id` | — | Filter to a single space by ID |
| `include_custom_fields` | false | Include custom field definitions |
| `include_item_types` | false | Include custom item type definitions |

### Workflow scoping

Wrike workflows can be scoped to either the account level or to specific spaces. The `discover_account` tool checks both:

- **Account-level**: `GET /workflows` — shared across all spaces
- **Space-scoped**: `GET /spaces/{id}/workflows` — belong to a specific space

This matters because a folder's `workflowId` may reference a space-scoped workflow that doesn't appear in the account-level listing.

## Custom Fields

`create_task` and `update_task` support Wrike custom fields via the `custom_fields` parameter:

```json
{
  "custom_fields": [
    {"id": "FIELD_ID", "value": "field value"}
  ]
}
```

Use `discover_account` with `include_custom_fields=true` to list all available field IDs, or query the Wrike API directly: `GET /customfields`.

## Custom Item Types

Tasks can be assigned a custom item type at creation via the `custom_item_type_id` parameter. Item types cannot be changed after creation.

Use `discover_account` with `include_item_types=true` to list available types.

## Usage Examples

Once configured, you can ask Claude:

- "Discover my Wrike account structure"
- "Search for active tasks about 'deployment'"
- "Create a task in folder X titled 'Fix the bug'"
- "Mark task IEAABCD123 as completed"
- "Move the task to the Engineering folder"
- "Attach this spec file to the Wrike task"
- "Show me all tasks in the Engineering folder"
- "Delete the empty test folder"
- "Log 30 min on https://www.wrike.com/open.htm?id=4614504478"
- "Which of my tasks are overdue?"
- "What's inside this folder?" (with a link)
- "Request approval from Laura for this task"
- "Create the project from the onboarding blueprint"

## License

MIT
