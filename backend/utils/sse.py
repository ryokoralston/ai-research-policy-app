"""Shared SSE queue-to-stream plumbing for the research and debate endpoints.

Producers (background tasks) push fully-formatted SSE strings onto an
asyncio.Queue; this generator relays them to the client, emitting heartbeats
while idle and closing the stream on a terminal event.

Termination is decided by the SSE event NAME ('complete' or 'error') parsed
from the first line — not by substring-matching the JSON payload, which
depended on json.dumps spacing and missed error events whose payload lacked
an event_type field (those streams used to heartbeat forever).
The wire format is unchanged.
"""
import asyncio
from typing import AsyncIterator

HEARTBEAT_EVENT = "event: heartbeat\ndata: {}\n\n"

_TERMINAL_EVENT_NAMES = {"complete", "error"}


def _event_name(event: str) -> str:
    """Extract the SSE event name from a formatted event string."""
    first_line = event.split("\n", 1)[0]
    if first_line.startswith("event: "):
        return first_line[len("event: "):].strip()
    return ""


def is_terminal_event(event: str) -> bool:
    """True for events that end the stream ('complete' / 'error')."""
    return _event_name(event) in _TERMINAL_EVENT_NAMES


async def queue_event_stream(
    queue: asyncio.Queue,
    timeout_seconds: float,
) -> AsyncIterator[str]:
    """Yield SSE strings from `queue` until a terminal event has been relayed.

    Emits a heartbeat whenever no event arrives within `timeout_seconds`
    (keeps proxies from closing the idle connection).
    """
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            yield HEARTBEAT_EVENT
            continue
        yield event
        if is_terminal_event(event):
            return
