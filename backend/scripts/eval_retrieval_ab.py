"""A/B retrieval eval for Contextual Retrieval (rag/contextualizer.py).

Two subcommands:

  generate --out FILE.json --n 20 --seed 42
      Sample N DocumentChunk rows (seeded random, chunks < 200 chars
      excluded — too short to support a specific factual question), ask the
      fast model to write one specific factual question each chunk answers,
      and save [{chunk_id, doc_id, question}, ...] to FILE.json.

  eval --questions FILE.json --top-k 5
      For each saved question, run Retriever().retrieve(question, top_k=5)
      and report chunk-level hit rate (expected chunk_id present in the
      results) and doc-level hit rate (expected doc_id present), plus a line
      per miss, ending in one summary line:
        chunk_hit=X/N (P%) doc_hit=Y/N (Q%)
      so two runs (before/after a contextualize_reindex.py backfill) are
      easy to diff.

Run from the backend/ directory:
    ./venv/bin/python -m scripts.eval_retrieval_ab generate --out /tmp/q.json --n 20 --seed 42
    ./venv/bin/python -m scripts.eval_retrieval_ab eval --questions /tmp/q.json --top-k 5

Not run against real data by this task — the main session runs the measured
A/B before/after the real backfill.
"""
import argparse
import asyncio
import json
import os
import random
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database import SessionLocal
from models import Document, DocumentChunk
from services.anthropic_client import generate_text

MIN_CHUNK_CHARS = 200

QUESTION_PROMPT = (
    "Write one specific factual question that this passage answers and that "
    "someone searching a policy document library might plausibly ask. "
    "Respond with the question only.\n\n"
    "<passage>\n{content}\n</passage>"
)


# ── generate ─────────────────────────────────────────────────────────────────

def _sample_chunks(n: int, seed: int) -> list[DocumentChunk]:
    db = SessionLocal()
    try:
        chunks = db.query(DocumentChunk).all()
    finally:
        db.close()
    eligible = [c for c in chunks if len(c.content) >= MIN_CHUNK_CHARS]
    rng = random.Random(seed)
    return rng.sample(eligible, k=min(n, len(eligible)))


async def generate_questions(out_path: str, n: int, seed: int) -> None:
    sampled = _sample_chunks(n, seed)
    if not sampled:
        print("No eligible chunks found (need chunks >= "
              f"{MIN_CHUNK_CHARS} chars). Nothing to generate.")
        return

    print(f"Sampled {len(sampled)} chunk(s) (seed={seed}). Generating questions...")
    results = []
    for i, chunk in enumerate(sampled, start=1):
        question = await generate_text(
            QUESTION_PROMPT.format(content=chunk.content),
            temperature=1.0,
        )
        question = question.strip()
        results.append({
            "chunk_id": chunk.chroma_id or chunk.id,
            "doc_id": chunk.document_id,
            "question": question,
        })
        print(f"  {i}/{len(sampled)}: {question!r}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {len(results)} questions to {out_path}")


# ── eval ─────────────────────────────────────────────────────────────────────

def run_eval(questions_path: str, top_k: int) -> None:
    from rag.retriever import Retriever

    with open(questions_path, encoding="utf-8") as f:
        questions = json.load(f)
    if not questions:
        print("No questions in file. Nothing to evaluate.")
        return

    db = SessionLocal()
    try:
        doc_titles = {d.id: (d.title or d.filename) for d in db.query(Document).all()}
    finally:
        db.close()

    retriever = Retriever()
    chunk_hits = 0
    doc_hits = 0
    misses = []

    for i, item in enumerate(questions, start=1):
        results = retriever.retrieve(item["question"], top_k=top_k)
        result_chunk_ids = {c.chunk_id for c in results}
        result_doc_ids = {c.doc_id for c in results}

        chunk_hit = item["chunk_id"] in result_chunk_ids
        doc_hit = item["doc_id"] in result_doc_ids
        if chunk_hit:
            chunk_hits += 1
        if doc_hit:
            doc_hits += 1
        if not chunk_hit:
            title = doc_titles.get(item["doc_id"], "Unknown")
            misses.append(f"  MISS [{i}/{len(questions)}] {item['question']!r} — expected doc {title!r}")

        print(f"  {i}/{len(questions)}: chunk_hit={chunk_hit} doc_hit={doc_hit} — {item['question']!r}")

    print()
    for line in misses:
        print(line)

    n = len(questions)
    chunk_pct = 100 * chunk_hits / n
    doc_pct = 100 * doc_hits / n
    print(
        f"\nchunk_hit={chunk_hits}/{n} ({chunk_pct:.1f}%) "
        f"doc_hit={doc_hits}/{n} ({doc_pct:.1f}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Sample chunks and generate ground-truth questions")
    gen.add_argument("--out", required=True, help="Output JSON file path")
    gen.add_argument("--n", type=int, default=20, help="Number of questions to generate")
    gen.add_argument("--seed", type=int, default=42, help="Random seed for chunk sampling")

    ev = sub.add_parser("eval", help="Run retrieval against saved questions and report hit rates")
    ev.add_argument("--questions", required=True, help="Path to a generate-produced JSON file")
    ev.add_argument("--top-k", type=int, default=5, help="top_k passed to Retriever.retrieve")

    args = parser.parse_args()
    if args.command == "generate":
        asyncio.run(generate_questions(args.out, args.n, args.seed))
    elif args.command == "eval":
        run_eval(args.questions, args.top_k)


if __name__ == "__main__":
    main()
