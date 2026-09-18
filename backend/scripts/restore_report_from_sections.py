"""Reconstruct a report's `content` from its `report_sections` rows.

Recovers from the bug fixed in commit 3bc5095: report_generator.py's revision
pass could silently adopt a max_tokens-truncated rewrite as the final report
content when the truncated version scored no worse than the original on
citation grounding verification. One production report is known to have hit
this — "AI Governance Risk regarding Autonomous Vehicle", saved with
status="completed" but only 311 words, cut off mid-sentence in the
Background section, citation confidence 2/10.

The invariant this exploits to recover: `ReportSection` rows are written to
the DB DURING generation, before the revision pass ever runs, and are never
re-synced after a revision (see report_generator.py's own comment — the
ReportSection rows saved during the loop above keep the pre-revision text,
they're the generation-time record, not re-synced after a revision). So a
report whose `reports.content` was overwritten with a worse, truncated
rewrite almost always still has the fuller, pre-revision text sitting
untouched in `report_sections`.

Reconstruction reproduces EXACTLY the assembly report_generator.py performs
before any revision pass, using ReportSection.order_index for canonical
order (Report.sections is already ordered by it):

    f"# {report.title}\\n\\n" + "\\n\\n---\\n\\n".join(
        f"## {s.title}\\n\\n{s.content}" for s in report.sections
    )

`_strip_scores_json_lines` is applied to section content before it is saved
during generation, so `s.content` is already exactly what went into the
original `full_content` — no re-processing needed here.

Safety rule: this NEVER writes unless the reconstructed content is strictly
more complete (more words) than what's currently stored, regardless of
--apply. This guards against ever using this recovery path to overwrite a
report with something worse.

READ THIS BEFORE --apply: the revision pass can also legitimately produce
SHORTER text (tightening prose that was fine to begin with). A higher
word count from `report_sections` does not by itself prove a report was hit
by the truncation bug — it only proves restoring won't make the report
shorter. Confirm independently that this report is actually damaged (a
mid-sentence cutoff, a low citation confidence score, a status/word_count
combination that looks wrong) before trusting --apply's word-count
comparison alone. The dry run prints a tail of the current content and the
report's status specifically so that check is easy to do here.

This restores the fuller TEXT only. `Report.metadata_json`'s stored
citation_confidence (if the truncated/revised content was graded by
verify_grounding) is NOT updated by this script — that would need another
LLM call this script does not make, so the persisted score will still
reflect whatever version was actually graded, not the restored text.

Safe by default: running with no flags is a dry run (prints what it would
restore, writes nothing). Pass --apply to actually write.

Run from the backend/ directory:
    ./venv/bin/python -m scripts.restore_report_from_sections --report-id <id>
    ./venv/bin/python -m scripts.restore_report_from_sections --report-id <id> --apply

This also works unmodified from a Render Shell against production, using the
same invocation.
"""
import argparse
import os
import sys
from datetime import datetime

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from config import get_settings
from database import SessionLocal
from models import Report

# How much of the current content's tail to print in the dry-run / no-write
# output — enough to eyeball a mid-sentence cutoff without flooding the
# terminal with the whole report.
TAIL_PREVIEW_CHARS = 200


def _print_section_table(sections) -> None:
    print(f"\n  {'order':<6}{'key':<24}{'title':<40}{'words':>8}")
    for s in sections:
        print(f"  {s.order_index:<6}{s.section_key:<24}{s.title:<40}{len(s.content.split()):>8}")


def restore_report(db, report_id: str, apply: bool) -> dict:
    """Reconstruct `report_id`'s content from its ReportSection rows.

    Only ever writes when `apply` is True AND the reconstructed content is
    strictly more complete (more words) than what's currently stored. Never
    calls sys.exit — returns a result dict so both the CLI and tests can
    inspect the outcome. `status` is one of:
        "not_found" | "no_sections" | "not_improved" | "dry_run" | "restored"
    """
    report = db.get(Report, report_id)
    if report is None:
        print(f"ERROR: no report found with id {report_id!r}")
        return {"status": "not_found"}

    print(f"Report:      {report.id}")
    print(f"Title:       {report.title}")
    print(f"Status:      {report.status}")

    sections = report.sections  # relationship is already ordered by order_index
    if not sections:
        print("\nNo report_sections rows for this report — nothing to restore from.")
        return {"status": "no_sections"}

    order_indexes = [s.order_index for s in sections]
    if len(set(order_indexes)) != len(order_indexes):
        print(
            f"\nERROR: duplicate order_index values in report_sections {order_indexes} — "
            "refusing to guess an assembly order. Nothing written."
        )
        return {"status": "duplicate_order_index"}

    _print_section_table(sections)

    restored = f"# {report.title}\n\n" + "\n\n---\n\n".join(
        f"## {s.title}\n\n{s.content}" for s in sections
    )

    current_content = report.content or ""
    current_words = len(current_content.split())
    restored_words = len(restored.split())
    print(f"\nCurrent content:   {current_words} words")
    print(f"Restored content:  {restored_words} words")
    print(f"Current content tail: ...{current_content[-TAIL_PREVIEW_CHARS:]!r}")

    if restored_words <= current_words:
        print(
            "\nRestoration would not improve completeness (current content is "
            "already as long or longer). Nothing written."
        )
        return {
            "status": "not_improved",
            "current_words": current_words,
            "restored_words": restored_words,
        }

    if not apply:
        print(
            f"\nDry run: content would grow from {current_words} to {restored_words} words. "
            "Nothing has been written.\n"
            "Confirm this report is actually the damaged one (mid-sentence cutoff, "
            "low citation confidence — a higher section word count alone does not "
            "prove truncation) before re-running with --apply:\n"
            f"    ./venv/bin/python -m scripts.restore_report_from_sections "
            f"--report-id {report_id} --apply"
        )
        return {
            "status": "dry_run",
            "current_words": current_words,
            "restored_words": restored_words,
        }

    # Note: report.metadata_json's citation_confidence (if present) is
    # intentionally left untouched. It reflects the grade verify_grounding
    # gave to the truncated/revised content that is being replaced here.
    # Restoring the fuller pre-revision text does not re-run grounding
    # verification — that would require another LLM call this script does
    # not make — so the stored score may now understate (or overstate) how
    # well-grounded the restored text actually is.
    report.content = restored
    report.word_count = restored_words
    report.updated_at = datetime.utcnow()
    db.commit()

    # Verify by re-read rather than trusting the write's own return value, so
    # a partial/failed write surfaces here instead of on the next page load.
    db.expire_all()
    reread = db.get(Report, report_id)
    print(f"\nWrote restored content. Persisted word_count: {reread.word_count}")
    if reread.word_count != restored_words or reread.content != restored:
        return {
            "status": "verification_failed",
            "current_words": current_words,
            "restored_words": restored_words,
            "persisted_words": reread.word_count,
        }
    print("Verified: write landed as expected.")

    return {
        "status": "restored",
        "current_words": current_words,
        "restored_words": restored_words,
        "persisted_words": reread.word_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--report-id", required=True, help="Report.id to restore")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the restored content (default: dry run)",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Database:    {settings.database_url}\n")

    db = SessionLocal()
    try:
        result = restore_report(db, args.report_id, args.apply)
    finally:
        db.close()

    if result["status"] == "not_found":
        sys.exit(1)
    if result["status"] == "duplicate_order_index":
        sys.exit(1)
    if result["status"] == "verification_failed":
        sys.exit(
            "VERIFICATION FAILED: persisted content/word_count does not match what "
            "was written. Investigate before trusting this report."
        )


if __name__ == "__main__":
    main()
