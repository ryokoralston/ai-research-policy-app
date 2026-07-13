"""Data Lab: upload a data file (CSV/JSON/XLSX/TXT), let Claude analyze it with
the server-side code execution tool, and stream back the analysis text, the
code Claude ran, its stdout, and any generated charts.

Flow per request:
  1. Upload the file via the Files API (upload_analysis_file) -> file_id.
  2. Send a message referencing the file via a `container_upload` content
     block, with the `code_execution` server tool enabled
     (analyze_data_stream). Claude runs pandas/matplotlib etc. in an
     Anthropic-hosted sandbox; text and code results stream back as SSE
     events.
  3. Any files Claude's code wrote (e.g. PNG charts) are downloaded via the
     Files API and re-encoded as base64 `image` events.
  4. The uploaded *input* file is deleted afterward (best effort) — the
     analysis is one-shot, so there's no reason to keep it around.

Mirrors the SSE service style used by services/document_qa.py: a module-level
SYSTEM_PROMPT, a `sse_event`-based generator, pure helpers factored out for
unit testing (see tests/test_data_analysis.py), and a usage log line at the
end of the request.
"""
import base64
import logging
import os
from typing import AsyncIterator

import anthropic

from services.anthropic_client import sse_event, _load_ai_settings, _get_anthropic_client, _block_get

logger = logging.getLogger(__name__)

# ── Upload constraints ────────────────────────────────────────────────────────
MAX_ANALYSIS_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_ANALYSIS_EXTENSIONS = {".csv", ".json", ".xlsx", ".txt"}

# Files API beta header — required on the messages call that references an
# uploaded file via container_upload (upload/download/delete go through
# client.beta.files.* instead, which sets this automatically).
_FILES_API_BETA_HEADER = {"anthropic-beta": "files-api-2025-04-14"}

_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
}

# Code execution tool type — try the current GA type first, fall back to the
# older REPL-persistence variant if the API rejects it (e.g. on an account/
# model combination that hasn't rolled out the newer type yet).
_CODE_EXECUTION_TOOL_TYPES = ["code_execution_20260521", "code_execution_20260120"]

MAX_CONTINUATIONS = 5  # cap on pause_turn re-requests
STDOUT_TRIM_CHARS = 4000
MAX_IMAGES = 3
MAX_IMAGE_DECODED_BYTES = 2 * 1024 * 1024  # 2 MB

SYSTEM_PROMPT = (
    "You are a rigorous data analyst. You have been given a data file to "
    "analyze using the code execution tool (Python: pandas, numpy, "
    "matplotlib, seaborn, scikit-learn, statsmodels are available). "
    "Explore the data, run appropriate statistical analysis, and answer the "
    "user's question thoroughly. Save any plots you create as PNG files so "
    "they can be retrieved. Describe your findings clearly in plain text, "
    "referencing the specific numbers you computed."
)


# ── Pure helpers (unit-testable — see tests/test_data_analysis.py) ───────────

def _content_type_for(filename: str) -> str | None:
    """Map a filename's extension to the Files API content type. Returns
    None for unsupported extensions (including no extension at all)."""
    ext = os.path.splitext(filename)[1].lower()
    return _CONTENT_TYPES.get(ext)


def _extract_code_blocks(content) -> list[str]:
    """Pull the code/command string out of every server_tool_use block in a
    final message's content list. Works with SDK objects or plain dicts.

    The code execution tool's server_tool_use blocks carry the code under
    either "command" (bash_code_execution) or "code" (other sub-tools) in
    `.input`; fall back to a stringified `.input` if neither key is present
    so nothing is silently dropped.
    """
    codes: list[str] = []
    for block in content or []:
        if _block_get(block, "type") != "server_tool_use":
            continue
        tool_input = _block_get(block, "input") or {}
        code = None
        if isinstance(tool_input, dict):
            code = tool_input.get("command") or tool_input.get("code")
        if not code:
            code = str(tool_input)
        codes.append(code)
    return codes


def _extract_stdout_results(content) -> list[dict]:
    """Pull {"text", "return_code"} out of every bash_code_execution_tool_result
    block. `text` is the result's stdout, trimmed to STDOUT_TRIM_CHARS. Error
    results (no stdout) surface their error_code as `text` with return_code
    None, rather than being dropped."""
    results: list[dict] = []
    for block in content or []:
        if _block_get(block, "type") != "bash_code_execution_tool_result":
            continue
        result = _block_get(block, "content")
        result_type = _block_get(result, "type")
        if result_type == "bash_code_execution_result":
            stdout = _block_get(result, "stdout") or ""
            if len(stdout) > STDOUT_TRIM_CHARS:
                stdout = stdout[:STDOUT_TRIM_CHARS] + "\n… (truncated)"
            results.append({
                "text": stdout,
                "return_code": _block_get(result, "return_code"),
            })
        else:
            # Tool-level error (e.g. bash_code_execution_tool_result_error) —
            # surface the error code rather than dropping the event.
            error_code = _block_get(result, "error_code")
            results.append({"text": f"[tool error: {error_code}]", "return_code": None})
    return results


def _extract_output_file_ids(content) -> list[str]:
    """Collect file_ids of any files the code execution tool wrote, from the
    nested content list inside each successful bash_code_execution_tool_result
    block."""
    file_ids: list[str] = []
    for block in content or []:
        if _block_get(block, "type") != "bash_code_execution_tool_result":
            continue
        result = _block_get(block, "content")
        if _block_get(result, "type") != "bash_code_execution_result":
            continue
        for item in _block_get(result, "content") or []:
            if _block_get(item, "type") == "bash_code_execution_output":
                fid = _block_get(item, "file_id")
                if fid:
                    file_ids.append(fid)
    return file_ids


def _is_unknown_tool_type_error(exc: Exception) -> bool:
    """Heuristic: does this BadRequestError look like the API rejecting the
    code-execution tool `type` string (vs. some other 400, e.g. a bad
    question or oversized file) — used to decide whether to retry with the
    fallback tool version."""
    message = str(exc).lower()
    return "type" in message and ("tool" in message or "code_execution" in message)


# ── Files API ─────────────────────────────────────────────────────────────────

async def upload_analysis_file(file_bytes: bytes, filename: str) -> str:
    """Upload a data file to the Anthropic Files API. Returns the file_id."""
    content_type = _content_type_for(filename)
    if content_type is None:
        raise ValueError(f"Unsupported file extension: {filename}")

    ai_settings = _load_ai_settings()
    client = _get_anthropic_client(ai_settings)
    uploaded = await client.beta.files.upload(file=(filename, file_bytes, content_type))
    return uploaded.id


# ── Analysis stream ───────────────────────────────────────────────────────────

async def analyze_data_stream(file_id: str, question: str, filename: str) -> AsyncIterator[str]:
    """SSE generator: analyze the uploaded file (`file_id`) with Claude's code
    execution tool. See module docstring for event contract.
    """
    ai_settings = _load_ai_settings()
    model = ai_settings["main_model"]
    client = _get_anthropic_client(ai_settings)

    yield sse_event("start", {"filename": filename, "question": question})

    messages: list[dict] = [{
        "role": "user",
        "content": [
            {"type": "container_upload", "file_id": file_id},
            {"type": "text", "text": question},
        ],
    }]

    full_text = ""
    total_code_runs = 0
    image_filenames: list[str] = []
    tool_candidates = list(_CODE_EXECUTION_TOOL_TYPES)
    chosen_tool_type: str | None = None
    continuations = 0

    try:
        while True:
            tool_type = chosen_tool_type or tool_candidates[0]
            tools = [{"type": tool_type, "name": "code_execution"}]

            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=tools,
                    extra_headers=_FILES_API_BETA_HEADER,
                ) as stream:
                    async for event in stream:
                        if getattr(event, "type", None) == "text":
                            text = event.text or ""
                            if text:
                                full_text += text
                                yield sse_event("token", {"text": text})
                    final = await stream.get_final_message()
            except anthropic.BadRequestError as exc:
                if (
                    chosen_tool_type is None
                    and len(tool_candidates) > 1
                    and _is_unknown_tool_type_error(exc)
                ):
                    # First turn, no tokens streamed yet — safe to retry with
                    # the fallback tool version.
                    logger.info("code execution tool type %s rejected, retrying with fallback", tool_type)
                    tool_candidates.pop(0)
                    continue
                raise

            chosen_tool_type = tool_type

            u = getattr(final, "usage", None)
            if u is not None:
                logger.info(
                    "data-lab usage: input=%s cache_read=%s cache_write=%s output=%s",
                    u.input_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens,
                )

            for code in _extract_code_blocks(final.content):
                total_code_runs += 1
                yield sse_event("code", {"code": code})

            for result in _extract_stdout_results(final.content):
                yield sse_event("stdout", result)

            for fid in _extract_output_file_ids(final.content):
                if len(image_filenames) >= MAX_IMAGES:
                    yield sse_event("note", {
                        "message": f"Reached the maximum of {MAX_IMAGES} images — additional generated files were not downloaded.",
                    })
                    break
                try:
                    meta = await client.beta.files.retrieve_metadata(fid)
                except Exception:
                    logger.warning("Failed to fetch metadata for generated file %s", fid, exc_info=True)
                    continue
                mime_type = getattr(meta, "mime_type", None) or ""
                fname = getattr(meta, "filename", None) or f"{fid}.png"
                if not mime_type.startswith("image/"):
                    continue
                try:
                    downloaded = await client.beta.files.download(fid)
                    raw = await downloaded.read()
                except Exception:
                    logger.warning("Failed to download generated file %s", fid, exc_info=True)
                    continue
                if len(raw) > MAX_IMAGE_DECODED_BYTES:
                    yield sse_event("note", {
                        "message": f"Skipped {fname} — exceeds the {MAX_IMAGE_DECODED_BYTES // (1024 * 1024)}MB image size limit.",
                    })
                    continue
                image_filenames.append(fname)
                yield sse_event("image", {
                    "filename": fname,
                    "media_type": mime_type,
                    "data_base64": base64.standard_b64encode(raw).decode(),
                })

            if final.stop_reason == "pause_turn":
                continuations += 1
                if continuations > MAX_CONTINUATIONS:
                    yield sse_event("error", {
                        "message": f"Analysis exceeded the maximum of {MAX_CONTINUATIONS} continuations.",
                    })
                    return
                # Resume server-side tool work: append the raw assistant
                # content and re-send as-is — no extra user message.
                messages.append({"role": "assistant", "content": final.content})
                continue

            break

        yield sse_event("complete", {
            "answer": full_text,
            "images": image_filenames,
            "code_runs": total_code_runs,
        })
    except Exception as exc:
        logger.exception("Data Lab analysis failed")
        yield sse_event("error", {"message": str(exc)})
    finally:
        try:
            await client.beta.files.delete(file_id)
        except Exception:
            logger.warning("Failed to delete uploaded analysis file %s", file_id, exc_info=True)
