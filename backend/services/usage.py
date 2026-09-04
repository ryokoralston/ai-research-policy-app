"""Records what each provider call consumed, so feature cost is measurable.

Why a ContextVar rather than a parameter: the provider calls happen deep inside
services/anthropic_client.py, several layers below the router that knows who is
signed in — threading a user id through research_agent, report generation, risk
analysis and the rest would touch every signature on the way down for a concern
none of them care about. `usage_context()` sets it once at the entry point.

Failure to record is never allowed to break the request it was measuring: this
is accounting, and losing a row is much cheaper than failing a research run the
user is watching stream.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageContext:
    user_id: str | None
    feature: str


_context: ContextVar[UsageContext | None] = ContextVar("usage_context", default=None)


@contextmanager
def usage_context(user_id: str | None, feature: str):
    """Attribute provider calls made inside this block to `user_id`/`feature`.

    Background tasks need their own `with usage_context(...)`: a task started
    with BackgroundTasks does not reliably inherit the request's context, and
    inheriting silently would be worse than not inheriting at all — usage would
    be attributed to whoever happened to be in context.

    When an async generator using this context is finalized from a different
    Context (e.g., an HTTP client disconnects mid-stream and aclose() runs in
    another task), reset() raises ValueError. That case is tolerated because
    the original context is gone anyway, and usage rows were already written
    before the yield — there is no data loss or misattribution.
    """
    token = _context.set(UsageContext(user_id=user_id, feature=feature))
    try:
        yield
    finally:
        try:
            _context.reset(token)
        except ValueError:
            # The generator that entered this block was finalized from a
            # different Context (client disconnected mid-stream and aclose()
            # ran elsewhere). That foreign context never had our value, so
            # there is nothing to restore; the original context is gone.
            pass


def current_feature() -> str | None:
    ctx = _context.get()
    return ctx.feature if ctx else None


def record_anthropic(model: str, usage) -> None:
    """Record one Anthropic call. `usage` is the SDK's usage object.

    Tolerates `None` and reads every field defensively: the shape varies by
    endpoint, and a response that doesn't carry usage must cost us a row, not
    the request itself.
    """
    if usage is None:
        logger.debug("usage not recorded: response carried no usage (model=%s)", model)
        return
    _record(
        provider="anthropic",
        model=model,
        input_tokens=_int(getattr(usage, "input_tokens", 0)),
        output_tokens=_int(getattr(usage, "output_tokens", 0)),
        cache_read_input_tokens=_int(getattr(usage, "cache_read_input_tokens", 0)),
        cache_creation_input_tokens=_int(getattr(usage, "cache_creation_input_tokens", 0)),
        request_count=1,
    )


def record_tavily(searches: int) -> None:
    """Record Tavily searches — billed per search, so there are no tokens."""
    _record(provider="tavily", model=None, request_count=searches)


def _int(value) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _record(
    *,
    provider: str,
    model: str | None,
    request_count: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> None:
    ctx = _context.get()
    if ctx is None:
        # A provider call outside any entry point that set a context — a script,
        # a test, or an entry point that hasn't been wired up yet. Recording it
        # with no feature would produce rows that can't be attributed to
        # anything, so drop it and say so once, loudly enough to find.
        logger.debug("usage not recorded: no usage_context set (provider=%s)", provider)
        return

    # Imported here: services are imported at module load by the app, and
    # importing the DB session at that point pulls the engine in earlier than
    # the rest of the service layer expects.
    from database import SessionLocal
    from models.usage_event import UsageEvent

    try:
        with SessionLocal() as db:
            db.add(
                UsageEvent(
                    user_id=ctx.user_id,
                    feature=ctx.feature,
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_input_tokens=cache_read_input_tokens,
                    cache_creation_input_tokens=cache_creation_input_tokens,
                    request_count=request_count,
                )
            )
            db.commit()
    except Exception:
        logger.exception("Failed to record usage event (provider=%s, feature=%s)", provider, ctx.feature)
