"""Tool definitions and executor for the reminder feature.

Three tools that Claude chains together to set reminders from natural-language
time expressions like "a week from Thursday":
  1. get_current_datetime  — returns current server time + weekday
  2. add_duration_to_datetime — computes a future datetime from duration + unit
  3. set_reminder — persists a Reminder row and returns a confirmation
"""
import uuid
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from models.reminder import Reminder


# ── Tool schemas (Anthropic tool_use format) ──────────────────────────────────

GET_CURRENT_DATETIME_TOOL = {
    "name": "get_current_datetime",
    "description": (
        "Return the current local date and time on the server, including the weekday name. "
        "Call this tool FIRST whenever a user mentions a relative date or time "
        "('next Thursday', 'in two weeks', 'a week from Friday', 'tomorrow', etc.) "
        "so you have an accurate baseline before computing any future datetime. "
        "Never assume or guess the current date — always call this tool to get it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date_format": {
                "type": "string",
                "description": (
                    "strftime format string for the returned datetime. "
                    "Default: '%Y-%m-%d %H:%M:%S' (e.g. '2026-06-12 14:30:00'). "
                    "The default ISO-like format is best when you plan to pass the result "
                    "to add_duration_to_datetime, which can parse it directly. "
                    "Use a custom format only when you need human-readable display."
                ),
            },
        },
    },
}

ADD_DURATION_TO_DATETIME_TOOL = {
    "name": "add_duration_to_datetime",
    "description": (
        "Add a duration to a given ISO 8601 datetime and return the resulting datetime "
        "plus its weekday name. Use this to compute exact future datetimes from expressions "
        "like 'in two weeks' or 'a week from Thursday'. "
        "Call get_current_datetime first to get the starting datetime. "
        "For 'a week from [weekday]': first advance to that weekday using days, then add 1 week. "
        "Always pass the full ISO datetime string you received from get_current_datetime."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "datetime": {
                "type": "string",
                "description": (
                    "Starting datetime in ISO 8601 format (e.g. '2026-06-12T14:30:00'). "
                    "Use the value returned by get_current_datetime."
                ),
            },
            "duration": {
                "type": "number",
                "description": "Amount to add (positive integer or decimal).",
            },
            "unit": {
                "type": "string",
                "enum": ["minutes", "hours", "days", "weeks", "months"],
                "description": "Unit of the duration to add.",
            },
        },
        "required": ["datetime", "duration", "unit"],
    },
}

SET_REMINDER_TOOL = {
    "name": "set_reminder",
    "description": (
        "Persist a reminder with a specific due date and time. "
        "Always compute the exact target datetime first using get_current_datetime and "
        "add_duration_to_datetime before calling this tool — never pass a vague or "
        "relative time string. The content should summarise what the user wants to be "
        "reminded about. Returns a confirmation with the stored due date."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "What the user should be reminded about (concise summary).",
            },
            "timestamp": {
                "type": "string",
                "description": "Exact due datetime in ISO 8601 format (e.g. '2026-06-25T09:00:00').",
            },
        },
        "required": ["content", "timestamp"],
    },
}

REMINDER_TOOLS = [GET_CURRENT_DATETIME_TOOL, ADD_DURATION_TO_DATETIME_TOOL, SET_REMINDER_TOOL]

# Weekday names indexed Monday=0 … Sunday=6 (matches datetime.weekday())
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _get_current_datetime(tool_input: dict | None = None) -> str:
    date_format = (tool_input or {}).get("date_format", "%Y-%m-%d %H:%M:%S")
    if not isinstance(date_format, str) or not date_format:
        return f"Error: 'date_format' must be a non-empty strftime string. Got: {date_format!r}"
    now = datetime.now()
    weekday = _WEEKDAY_NAMES[now.weekday()]
    formatted = now.strftime(date_format)
    return f"Current datetime: {formatted}, weekday: {weekday}"


def _add_duration_to_datetime(tool_input: dict) -> str:
    raw_dt = tool_input.get("datetime", "")
    duration = tool_input.get("duration")
    unit = tool_input.get("unit", "")

    if not raw_dt:
        return "Error: 'datetime' is required."
    if duration is None:
        return "Error: 'duration' is required."
    if unit not in {"minutes", "hours", "days", "weeks", "months"}:
        return f"Error: 'unit' must be one of minutes, hours, days, weeks, months. Got: {unit!r}"

    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return f"Error: 'duration' must be a number. Got: {duration!r}"

    try:
        # Parse ISO datetime; strip trailing Z if present for Python <3.11 compat
        dt_str = raw_dt.replace("Z", "+00:00") if raw_dt.endswith("Z") else raw_dt
        dt = datetime.fromisoformat(dt_str)
    except ValueError as exc:
        return f"Error: could not parse datetime {raw_dt!r}: {exc}"

    try:
        if unit == "minutes":
            result = dt + timedelta(minutes=duration)
        elif unit == "hours":
            result = dt + timedelta(hours=duration)
        elif unit == "days":
            result = dt + timedelta(days=duration)
        elif unit == "weeks":
            result = dt + timedelta(weeks=duration)
        elif unit == "months":
            result = dt + relativedelta(months=int(duration))
        else:
            return f"Error: unsupported unit {unit!r}"
    except Exception as exc:
        return f"Error computing new datetime: {exc}"

    weekday = _WEEKDAY_NAMES[result.weekday()]
    return (
        f"Result datetime: {result.isoformat(timespec='seconds')}, weekday: {weekday}"
    )


async def _set_reminder(tool_input: dict, db: Session) -> str:
    content = tool_input.get("content", "").strip()
    timestamp = tool_input.get("timestamp", "").strip()

    if not content:
        return "Error: 'content' is required."
    if not timestamp:
        return "Error: 'timestamp' is required."

    try:
        ts_str = timestamp.replace("Z", "+00:00") if timestamp.endswith("Z") else timestamp
        due_at = datetime.fromisoformat(ts_str)
        # Store as naive UTC if tz-aware; otherwise keep as-is (local naive)
        if due_at.tzinfo is not None:
            due_at = due_at.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError as exc:
        return f"Error: could not parse timestamp {timestamp!r}: {exc}"

    reminder = Reminder(
        id=str(uuid.uuid4()),
        content=content,
        due_at=due_at,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)

    weekday = _WEEKDAY_NAMES[due_at.weekday()]
    return (
        f"Reminder set! ID: {reminder.id}. "
        f"You will be reminded to: \"{content}\" "
        f"on {due_at.strftime('%A, %B %-d, %Y at %-I:%M %p')} ({weekday})."
    )


# ── Tool registry / executor ──────────────────────────────────────────────────
# Registry-based dispatch: adding a tool means adding one entry here, not another
# if/elif branch in execute_reminder_tool. Handlers are normalized to a uniform
# async (tool_input, db) -> str signature so the registry can hold a plain
# dict[str, callable] regardless of whether the underlying handler is sync or
# already async.

def _sync_handler(fn):
    """Wrap a sync (tool_input) -> str handler as an async (tool_input, db) -> str."""
    async def wrapper(tool_input: dict, db: Session) -> str:
        return fn(tool_input)
    return wrapper


_REMINDER_TOOL_HANDLERS: dict[str, callable] = {
    "get_current_datetime": _sync_handler(_get_current_datetime),
    "add_duration_to_datetime": _sync_handler(_add_duration_to_datetime),
    "set_reminder": _set_reminder,  # already async (tool_input, db) -> str
}


async def execute_reminder_tool(name: str, tool_input: dict, db: Session) -> str | None:
    """Execute one of the reminder tools via the handler registry.

    Returns a result string on success/error, or None if the tool name is not
    one of the reminder tools (so the caller can fall through to other tools).
    """
    handler = _REMINDER_TOOL_HANDLERS.get(name)
    if handler is None:
        return None
    return await handler(tool_input, db)
