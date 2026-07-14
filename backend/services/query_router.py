"""Routing workflow (Anthropic "Routing" pattern): classify an incoming chat
question with a cheap, low-token Claude call, then hand back per-category
response-style guidance that rag_service.answer_question folds into the
system prompt. Tools available to the model are unaffected by the route —
misclassification degrades style, not capability.
"""
import string

from services.anthropic_client import generate_text

# Ordered so build_router_prompt's <categories> block lists them in a stable,
# reviewable order. "general" is last — the catch-all / default.
QUERY_CATEGORIES: dict[str, dict] = {
    "factual_lookup": {
        "description": "specific fact, definition, date, number, or detail question",
        "guidance": (
            "Answer in 2-4 tight sentences, no preamble, cite every claim with its [N] number."
        ),
    },
    "synthesis_overview": {
        "description": "asks to summarize, give an overview, or explain a broad topic across sources",
        "guidance": (
            "Structure the answer (short bullets or bolded mini-headings), synthesize across "
            "multiple sources rather than quoting one, explicitly note coverage gaps."
        ),
    },
    "comparison": {
        "description": "asks to compare/contrast positions, documents, policies, or jurisdictions",
        "guidance": (
            "Organize by comparison dimensions giving each side parallel treatment, cite each "
            "side's sources, call out where sources directly disagree."
        ),
    },
    "current_events": {
        "description": "asks about recent news or developments likely newer than the document library",
        "guidance": (
            "Use web_search FIRST for recency, then check the library for background; clearly "
            "separate web-sourced from library-sourced statements and include dates."
        ),
    },
    "task_action": {
        "description": "asks to set a reminder, draft/edit a file, or perform an action rather than answer a question",
        "guidance": (
            "Perform the task with the appropriate tools, confirm concretely what was done "
            "(times, filenames), keep exposition minimal."
        ),
    },
    "general": {
        "description": "greetings, meta questions about the assistant, or anything not fitting above",
        # Empty — the default system prompt already covers this case.
        "guidance": "",
    },
}

# Characters parse_category strips from both ends before matching — covers
# trailing punctuation, wrapping quotes/backticks, and stray whitespace that a
# fast/cheap classification call tends to emit around the bare category key.
_STRIP_CHARS = string.punctuation + " \t\n\r"


def build_router_prompt(question: str, history_snippet: str = "") -> str:
    """Pure prompt builder for the routing call. XML-tags the question (and,
    when non-empty, a recent-conversation snippet for interpreting
    follow-ups) plus the category list, and instructs a single bare key back.
    """
    history_block = ""
    if history_snippet:
        history_block = (
            "<recent_conversation>\n"
            "Context for interpreting follow-up questions:\n"
            f"{history_snippet}\n"
            "</recent_conversation>\n\n"
        )

    categories_block = "\n".join(
        f"{key}: {info['description']}" for key, info in QUERY_CATEGORIES.items()
    )

    return (
        f"{history_block}"
        f"<question>\n{question}\n</question>\n\n"
        "<categories>\n"
        f"{categories_block}\n"
        "</categories>\n\n"
        "Classify the question in <question> into exactly one of the categories above. "
        "Respond with exactly one category key from the list and nothing else."
    )


def _block_text(content) -> str:
    """Extract plain text from a chat message's content field, which is
    either a plain string or a list of block dicts. Only {"type": "text"}
    blocks are collected — tool_use/tool_result blocks carry no prose worth
    feeding to the router and are skipped.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def history_snippet(chat_history: list[dict] | None, max_chars: int = 500) -> str:
    """Plain-text snippet of the most recent user and assistant turns, for
    giving the router just enough context to classify follow-up questions
    ("What about the EU?" needs the prior turn to route sensibly).

    Truncates from the END so the most recent, most relevant text survives.
    """
    if not chat_history:
        return ""

    last_user: str | None = None
    last_assistant: str | None = None
    for message in reversed(chat_history):
        role = message.get("role")
        if role == "user" and last_user is None:
            last_user = _block_text(message.get("content"))
        elif role == "assistant" and last_assistant is None:
            last_assistant = _block_text(message.get("content"))
        if last_user is not None and last_assistant is not None:
            break

    parts = []
    if last_user:
        parts.append(f"User: {last_user}")
    if last_assistant:
        parts.append(f"Assistant: {last_assistant}")
    snippet = "\n".join(parts)

    if len(snippet) > max_chars:
        snippet = snippet[-max_chars:]
    return snippet


def parse_category(raw: str) -> str:
    """Normalize the router's raw response into a category key. Never
    raises — the caller falls back to "general" on any malformed input.

    Exact match (after lowering/stripping wrapping punctuation) wins. Failing
    that, if exactly one category key appears as a substring of the response
    (e.g. "The category is comparison."), use it. Anything else — garbage,
    or a response that names more than one key — defaults to "general".
    """
    try:
        normalized = (raw or "").strip().lower().strip(_STRIP_CHARS)
    except Exception:
        return "general"

    if normalized in QUERY_CATEGORIES:
        return normalized

    matches = [key for key in QUERY_CATEGORIES if key in normalized]
    if len(matches) == 1:
        return matches[0]
    return "general"


async def route_query(question: str, chat_history: list[dict] | None = None) -> str:
    """Classify `question` into a QUERY_CATEGORIES key via a fast, cheap,
    deterministic Claude call (fast_model default, temperature=0).

    Any failure — API error, timeout, unexpected response shape — falls back
    to "general" rather than propagating: a misrouted or failed classification
    must never break the chat turn it's only meant to style.
    """
    try:
        prompt = build_router_prompt(question, history_snippet(chat_history))
        raw = await generate_text(prompt, temperature=0.0, max_tokens=16)
        return parse_category(raw)
    except Exception:
        return "general"


def guidance_for(category: str) -> str:
    """Response-style guidance for a category; "" for unknown/general."""
    return QUERY_CATEGORIES.get(category, {}).get("guidance", "")
