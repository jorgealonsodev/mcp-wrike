"""MCP server for Wrike project management."""

import asyncio
import html
import re
import unicodedata

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth import get_access_token
from .client import WrikeClient

# Wrike renders <input type="checkbox" /> in descriptions as a native checklist
# item; the editor's own data-checked markup is stripped by the API.
CHECKBOX_MARKUP = '<input type="checkbox" />'
CHECKBOX_HINT = (
    f" For a checklist item use {CHECKBOX_MARKUP} before the text"
    ' (add checked="checked" to mark it done).'
)
_CHECKBOX_RE = re.compile(
    r"<input\b[^>]*\btype\s*=\s*[\"']?checkbox[\"']?[^>]*>", re.IGNORECASE
)
_LABEL_END_RE = re.compile(r"<input\b|</p>|</li>|</div>|<br\b", re.IGNORECASE)
_CHECKED_ATTR_RE = re.compile(
    r"\s+checked(\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>/]+))?", re.IGNORECASE
)


def _find_checkboxes(description: str) -> list[tuple[re.Match, str, bool]]:
    """Return (tag match, plain-text label, checked) for each checkbox."""
    boxes = []
    for match in _CHECKBOX_RE.finditer(description):
        rest = description[match.end():]
        end = _LABEL_END_RE.search(rest)
        raw = rest[: end.start()] if end else rest
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", "", raw)).split())
        checked = bool(_CHECKED_ATTR_RE.search(match.group(0)))
        boxes.append((match, label, checked))
    return boxes


def _set_checked(tag: str, checked: bool) -> str:
    """Return the checkbox tag with its checked attribute set or removed."""
    tag = _CHECKED_ATTR_RE.sub("", tag)
    if not checked:
        return tag
    if tag.endswith("/>"):
        return tag[:-2].rstrip() + ' checked="checked" />'
    return tag[:-1].rstrip() + ' checked="checked">'


# Tool arguments that carry Wrike IDs, by argument name, and the kind used to
# convert permalinks and numeric IDs. Names are shared across tools on purpose.
ID_ARGS: dict[str, str] = {
    "task_id": "task",
    "add_super_tasks": "task",
    "remove_super_tasks": "task",
    "folder_id": "folder",
    "parent_folder_id": "folder",
    "project_id": "folder",
    "space_id": "folder",
    "add_parents": "folder",
    "remove_parents": "folder",
    "responsible_ids": "user",
    "add_responsibles": "user",
    "remove_responsibles": "user",
    "owner_ids": "user",
}
ID_HINT = (
    " Accepts the API ID, the numeric ID from the item's link,"
    " or the full link (https://www.wrike.com/open.htm?id=...)."
)


async def _resolve_id_args(client: WrikeClient, arguments: dict) -> dict:
    """Return a copy of the arguments with every ID argument converted to an API ID."""
    resolved = dict(arguments)
    for arg, kind in ID_ARGS.items():
        value = resolved.get(arg)
        if isinstance(value, str) and value:
            resolved[arg] = await client.resolve_id(value, kind)
        elif isinstance(value, list):
            resolved[arg] = await client.resolve_ids(value, kind)
    return resolved


def _add_id_hints(tools: list[Tool]) -> list[Tool]:
    """Append ID_HINT to the description of every ID argument."""
    for tool in tools:
        for arg, prop in tool.inputSchema.get("properties", {}).items():
            if arg in ID_ARGS and ID_HINT not in prop.get("description", ""):
                prop["description"] = prop.get("description", "") + ID_HINT
    return tools


# Initialize MCP server
server = Server("mcp-wrike")

# Cache for user lookup (id -> name)
_user_cache: dict[str, str] = {}


async def _get_user_name(client: WrikeClient, user_id: str) -> str:
    """Get user display name from ID, with caching."""
    if user_id in _user_cache:
        return _user_cache[user_id]

    try:
        contacts = await client.get_contacts()
        for c in contacts:
            name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            _user_cache[c["id"]] = name or c.get("email", c["id"])
        return _user_cache.get(user_id, user_id)
    except Exception:
        return user_id


def _format_task(task, include_description: bool = True) -> str:
    """Format task for display."""
    status_display = task.custom_status_name or task.status
    lines = [
        f"**{task.title}**",
        f"- ID: `{task.id}`",
        f"- Status: {status_display}",
    ]

    if task.importance:
        lines.append(f"- Importance: {task.importance}")

    if task.dates:
        if task.dates.get("start"):
            lines.append(f"- Start: {task.dates['start']}")
        if task.dates.get("due"):
            lines.append(f"- Due: {task.dates['due']}")

    if task.created_date:
        lines.append(f"- Created: {task.created_date.strftime('%Y-%m-%d %H:%M')}")

    if task.updated_date:
        lines.append(f"- Updated: {task.updated_date.strftime('%Y-%m-%d %H:%M')}")

    if task.completed_date:
        lines.append(f"- Completed: {task.completed_date.strftime('%Y-%m-%d %H:%M')}")

    if task.parent_ids:
        lines.append(
            f"- Parent folders: {', '.join(f'`{pid}`' for pid in task.parent_ids)}"
        )

    if task.super_task_ids:
        lines.append(
            f"- Parent tasks: {', '.join(f'`{sid}`' for sid in task.super_task_ids)}"
        )

    if task.responsible_ids:
        lines.append(
            f"- Assigned: {', '.join(f'`{rid}`' for rid in task.responsible_ids)}"
        )

    if task.custom_status_id:
        lines.append(f"- Custom status ID: `{task.custom_status_id}`")

    if task.custom_fields:
        cf_parts = []
        for cf in task.custom_fields:
            cf_parts.append(f"`{cf.get('id')}` = {cf.get('value', '')}")
        lines.append(f"- Custom fields: {', '.join(cf_parts)}")

    if task.permalink:
        lines.append(f"- Link: {task.permalink}")

    if include_description and task.description and task.description.strip():
        # Raw HTML so agents can edit text in place without losing formatting
        lines.append(f"\n**Description (HTML):**\n{task.description}")

    return "\n".join(lines)


def _format_comment(comment, author_name: str = "") -> str:
    """Format comment for display."""
    date_str = (
        comment.created_date.strftime("%Y-%m-%d %H:%M")
        if comment.created_date
        else "Unknown"
    )
    author = author_name or comment.author_id

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", comment.text)
    text = text.replace("&nbsp;", " ").strip()

    return f"**{author}** ({date_str}):\n{text}"


def _format_project(project) -> str:
    """Format project for display."""
    status_display = project.custom_status_name or "No status"
    lines = [
        f"**{project.title}**",
        f"- ID: `{project.id}`",
        f"- Status: {status_display}",
    ]

    if project.owner_ids:
        lines.append(f"- Owners: {', '.join(f'`{oid}`' for oid in project.owner_ids)}")

    if project.created_date:
        lines.append(f"- Created: {project.created_date.strftime('%Y-%m-%d %H:%M')}")

    if project.updated_date:
        lines.append(f"- Updated: {project.updated_date.strftime('%Y-%m-%d %H:%M')}")

    if project.child_ids:
        lines.append(f"- Child folders: {len(project.child_ids)}")

    if project.permalink:
        lines.append(f"- Link: {project.permalink}")

    if project.description and project.description.strip():
        # Raw HTML so agents can edit text in place without losing formatting
        lines.append(f"\n**Description (HTML):**\n{project.description}")

    return "\n".join(lines)


def _format_attachment(attachment) -> str:
    """Format attachment for display."""
    date_str = (
        attachment.created_date.strftime("%Y-%m-%d")
        if attachment.created_date
        else "Unknown"
    )
    size_str = f"{attachment.size:,} bytes" if attachment.size else "Unknown size"

    lines = [
        f"- **{attachment.name}**",
        f"  - ID: `{attachment.id}`",
        f"  - Size: {size_str}",
        f"  - Type: {attachment.content_type or 'Unknown'}",
        f"  - Date: {date_str}",
    ]

    if attachment.url:
        lines.append(f"  - URL: {attachment.url}")

    return "\n".join(lines)


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return _add_id_hints(_tool_definitions())


def _tool_definitions() -> list[Tool]:
    """Tool definitions, before ID hints are added."""
    return [
        Tool(
            name="search_tasks",
            description=(
                "Search for Wrike tasks by title, status, folder,"
                " or date range. Supports pagination."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Search by title (partial match)",
                    },
                    "status": {
                        "type": "string",
                        "description": (
                            "Filter by status: Active, Completed,"
                            " Deferred, Cancelled"
                        ),
                        "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "Scope search to a specific folder",
                    },
                    "created_start": {
                        "type": "string",
                        "description": "Tasks created on or after (YYYY-MM-DD)",
                    },
                    "created_end": {
                        "type": "string",
                        "description": "Tasks created on or before (YYYY-MM-DD)",
                    },
                    "updated_start": {
                        "type": "string",
                        "description": "Tasks updated on or after (YYYY-MM-DD)",
                    },
                    "updated_end": {
                        "type": "string",
                        "description": "Tasks updated on or before (YYYY-MM-DD)",
                    },
                    "sort_field": {
                        "type": "string",
                        "description": "Sort by field (CreatedDate, UpdatedDate)",
                        "enum": ["CreatedDate", "UpdatedDate"],
                    },
                    "sort_order": {
                        "type": "string",
                        "description": "Sort direction",
                        "enum": ["Asc", "Desc"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 50)",
                        "default": 50,
                    },
                },
            },
        ),
        Tool(
            name="get_task",
            description="Get detailed information about a specific Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_task_comments",
            description="Get comments/notes for a Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum comments to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_task_attachments",
            description="Get attachments for a Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum attachments to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_task_full",
            description=(
                "Get complete task details including description,"
                " comments, and attachments"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="list_folders",
            description="List Wrike folders/projects. Use parent_folder_id to list children of a specific folder (e.g., projects under ACv2 Pipeline). Deleted/recycled folders are hidden by default — use include_deleted=true to see them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_folder_id": {
                        "type": "string",
                        "description": "Parent folder ID to list children of. Without this, lists top-level folders.",
                    },
                    "include_deleted": {
                        "type": "boolean",
                        "description": "Include recycle bin folders (default: false)",
                        "default": False,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum folders to return (default: 50)",
                        "default": 50,
                    },
                },
            },
        ),
        Tool(
            name="create_task",
            description="Create a new Wrike task in a folder",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "The Wrike folder ID to create the task in",
                    },
                    "title": {
                        "type": "string",
                        "description": "Task title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Task description (HTML allowed)"
                        + CHECKBOX_HINT,
                    },
                    "status": {
                        "type": "string",
                        "description": "Task status",
                        "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                    },
                    "responsible_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of contact IDs to assign as responsibles",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date in YYYY-MM-DD format",
                    },
                    "importance": {
                        "type": "string",
                        "description": "Task importance",
                        "enum": ["High", "Normal", "Low"],
                    },
                    "custom_fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["id", "value"],
                        },
                        "description": "Custom field values as [{id, value}] pairs",
                    },
                    "custom_status": {
                        "type": "string",
                        "description": (
                            "Custom workflow status ID" " (overrides generic status)"
                        ),
                    },
                    "custom_item_type_id": {
                        "type": "string",
                        "description": (
                            "Custom item type ID (e.g., Engineering"
                            " Project, Spike, Bug Report)"
                        ),
                    },
                },
                "required": ["folder_id", "title"],
            },
        ),
        Tool(
            name="update_task",
            description="Update an existing Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID to update",
                    },
                    "title": {
                        "type": "string",
                        "description": "New task title",
                    },
                    "description": {
                        "type": "string",
                        "description": "New task description (HTML allowed)"
                        + CHECKBOX_HINT,
                    },
                    "status": {
                        "type": "string",
                        "description": "New task status",
                        "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                    },
                    "add_responsibles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Contact IDs to add as responsibles",
                    },
                    "remove_responsibles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Contact IDs to remove from responsibles",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date in YYYY-MM-DD format",
                    },
                    "importance": {
                        "type": "string",
                        "description": "Task importance",
                        "enum": ["High", "Normal", "Low"],
                    },
                    "completed_date": {
                        "type": "string",
                        "description": "Completion date override in YYYY-MM-DD format",
                    },
                    "custom_fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["id", "value"],
                        },
                        "description": "Custom field values as [{id, value}] pairs",
                    },
                    "custom_status": {
                        "type": "string",
                        "description": (
                            "Custom workflow status ID" " (overrides generic status)"
                        ),
                    },
                    "add_super_tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Task IDs to add as parent tasks"
                            " (makes this task a subtask)"
                        ),
                    },
                    "remove_super_tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task IDs to remove as parent tasks",
                    },
                    "custom_item_type_id": {
                        "type": "string",
                        "description": (
                            "Custom item type ID (e.g., Engineering"
                            " Project, Spike, Bug Report)"
                        ),
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="edit_task_description",
            description=(
                "Edit part of a task description without touching its"
                " formatting. Replaces old_text with new_text inside the"
                " stored HTML (read it first with get_task, which returns"
                " the description as raw HTML). old_text must match the"
                " HTML exactly and occur once unless replace_all=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text or HTML fragment to replace",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text or HTML fragment"
                        + CHECKBOX_HINT,
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence (default false)",
                        "default": False,
                    },
                },
                "required": ["task_id", "old_text", "new_text"],
            },
        ),
        Tool(
            name="set_description_checkbox",
            description=(
                "Check or uncheck a checklist item in a task description,"
                " found by its visible text. Only that checkbox changes;"
                " the rest of the description is left untouched."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "label": {
                        "type": "string",
                        "description": "Visible text of the checklist item",
                    },
                    "checked": {
                        "type": "boolean",
                        "description": "true to check, false to uncheck (default true)",
                        "default": True,
                    },
                },
                "required": ["task_id", "label"],
            },
        ),
        Tool(
            name="delete_task",
            description="Delete a Wrike task permanently",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID to delete",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="delete_folder",
            description="Delete a Wrike folder permanently. The folder must be empty (no tasks or child folders) or Wrike will move contents to recycle bin.",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "The Wrike folder ID to delete",
                    },
                },
                "required": ["folder_id"],
            },
        ),
        Tool(
            name="delete_space",
            description="Delete a Wrike space permanently. The space should be empty (no folders or tasks). Use with extreme caution.",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_id": {
                        "type": "string",
                        "description": "The Wrike space ID to delete",
                    },
                },
                "required": ["space_id"],
            },
        ),
        Tool(
            name="create_folder",
            description="Create a new Wrike folder inside a parent folder",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_folder_id": {
                        "type": "string",
                        "description": "The parent folder ID",
                    },
                    "title": {
                        "type": "string",
                        "description": "Folder title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional folder description",
                    },
                },
                "required": ["parent_folder_id", "title"],
            },
        ),
        Tool(
            name="get_folder_tasks",
            description=(
                "Get tasks within a Wrike folder with pagination and"
                " date filtering. Use recursive=true to include tasks"
                " from child folders."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "The Wrike folder ID",
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status",
                        "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Include tasks from child folders (default: false)",
                        "default": False,
                    },
                    "created_start": {
                        "type": "string",
                        "description": "Tasks created on or after (YYYY-MM-DD)",
                    },
                    "created_end": {
                        "type": "string",
                        "description": "Tasks created on or before (YYYY-MM-DD)",
                    },
                    "updated_start": {
                        "type": "string",
                        "description": "Tasks updated on or after (YYYY-MM-DD)",
                    },
                    "updated_end": {
                        "type": "string",
                        "description": "Tasks updated on or before (YYYY-MM-DD)",
                    },
                    "sort_field": {
                        "type": "string",
                        "description": "Sort by field",
                        "enum": ["CreatedDate", "UpdatedDate"],
                    },
                    "sort_order": {
                        "type": "string",
                        "description": "Sort direction",
                        "enum": ["Asc", "Desc"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results per folder (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["folder_id"],
            },
        ),
        Tool(
            name="get_workflows",
            description="List all Wrike workflows and their custom statuses",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_name": {
                        "type": "string",
                        "description": (
                            "Filter by workflow name"
                            " (partial match, case-insensitive)"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_custom_item_types",
            description=(
                "List all Wrike custom item types"
                " (Engineering Project, Spike, Bug Report, etc.)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": (
                            "Filter by type name" " (partial match, case-insensitive)"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_custom_fields",
            description=(
                "List all Wrike custom field definitions"
                " (IDs, types, allowed values)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": (
                            "Filter by field name" " (partial match, case-insensitive)"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="attach_file",
            description="Attach a local file to a Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute local path to the file to attach",
                    },
                    "file_name": {
                        "type": "string",
                        "description": (
                            "Display name for the attachment"
                            " (defaults to filename from path)"
                        ),
                    },
                },
                "required": ["task_id", "file_path"],
            },
        ),
        Tool(
            name="complete_task",
            description="Mark a Wrike task as completed",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID to complete",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="create_project",
            description=(
                "Create a Wrike project (folder with project"
                " properties) inside a parent folder"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_folder_id": {
                        "type": "string",
                        "description": "The parent folder ID to create the project in",
                    },
                    "title": {
                        "type": "string",
                        "description": "Project title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Project description (HTML allowed)",
                    },
                    "owner_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Contact IDs for project owners",
                    },
                    "custom_status": {
                        "type": "string",
                        "description": "Custom workflow status ID for the project",
                    },
                    "custom_fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["id", "value"],
                        },
                        "description": "Custom field values as [{id, value}] pairs",
                    },
                },
                "required": ["parent_folder_id", "title"],
            },
        ),
        Tool(
            name="update_project",
            description="Update an existing Wrike project",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "The Wrike project (folder) ID to update",
                    },
                    "title": {
                        "type": "string",
                        "description": "New project title",
                    },
                    "description": {
                        "type": "string",
                        "description": "New project description (HTML allowed)",
                    },
                    "custom_status": {
                        "type": "string",
                        "description": "Custom workflow status ID",
                    },
                    "custom_fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["id", "value"],
                        },
                        "description": "Custom field values as [{id, value}] pairs",
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="get_project",
            description="Get detailed information about a Wrike project including its description",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "The Wrike project (folder) ID",
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="discover_account",
            description=(
                "Discover Wrike account structure: authenticated user, spaces, "
                "folders, workflows (account-level and space-scoped), custom fields, "
                "and item types. Use this to understand the account layout and "
                "identify which folders/workflows to use."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "include_custom_fields": {
                        "type": "boolean",
                        "description": "Include custom field definitions (default: false)",
                        "default": False,
                    },
                    "include_item_types": {
                        "type": "boolean",
                        "description": "Include custom item type definitions (default: false)",
                        "default": False,
                    },
                    "space_id": {
                        "type": "string",
                        "description": "Only discover a specific space (by ID). Without this, discovers all spaces.",
                    },
                },
            },
        ),
        Tool(
            name="create_comment",
            description="Add a comment to a Wrike task (HTML allowed)",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "text": {
                        "type": "string",
                        "description": "Comment text (HTML allowed)",
                    },
                },
                "required": ["task_id", "text"],
            },
        ),
        Tool(
            name="list_request_forms",
            description=(
                "List Wrike request forms visible to this token, with each "
                "field's ID, type, title, mandatory flag and options. Use the "
                "field IDs with submit_request_form. The list omits some forms "
                "the token can still read; pass form_id to fetch one directly."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": (
                            "Fetch this one form: API ID (16 chars) or the "
                            "numeric formid from the form's URL"
                        ),
                    },
                    "title_contains": {
                        "type": "string",
                        "description": "Only forms whose title contains this text (case-insensitive)",
                    },
                },
            },
        ),
        Tool(
            name="submit_request_form",
            description=(
                "Submit a Wrike request form. This creates real work in Wrike; "
                "confirm field values with the user first. Wrike runs the "
                "submission as an async job and checks mandatory fields only "
                "then; this tool waits for the job and reports the outcome."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": (
                            "Request form: API ID (16 chars) or the numeric "
                            "formid from the form's URL"
                        ),
                    },
                    "fields": {
                        "type": "array",
                        "description": (
                            "One entry per field. Choice fields take the option "
                            "text or its ID (text is translated to the ID); Date "
                            "takes YYYY-MM-DD; User takes a contact ID."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "field_id": {"type": "string"},
                                "values": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["field_id", "values"],
                        },
                    },
                },
                "required": ["form_id", "fields"],
            },
        ),
        Tool(
            name="create_timelog",
            description="Log time against a Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Hours to log (e.g. 1.5)",
                    },
                    "tracked_date": {
                        "type": "string",
                        "description": "Date the work was done (YYYY-MM-DD)",
                    },
                    "comment": {
                        "type": "string",
                        "description": "Optional description of work done",
                    },
                    "category_id": {
                        "type": "string",
                        "description": "Timelog category ID (e.g. Development, Research)",
                    },
                },
                "required": ["task_id", "hours", "tracked_date"],
            },
        ),
        Tool(
            name="get_task_timelogs",
            description="Get time entries logged against a Wrike task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_timelog_report",
            description=(
                "Get a time report — all timelog entries optionally"
                " filtered by folder and date range"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "Folder ID to scope timelogs to",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start of date range (YYYY-MM-DD)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End of date range (YYYY-MM-DD)",
                    },
                },
            },
        ),
        Tool(
            name="move_task",
            description=(
                "Move a task between folders/projects by adding"
                " or removing parent folders"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Wrike task ID to move",
                    },
                    "add_parents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Folder/project IDs to add the task to",
                    },
                    "remove_parents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Folder/project IDs to remove the task from",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_me",
            description="Get the authenticated Wrike user: ID, name, email and role",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="search_users",
            description=(
                "Find Wrike users and groups by part of their first name, last"
                " name or email (case- and accent-insensitive). Returns IDs to"
                " use as assignees, owners or approvers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to match, e.g. 'laura' or '@acme.com'",
                    },
                    "include_deleted": {
                        "type": "boolean",
                        "description": "Include deleted users (default: false)",
                        "default": False,
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Reload the contact list instead of using the cache",
                        "default": False,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_spaces",
            description="List the Wrike spaces you can access, with ID, title and access type",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_item_children",
            description=(
                "List the direct children of a space, folder, project or task:"
                " child folders and projects, then tasks (or subtasks of a task),"
                " each with ID, type, status and link. Use it to walk the"
                " hierarchy one level at a time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Space, folder, project or task." + ID_HINT,
                    },
                    "include_tasks": {
                        "type": "boolean",
                        "description": "Include tasks directly in a folder (default: true)",
                        "default": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum children to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["item_id"],
            },
        ),
    ]


def _contact_name(contact: dict) -> str:
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
    return name or _contact_email(contact) or contact.get("id", "")


def _contact_email(contact: dict) -> str:
    profiles = contact.get("profiles") or [{}]
    return profiles[0].get("email", "") or ""


def _fold(text: str) -> str:
    """Lower-case text without accents, for forgiving matches."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _format_contact(contact: dict) -> str:
    kind = "Group" if contact.get("type") == "Group" else "Person"
    parts = [f"- **{_contact_name(contact)}** ({kind})", f"  - ID: `{contact.get('id')}`"]
    email = _contact_email(contact)
    if email:
        parts.append(f"  - Email: {email}")
    if contact.get("deleted"):
        parts.append("  - Deleted: yes")
    return "\n".join(parts)


def _folder_type(folder: dict) -> str:
    if folder.get("scope") == "WsRoot" or folder.get("space"):
        return "Space"
    return "Project" if folder.get("project") else "Folder"


def _is_recycled(folder: dict) -> bool:
    return folder.get("scope", "") in ("RbFolder", "RbRoot")


def _child_line(title: str, item_id: str, kind: str, status: str = "", link: str = "") -> str:
    status_part = f" | {status}" if status else ""
    link_part = f" | {link}" if link else ""
    return f"- **{title}** ({kind}{status_part}) — `{item_id}`{link_part}"


def _task_child(task) -> str:
    status = task.custom_status_name or task.status
    return _child_line(task.title, task.id, "Task", status, task.permalink or "")


async def _item_children(client: WrikeClient, arguments: dict) -> str:
    """Direct children of a space, folder, project or task, as markdown."""
    raw = arguments["item_id"]
    limit = arguments.get("limit", 50)
    include_tasks = arguments.get("include_tasks", True)

    folder = None
    try:
        folder = await client.get_folder(await client.resolve_id(raw, "folder"))
    except (httpx.HTTPStatusError, ValueError):
        pass

    if folder is None:
        try:
            task_id = await client.resolve_id(raw, "task")
            task, sub_ids = await client.get_subtask_ids(task_id)
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise ValueError(
                f"'{raw}' is not a space, folder, project or task you can access"
            ) from exc
        subtasks = await client.get_tasks_by_ids(sub_ids[:limit]) if sub_ids else []
        lines = [f"**{task.title}** (Task) — `{task.id}`", ""]
        if not subtasks:
            return "\n".join(lines + ["No subtasks."])
        lines.append(f"Subtasks ({len(subtasks)} of {len(sub_ids)}):")
        lines.extend(_task_child(t) for t in subtasks)
        return "\n".join(lines)

    folder_id = folder["id"]
    child_ids = folder.get("childIds", [])
    children = await client.get_folders_by_ids(child_ids) if child_ids else []
    children = [c for c in children if not _is_recycled(c)][:limit]
    tasks = (
        await client.get_direct_folder_tasks(folder_id, limit - len(children))
        if include_tasks and len(children) < limit
        else []
    )

    lines = [
        f"**{folder.get('title', 'Untitled')}** ({_folder_type(folder)}) — `{folder_id}`",
        "",
    ]
    if not children and not tasks:
        return "\n".join(lines + ["No children."])
    if children:
        lines.append(f"Folders and projects ({len(children)}):")
        lines.extend(
            _child_line(
                c.get("title", "Untitled"), c["id"], _folder_type(c),
                link=c.get("permalink", ""),
            )
            for c in children
        )
    if tasks:
        if children:
            lines.append("")
        lines.append(f"Tasks ({len(tasks)}):")
        lines.extend(_task_child(t) for t in tasks)
    if len(children) + len(tasks) >= limit:
        lines.append(f"\nShowing the first {limit} items; raise limit to see more.")
    return "\n".join(lines)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    token = get_access_token()
    if not token:
        return [
            TextContent(
                type="text",
                text="Error: Wrike access token not configured.\n\n"
                "To configure, run: `wrike-auth store`\n"
                "Or set environment variable:"
                " `export WRIKE_ACCESS_TOKEN=your_token`\n\n"
                "Get your token from:"
                " https://www.wrike.com/frontend/apps/index.html#/api",
            )
        ]

    try:
        async with WrikeClient(token) as client:
            arguments = await _resolve_id_args(client, arguments)

            if name == "search_tasks":
                title = arguments.get("title")
                status = arguments.get("status")
                limit = arguments.get("limit", 50)

                tasks = await client.search_tasks(
                    title=title,
                    status=status,
                    folder_id=arguments.get("folder_id"),
                    limit=limit,
                    created_start=arguments.get("created_start"),
                    created_end=arguments.get("created_end"),
                    updated_start=arguments.get("updated_start"),
                    updated_end=arguments.get("updated_end"),
                    sort_field=arguments.get("sort_field"),
                    sort_order=arguments.get("sort_order"),
                )

                if not tasks:
                    search_desc = []
                    if title:
                        search_desc.append(f"title='{title}'")
                    if status:
                        search_desc.append(f"status='{status}'")
                    search_str = ", ".join(search_desc) if search_desc else "no filters"
                    return [
                        TextContent(type="text", text=f"No tasks found ({search_str}).")
                    ]

                output = [f"Found {len(tasks)} tasks:\n"]
                for task in tasks:
                    output.append(_format_task(task, include_description=False))
                    output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_task":
                task_id = arguments["task_id"]
                task = await client.get_task(task_id)
                return [TextContent(type="text", text=_format_task(task))]

            elif name == "get_task_comments":
                task_id = arguments["task_id"]
                limit = arguments.get("limit", 50)

                comments = await client.get_task_comments(task_id, limit=limit)

                if not comments:
                    return [
                        TextContent(
                            type="text", text="No comments found for this task."
                        )
                    ]

                # Get author names
                output = [f"**{len(comments)} Comments:**\n"]
                for comment in comments:
                    author_name = await _get_user_name(client, comment.author_id)
                    output.append(_format_comment(comment, author_name))
                    output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_task_attachments":
                task_id = arguments["task_id"]
                limit = arguments.get("limit", 50)

                attachments = await client.get_task_attachments(task_id, limit=limit)

                if not attachments:
                    return [
                        TextContent(
                            type="text", text="No attachments found for this task."
                        )
                    ]

                output = [f"**{len(attachments)} Attachments:**\n"]
                for attachment in attachments:
                    output.append(_format_attachment(attachment))
                    output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_task_full":
                task_id = arguments["task_id"]

                # Fetch task, comments, and attachments in parallel
                task = await client.get_task(task_id)
                comments = await client.get_task_comments(task_id)
                attachments = await client.get_task_attachments(task_id)

                output = [
                    "# Task Details",
                    "",
                    _format_task(task),
                    "",
                ]

                if comments:
                    output.extend(["---", f"## Comments ({len(comments)})", ""])
                    for comment in comments:
                        author_name = await _get_user_name(client, comment.author_id)
                        output.append(_format_comment(comment, author_name))
                        output.append("")

                if attachments:
                    output.extend(["---", f"## Attachments ({len(attachments)})", ""])
                    for attachment in attachments:
                        output.append(_format_attachment(attachment))
                        output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "list_folders":
                limit = arguments.get("limit", 50)
                parent_folder_id = arguments.get("parent_folder_id")
                include_deleted = arguments.get("include_deleted", False)
                folders = await client.get_folders(parent_folder_id=parent_folder_id)

                # Filter out recycle bin items by default
                if not include_deleted:
                    folders = [
                        f for f in folders
                        if f.get("scope", "") not in ("RbFolder", "RbRoot")
                    ]

                if not folders:
                    ctx = f" under `{parent_folder_id}`" if parent_folder_id else ""
                    return [TextContent(type="text", text=f"No folders found{ctx}.")]

                output = [f"Found {len(folders[:limit])} folders:\n"]
                for f in folders[:limit]:
                    title = f.get("title", "Untitled")
                    folder_id = f.get("id", "Unknown")
                    scope = f.get("scope", "")
                    project = f.get("project", {})
                    is_project = bool(project)

                    type_label = "Project" if is_project else "Folder"
                    output.append(f"- **{title}** ({type_label})")
                    output.append(f"  - ID: `{folder_id}`")
                    if scope:
                        output.append(f"  - Scope: {scope}")
                    if is_project:
                        owner_ids = project.get("ownerIds", [])
                        if owner_ids:
                            output.append(f"  - Owners: {', '.join(f'`{o}`' for o in owner_ids)}")
                        status_id = project.get("customStatusId")
                        if status_id:
                            output.append(f"  - Status ID: `{status_id}`")
                    child_ids = f.get("childIds", [])
                    if child_ids:
                        output.append(f"  - Children: {len(child_ids)}")
                    output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "create_task":
                folder_id = arguments["folder_id"]
                title = arguments["title"]

                dates: dict | None = None
                start_date = arguments.get("start_date")
                due_date = arguments.get("due_date")
                if start_date or due_date:
                    dates = {}
                    if start_date:
                        dates["start"] = start_date
                    if due_date:
                        dates["due"] = due_date

                task = await client.create_task(
                    folder_id=folder_id,
                    title=title,
                    description=arguments.get("description"),
                    status=arguments.get("status"),
                    responsible_ids=arguments.get("responsible_ids"),
                    dates=dates,
                    importance=arguments.get("importance"),
                    custom_fields=arguments.get("custom_fields"),
                    custom_status=arguments.get("custom_status"),
                    custom_item_type_id=arguments.get("custom_item_type_id"),
                )

                output = ["**Task created successfully:**\n", _format_task(task)]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "update_task":
                task_id = arguments["task_id"]

                dates = None
                start_date = arguments.get("start_date")
                due_date = arguments.get("due_date")
                if start_date or due_date:
                    dates = {}
                    if start_date:
                        dates["start"] = start_date
                    if due_date:
                        dates["due"] = due_date

                task = await client.update_task(
                    task_id=task_id,
                    title=arguments.get("title"),
                    description=arguments.get("description"),
                    status=arguments.get("status"),
                    add_responsibles=arguments.get("add_responsibles"),
                    remove_responsibles=arguments.get("remove_responsibles"),
                    dates=dates,
                    importance=arguments.get("importance"),
                    completed_date=arguments.get("completed_date"),
                    custom_fields=arguments.get("custom_fields"),
                    custom_status=arguments.get("custom_status"),
                    add_super_tasks=arguments.get("add_super_tasks"),
                    remove_super_tasks=arguments.get("remove_super_tasks"),
                    custom_item_type_id=arguments.get("custom_item_type_id"),
                )

                output = ["**Task updated successfully:**\n", _format_task(task)]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "edit_task_description":
                task_id = arguments["task_id"]
                old_text = arguments["old_text"]
                new_text = arguments["new_text"]
                replace_all = arguments.get("replace_all", False)

                current = (await client.get_task(task_id)).description or ""
                count = current.count(old_text) if old_text else 0
                if count == 0:
                    raise ValueError(
                        "old_text not found in the description HTML."
                        " Read the task first and copy the HTML exactly."
                    )
                if count > 1 and not replace_all:
                    raise ValueError(
                        f"old_text appears {count} times in the description."
                        " Add surrounding context or set replace_all=true."
                    )

                updated = current.replace(old_text, new_text)
                task = await client.update_task(task_id=task_id, description=updated)
                output = ["**Task description updated:**\n", _format_task(task)]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "set_description_checkbox":
                task_id = arguments["task_id"]
                label = " ".join(arguments["label"].split())
                checked = arguments.get("checked", True)

                current = (await client.get_task(task_id)).description or ""
                boxes = _find_checkboxes(current)
                if not boxes:
                    raise ValueError("The task description has no checkboxes.")
                matches = [b for b in boxes if b[1] == label]
                if not matches:
                    available = ", ".join(f"'{b[1]}'" for b in boxes)
                    raise ValueError(
                        f"No checkbox labelled '{label}'. Available: {available}"
                    )
                if len(matches) > 1:
                    raise ValueError(
                        f"{len(matches)} checkboxes are labelled '{label}';"
                        " use edit_task_description to change the right one."
                    )

                match, _, is_checked = matches[0]
                state = "checked" if checked else "unchecked"
                if is_checked == checked:
                    return [
                        TextContent(
                            type="text",
                            text=f"Checkbox '{label}' is already {state}; nothing changed.",
                        )
                    ]

                updated = (
                    current[: match.start()]
                    + _set_checked(match.group(0), checked)
                    + current[match.end():]
                )
                task = await client.update_task(task_id=task_id, description=updated)
                output = [f"**Checkbox '{label}' {state}:**\n", _format_task(task)]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "delete_task":
                task_id = arguments["task_id"]
                await client.delete_task(task_id)
                return [
                    TextContent(
                        type="text", text=f"Task `{task_id}` deleted successfully."
                    )
                ]

            elif name == "delete_folder":
                folder_id = arguments["folder_id"]
                await client.delete_folder(folder_id)
                return [
                    TextContent(
                        type="text", text=f"Folder `{folder_id}` deleted successfully."
                    )
                ]

            elif name == "delete_space":
                space_id = arguments["space_id"]
                await client.delete_space(space_id)
                return [
                    TextContent(
                        type="text", text=f"Space `{space_id}` deleted successfully."
                    )
                ]

            elif name == "create_folder":
                parent_folder_id = arguments["parent_folder_id"]
                title = arguments["title"]

                folder = await client.create_folder(
                    parent_folder_id=parent_folder_id,
                    title=title,
                    description=arguments.get("description"),
                )

                folder_id = folder.get("id", "Unknown")
                folder_title = folder.get("title", title)
                scope = folder.get("scope", "")

                output = [
                    "**Folder created successfully:**\n",
                    f"- **{folder_title}**",
                    f"  - ID: `{folder_id}`",
                ]
                if scope:
                    output.append(f"  - Scope: {scope}")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_folder_tasks":
                folder_id = arguments["folder_id"]
                status = arguments.get("status")
                recursive = arguments.get("recursive", False)
                limit = arguments.get("limit", 50)
                date_kwargs = {
                    "created_start": arguments.get("created_start"),
                    "created_end": arguments.get("created_end"),
                    "updated_start": arguments.get("updated_start"),
                    "updated_end": arguments.get("updated_end"),
                    "sort_field": arguments.get("sort_field"),
                    "sort_order": arguments.get("sort_order"),
                }

                all_tasks: list[WrikeTask] = []
                folder_labels: dict[str, str] = {}  # task_id -> folder info

                if recursive:
                    # Discover child folders dynamically
                    child_folders = await client.get_folders(parent_folder_id=folder_id)
                    # Filter out recycle bin
                    child_folders = [
                        f for f in child_folders
                        if f.get("scope", "") not in ("RbFolder", "RbRoot")
                    ]

                    # Query parent folder + all children in parallel
                    folder_ids = [folder_id] + [f["id"] for f in child_folders]
                    folder_names = {folder_id: "(root)"}
                    for f in child_folders:
                        folder_names[f["id"]] = f.get("title", f["id"])

                    coros = [
                        client.get_folder_tasks(
                            fid, status=status, limit=limit, **date_kwargs
                        )
                        for fid in folder_ids
                    ]
                    results = await asyncio.gather(*coros, return_exceptions=True)

                    seen_ids: set[str] = set()
                    for fid, result in zip(folder_ids, results):
                        if isinstance(result, Exception):
                            continue
                        for task in result:
                            if task.id not in seen_ids:
                                seen_ids.add(task.id)
                                all_tasks.append(task)
                                folder_labels[task.id] = folder_names.get(fid, fid)
                else:
                    all_tasks = await client.get_folder_tasks(
                        folder_id=folder_id,
                        status=status,
                        limit=limit,
                        **date_kwargs,
                    )

                if not all_tasks:
                    filter_desc = f" with status='{status}'" if status else ""
                    recursive_desc = " (recursive)" if recursive else ""
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"No tasks found in folder"
                                f" `{folder_id}`{recursive_desc}{filter_desc}."
                            ),
                        )
                    ]

                recursive_note = ""
                if recursive:
                    child_count = len([f for f in child_folders if f.get("scope", "") not in ("RbFolder", "RbRoot")])
                    recursive_note = f" (searched {child_count} child folders)"

                output = [f"Found {len(all_tasks)} tasks in folder `{folder_id}`{recursive_note}:\n"]
                for task in all_tasks:
                    task_output = _format_task(task, include_description=False)
                    if task.id in folder_labels:
                        task_output += f"\n- Folder: {folder_labels[task.id]}"
                    output.append(task_output)
                    output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_workflows":
                workflow_name = arguments.get("workflow_name", "").lower()
                workflows = await client.get_workflows()

                output = []
                for wf in workflows:
                    if wf.get("hidden", False):
                        continue
                    name_str = wf.get("name", "Untitled")
                    if workflow_name and workflow_name not in name_str.lower():
                        continue

                    output.append(f"**{name_str}** (ID: `{wf.get('id')}`)")
                    for status in wf.get("customStatuses", []):
                        if status.get("hidden", False):
                            continue
                        output.append(
                            f"  - {status.get('name')} | "
                            f"group: {status.get('group')} | "
                            f"id: `{status.get('id')}`"
                        )
                    output.append("")

                if not output:
                    return [TextContent(type="text", text="No workflows found.")]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_custom_item_types":
                search = arguments.get("search", "").lower()
                item_types = await client.get_custom_item_types()

                output = []
                for it in item_types:
                    title = it.get("title", "Untitled")
                    if search and search not in title.lower():
                        continue
                    related_type = it.get("relatedType", "Unknown")
                    space = it.get("spaceId", "")
                    desc = it.get("description", "")
                    desc_str = f" — {desc}" if desc else ""
                    output.append(
                        f"- **{title}** | {related_type} | "
                        f"id: `{it.get('id')}` | space: `{space}`{desc_str}"
                    )

                if not output:
                    return [
                        TextContent(type="text", text="No custom item types found.")
                    ]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_custom_fields":
                search = arguments.get("search", "").lower()
                fields = await client.get_custom_fields()

                output = []
                for f in fields:
                    title = f.get("title", "Untitled")
                    if search and search not in title.lower():
                        continue

                    settings = f.get("settings", {})
                    values = settings.get("values", [])
                    field_type = f.get("type", "Unknown")

                    line = f"**{title}** | type: {field_type} | id: `{f.get('id')}`"
                    output.append(line)
                    if values:
                        output.append(f"  Values: {', '.join(str(v) for v in values)}")

                if not output:
                    return [TextContent(type="text", text="No custom fields found.")]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "attach_file":
                task_id = arguments["task_id"]
                file_path = arguments["file_path"]
                file_name = arguments.get("file_name") or file_path.rsplit("/", 1)[-1]

                attachment = await client.attach_file(
                    task_id=task_id,
                    file_name=file_name,
                    file_path=file_path,
                )

                att_id = attachment.get("id", "Unknown")
                att_name = attachment.get("name", file_name)
                att_size = attachment.get("size", 0)

                return [
                    TextContent(
                        type="text",
                        text=f"**File attached successfully:**\n"
                        f"- Name: {att_name}\n"
                        f"- ID: `{att_id}`\n"
                        f"- Size: {att_size:,} bytes",
                    )
                ]

            elif name == "complete_task":
                task_id = arguments["task_id"]
                task = await client.update_task(task_id=task_id, status="Completed")
                output = ["**Task marked as completed:**\n", _format_task(task)]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "create_project":
                parent_folder_id = arguments["parent_folder_id"]
                title = arguments["title"]

                project = await client.create_project(
                    parent_folder_id=parent_folder_id,
                    title=title,
                    description=arguments.get("description"),
                    owner_ids=arguments.get("owner_ids"),
                    custom_status=arguments.get("custom_status"),
                    custom_fields=arguments.get("custom_fields"),
                )

                output = [
                    "**Project created successfully:**\n",
                    _format_project(project),
                ]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "update_project":
                project_id = arguments["project_id"]

                project = await client.update_project(
                    project_id=project_id,
                    title=arguments.get("title"),
                    description=arguments.get("description"),
                    custom_status=arguments.get("custom_status"),
                    custom_fields=arguments.get("custom_fields"),
                )

                output = [
                    "**Project updated successfully:**\n",
                    _format_project(project),
                ]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_project":
                project_id = arguments["project_id"]
                project = await client.get_project(project_id)
                return [TextContent(type="text", text=_format_project(project))]

            elif name == "discover_account":
                include_custom_fields = arguments.get("include_custom_fields", False)
                include_item_types = arguments.get("include_item_types", False)
                filter_space_id = arguments.get("space_id")

                output = []

                # 1. Who am I?
                me = await client.get_me()
                my_id = me["id"]
                my_name = f"{me.get('firstName', '')} {me.get('lastName', '')}".strip()
                my_email = me.get("profiles", [{}])[0].get("email", "")
                output.append("# Wrike Account Discovery\n")
                output.append(f"## Authenticated User")
                output.append(f"- **{my_name}** ({my_email})")
                output.append(f"- Contact ID: `{my_id}`")
                output.append("")

                # 2. Spaces
                spaces = await client.get_spaces()
                if filter_space_id:
                    spaces = [s for s in spaces if s["id"] == filter_space_id]

                output.append(f"## Spaces ({len(spaces)})\n")

                # 3. Account-level workflows (fetch all, filter later to only referenced ones)
                account_workflows = await client.get_workflows()
                referenced_wf_ids: set[str] = set()
                space_scoped_wf_ids: set[str] = set()

                for space in spaces:
                    space_id = space["id"]
                    space_title = space.get("title", "Untitled")
                    space_type = space.get("accessType", "")
                    members = space.get("members", [])
                    am_member = any(m.get("id") == my_id for m in members)

                    output.append(f"### {space_title}")
                    output.append(f"- ID: `{space_id}`")
                    output.append(f"- Access: {space_type}")
                    output.append(f"- Members: {len(members)}")
                    output.append(f"- I am member: {am_member}")

                    # Fetch space-scoped workflows first (needed for folder workflow resolution)
                    space_workflows_cache = []
                    try:
                        space_workflows_cache = await client.get_space_workflows(space_id)
                        for wf in space_workflows_cache:
                            space_scoped_wf_ids.add(wf["id"])
                    except Exception:
                        pass

                    # Get top-level folders in this space (direct children only)
                    try:
                        all_folders = await client.get_folders(space_id=space_id)
                        # Filter recycle bin
                        all_folders = [
                            f for f in all_folders
                            if f.get("scope", "") not in ("RbFolder", "RbRoot")
                        ]
                        # Find space root and its direct children
                        space_root_child_ids = set()
                        for f in all_folders:
                            if f.get("id") == space_id:
                                space_root_child_ids = set(f.get("childIds", []))
                                break
                        folders = [
                            f for f in all_folders
                            if f.get("id") in space_root_child_ids
                        ]
                        total_count = len(all_folders) - 1  # exclude space root itself
                        if folders:
                            output.append(f"- **Top-level folders ({len(folders)} of {total_count} total):**")
                            # Fetch individual folder details to get workflowId
                            for f in folders:
                                f_id = f.get("id", "")
                                f_title = f.get("title", "Untitled")
                                wf_id = ""
                                wf_name = ""
                                try:
                                    folder_detail = await client.get_folder(f_id)
                                    wf_id = folder_detail.get("workflowId", "")
                                except Exception:
                                    pass
                                # Resolve workflow name from account + space workflows
                                if wf_id:
                                    referenced_wf_ids.add(wf_id)
                                    all_wfs = account_workflows + space_workflows_cache
                                    for w in all_wfs:
                                        if w["id"] == wf_id:
                                            wf_name = w["name"]
                                            break
                                project = f.get("project", {})
                                is_project = bool(project)
                                type_label = " (Project)" if is_project else ""
                                wf_label = f" | workflow: {wf_name} (`{wf_id}`)" if wf_name else (f" | workflowId: `{wf_id}`" if wf_id else "")
                                child_count = len(f.get("childIds", []))
                                children_label = f" | {child_count} children" if child_count else ""
                                output.append(
                                    f"  - `{f_id}` **{f_title}**{type_label}{wf_label}{children_label}"
                                )
                        else:
                            output.append(f"- Folders: {total_count} total (none at top level)")
                    except Exception:
                        output.append("  - (could not list folders)")

                    # Display space-scoped workflows (already fetched above)
                    if space_workflows_cache:
                        visible_space_wfs = [w for w in space_workflows_cache if not w.get("hidden", False)]
                        if visible_space_wfs:
                            output.append(f"- **Space-scoped Workflows ({len(visible_space_wfs)}):**")
                            for wf in visible_space_wfs:
                                wf_name = wf.get("name", "Untitled")
                                wf_id = wf.get("id", "")
                                statuses = [
                                    s for s in wf.get("customStatuses", [])
                                    if not s.get("hidden", False)
                                ]
                                output.append(f"  - **{wf_name}** (`{wf_id}`) — {len(statuses)} statuses")
                                for s in statuses:
                                    output.append(
                                        f"    - {s.get('name')} | {s.get('group')} | `{s.get('id')}`"
                                    )

                    output.append("")

                # 4. Account-level workflows — only those referenced by folders
                used_account_wfs = [
                    w for w in account_workflows
                    if not w.get("hidden", False)
                    and w["id"] in referenced_wf_ids
                    and w["id"] not in space_scoped_wf_ids
                ]
                output.append(f"## Account-Level Workflows (showing {len(used_account_wfs)} used by your folders)\n")
                for wf in used_account_wfs:
                    wf_name = wf.get("name", "Untitled")
                    wf_id = wf.get("id", "")
                    is_standard = wf.get("standard", False)
                    std_label = " (standard)" if is_standard else ""
                    statuses = [
                        s for s in wf.get("customStatuses", [])
                        if not s.get("hidden", False)
                    ]
                    output.append(f"### {wf_name}{std_label} (`{wf_id}`)")
                    for s in statuses:
                        output.append(
                            f"- {s.get('name')} | {s.get('group')} | `{s.get('id')}`"
                        )
                    output.append("")

                # 5. Custom fields (optional)
                if include_custom_fields:
                    fields = await client.get_custom_fields()
                    output.append(f"## Custom Fields ({len(fields)})\n")
                    for f in fields:
                        title = f.get("title", "Untitled")
                        field_type = f.get("type", "Unknown")
                        settings = f.get("settings", {})
                        values = settings.get("values", [])
                        line = f"- **{title}** | {field_type} | `{f.get('id')}`"
                        if values:
                            line += f" | values: {', '.join(str(v) for v in values[:10])}"
                            if len(values) > 10:
                                line += f" (+{len(values)-10} more)"
                        output.append(line)
                    output.append("")

                # 6. Custom item types (optional)
                if include_item_types:
                    item_types = await client.get_custom_item_types()
                    output.append(f"## Custom Item Types ({len(item_types)})\n")
                    for it in item_types:
                        title = it.get("title", "Untitled")
                        related = it.get("relatedType", "Unknown")
                        space = it.get("spaceId", "")
                        output.append(
                            f"- **{title}** | {related} | `{it.get('id')}` | space: `{space}`"
                        )
                    output.append("")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "create_comment":
                task_id = arguments["task_id"]
                text = arguments["text"]

                comment = await client.create_comment(task_id=task_id, text=text)
                author_name = await _get_user_name(client, comment.author_id)

                return [
                    TextContent(
                        type="text",
                        text=(
                            f"**Comment added to task `{task_id}`:**\n\n"
                            f"{_format_comment(comment, author_name)}"
                        ),
                    )
                ]

            elif name == "list_request_forms":
                needle = (arguments.get("title_contains") or "").lower()
                if arguments.get("form_id"):
                    forms = [await client.get_request_form(arguments["form_id"])]
                else:
                    forms = await client.list_request_forms()
                output = []
                for f in forms:
                    if needle and needle not in (f.get("title") or "").lower():
                        continue
                    output.append(f"## {f.get('title')} — `{f.get('id')}`")
                    if f.get("description"):
                        output.append(f.get("description"))
                    for page in f.get("pages", []):
                        for fld in page.get("fields", []):
                            if fld.get("type") == "Separator":
                                continue
                            opts = [
                                i.get("title", i) if isinstance(i, dict) else i
                                for i in fld.get("items", []) or []
                            ]
                            line = (
                                f"- **{(fld.get('title') or '').strip()}** "
                                f"({fld.get('type')}"
                                f"{', mandatory' if fld.get('mandatory') else ''})"
                                f" `{fld.get('id')}`"
                            )
                            if opts:
                                line += f" — options: {', '.join(map(str, opts))}"
                            output.append(line)
                    output.append("")
                if not output:
                    output = ["No request forms visible to this token."]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "submit_request_form":
                form_fields = [
                    {"fieldId": f["field_id"], "values": f["values"]}
                    for f in arguments["fields"]
                ]
                job = await client.submit_request_form(
                    form_id=arguments["form_id"], form_fields=form_fields
                )
                status = job.get("status")
                lines = [f"**Request form job:** `{job.get('id')}` — {status}"]
                if job.get("errorMessage"):
                    lines.append(f"Error: {job['errorMessage']}")
                extra = {
                    k: v
                    for k, v in job.items()
                    if k not in ("id", "status", "progressPercent", "type", "errorMessage")
                }
                if extra:
                    lines.append(f"Result: {extra}")
                if status in ("InQueue", "InProgress"):
                    lines.append("Still running; check again before assuming it succeeded.")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "create_timelog":
                task_id = arguments["task_id"]
                hours = arguments["hours"]
                tracked_date = arguments["tracked_date"]

                timelog = await client.create_timelog(
                    task_id=task_id,
                    hours=hours,
                    tracked_date=tracked_date,
                    comment=arguments.get("comment"),
                    category_id=arguments.get("category_id"),
                )

                return [
                    TextContent(
                        type="text",
                        text=(
                            f"**Time logged to task `{task_id}`:**\n"
                            f"- Hours: {timelog.hours}\n"
                            f"- Date: {timelog.tracked_date}\n"
                            f"- Comment: {timelog.comment or '(none)'}\n"
                            f"- ID: `{timelog.id}`"
                        ),
                    )
                ]

            elif name == "get_task_timelogs":
                task_id = arguments["task_id"]
                timelogs = await client.get_task_timelogs(task_id)

                if not timelogs:
                    return [
                        TextContent(
                            type="text",
                            text=f"No time entries for task `{task_id}`.",
                        )
                    ]

                total_hours = sum(t.hours for t in timelogs)
                output = [f"**{len(timelogs)} time entries ({total_hours:.1f}h total):**\n"]
                for t in timelogs:
                    user_name = await _get_user_name(client, t.user_id)
                    comment_str = f" — {t.comment}" if t.comment else ""
                    output.append(
                        f"- {t.tracked_date} | {t.hours}h | {user_name}{comment_str}"
                    )

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_timelog_report":
                folder_id = arguments.get("folder_id")
                start_date = arguments.get("start_date")
                end_date = arguments.get("end_date")

                timelogs = await client.get_timelogs(
                    folder_id=folder_id,
                    start_date=start_date,
                    end_date=end_date,
                )

                if not timelogs:
                    scope = f" in folder `{folder_id}`" if folder_id else ""
                    date_range = ""
                    if start_date and end_date:
                        date_range = f" from {start_date} to {end_date}"
                    elif start_date:
                        date_range = f" from {start_date}"
                    elif end_date:
                        date_range = f" through {end_date}"
                    return [
                        TextContent(
                            type="text",
                            text=f"No time entries found{scope}{date_range}.",
                        )
                    ]

                # Aggregate by user
                by_user: dict[str, float] = {}
                by_task: dict[str, float] = {}
                total_hours = 0.0
                for t in timelogs:
                    by_user[t.user_id] = by_user.get(t.user_id, 0.0) + t.hours
                    by_task[t.task_id] = by_task.get(t.task_id, 0.0) + t.hours
                    total_hours += t.hours

                output = [f"**Time Report: {total_hours:.1f}h across {len(timelogs)} entries**\n"]

                output.append("**By person:**")
                for user_id, hours in sorted(by_user.items(), key=lambda x: -x[1]):
                    user_name = await _get_user_name(client, user_id)
                    output.append(f"- {user_name}: {hours:.1f}h")

                output.append(f"\n**By task ({len(by_task)} tasks):**")
                for task_id, hours in sorted(by_task.items(), key=lambda x: -x[1])[:20]:
                    output.append(f"- `{task_id}`: {hours:.1f}h")
                if len(by_task) > 20:
                    output.append(f"  ... and {len(by_task) - 20} more tasks")

                return [TextContent(type="text", text="\n".join(output))]

            elif name == "move_task":
                task_id = arguments["task_id"]

                task = await client.move_task(
                    task_id=task_id,
                    add_parents=arguments.get("add_parents"),
                    remove_parents=arguments.get("remove_parents"),
                )

                output = [
                    "**Task moved successfully:**\n",
                    _format_task(task, include_description=False),
                ]
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_me":
                me = await client.get_me()
                profile = (me.get("profiles") or [{}])[0]
                lines = [
                    f"**{_contact_name(me)}**",
                    f"- ID: `{me.get('id')}`",
                    f"- Email: {_contact_email(me) or me.get('primaryEmail', '')}",
                    f"- Role: {profile.get('role', 'Unknown')}"
                    + (" (admin)" if profile.get("admin") else ""),
                ]
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "search_users":
                needle = _fold(arguments["query"].strip())
                include_deleted = arguments.get("include_deleted", False)
                limit = arguments.get("limit", 20)
                contacts = await client.list_contacts(refresh=arguments.get("refresh", False))
                matches = [
                    c for c in contacts
                    if (include_deleted or not c.get("deleted"))
                    and needle in _fold(" ".join(
                        [_contact_name(c), _contact_email(c), c.get("primaryEmail", "")]
                    ))
                ]
                if not matches:
                    text = f"No users match '{arguments['query']}'."
                    return [TextContent(type="text", text=text)]
                output = [f"Found {len(matches)} users:\n"]
                output.extend(_format_contact(c) for c in matches[:limit])
                if len(matches) > limit:
                    output.append(f"... and {len(matches) - limit} more")
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "list_spaces":
                spaces = await client.get_spaces()
                if not spaces:
                    return [TextContent(type="text", text="No spaces found.")]
                output = [f"Found {len(spaces)} spaces:\n"]
                for s in spaces:
                    output.append(
                        f"- **{s.get('title', 'Untitled')}** — ID: `{s.get('id')}`"
                        f" | access: {s.get('accessType', 'Unknown')}"
                    )
                return [TextContent(type="text", text="\n".join(output))]

            elif name == "get_item_children":
                return [TextContent(type="text", text=await _item_children(client, arguments))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def main():
    """Run the MCP server."""

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
