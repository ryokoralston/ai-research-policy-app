"""Find (and optionally delete) search-index entries with no backing chunk row.

The invariant this enforces: no ChromaDB entry and no BM25 (SQLite FTS5) entry
may exist without a `document_chunks` row behind it. An entry that has lost its
row is an ORPHAN — still retrievable and still citable, but quoting text that no
longer exists anywhere in the app. The full rationale, and how orphans get
created, is in rag/reconcile.py's module docstring.

On 2026-08-19 the dev database had 954 of 976 indexed chunks in exactly this
state, after chunk rows were deleted directly in SQL — a path no endpoint
hardening can cover, which is why this script exists.

Safe by default: running with no flags is a dry run (prints what it would
delete, writes nothing). Pass --apply to actually delete.

READ THIS BEFORE --apply: orphans are defined against the database, so this
script trusts DATABASE_URL completely. If it points at an empty or wrong
database while the indexes are intact, EVERY entry looks like an orphan and
--apply erases both indexes. This app has already lost its Render env vars once
(DATABASE_URL included). Check the "SQLite chunk ids" line below against what
you expect the library to hold before applying anything.

Run from the backend/ directory:
    ./venv/bin/python -m scripts.reconcile_indexes
    ./venv/bin/python -m scripts.reconcile_indexes --apply
"""
import argparse
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from config import get_settings
from database import SessionLocal
from rag.reconcile import delete_orphans, find_orphans

# How many orphan ids to print in a dry run before truncating — enough to
# eyeball whether they look like real chunk ids, not so many that a badly
# pointed DATABASE_URL floods the terminal with thousands of lines.
SAMPLE_SIZE = 10


def _print_sample(label: str, orphan_ids: list[str]) -> None:
    if not orphan_ids:
        return
    print(f"\n  sample {label} orphan ids:")
    for chunk_id in orphan_ids[:SAMPLE_SIZE]:
        print(f"    {chunk_id}")
    if len(orphan_ids) > SAMPLE_SIZE:
        print(f"    ... and {len(orphan_ids) - SAMPLE_SIZE} more")


def reconcile(apply: bool) -> None:
    settings = get_settings()
    print(f"Database:    {settings.database_url}")
    print(f"Chroma dir:  {settings.chroma_persist_dir}")
    print(f"BM25 index:  {settings.bm25_index_path}\n")

    db = SessionLocal()
    try:
        report = find_orphans(db)

        chroma_orphans = report["chroma_orphans"]
        bm25_orphans = report["bm25_orphans"]

        print(f"  SQLite chunk ids:   {report['sqlite_chunk_ids']}")
        print(f"  Chroma entries:     {report['chroma_total']}  ({len(chroma_orphans)} orphaned)")
        print(f"  BM25 entries:       {report['bm25_total']}  ({len(bm25_orphans)} orphaned)")

        if not chroma_orphans and not bm25_orphans:
            print("\nNo orphans: every index entry has a backing document_chunks row.")
            return

        _print_sample("Chroma", chroma_orphans)
        _print_sample("BM25", bm25_orphans)

        if not apply:
            print(
                f"\nDry run: {len(chroma_orphans)} Chroma and {len(bm25_orphans)} BM25 "
                "entr(ies) would be deleted. Nothing has been written.\n"
                "Confirm the SQLite chunk-id count above matches the library you expect, "
                "then re-run with --apply."
            )
            return

        result = delete_orphans(db)
        print(
            f"\nDeleted {result['chroma_removed']} Chroma id(s) and "
            f"{result['bm25_removed']} BM25 row(s)."
        )
        print(f"  Chroma entries now: {result['chroma_total_after']}")
        print(f"  BM25 entries now:   {result['bm25_total_after']}")
        print(f"  SQLite chunk ids:   {result['sqlite_chunk_ids']}")

        # Re-read rather than trusting the delete's own return value, so a
        # partial removal surfaces here instead of on the next retrieval.
        recheck = find_orphans(db)
        remaining = len(recheck["chroma_orphans"]) + len(recheck["bm25_orphans"])
        if remaining:
            sys.exit(
                f"VERIFICATION FAILED: {remaining} orphan(s) still present after the delete. "
                "Investigate before trusting the indexes."
            )
        print("\nVerified: no orphans remain.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete orphans (default: dry run)"
    )
    args = parser.parse_args()
    reconcile(args.apply)


if __name__ == "__main__":
    main()
