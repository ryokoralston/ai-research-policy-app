"""Tests for services/persona_service.py — the built-in + custom persona merge.

Covers: get_all_personas returns all 10 built-ins with is_custom=False; a
created CustomPersona row appears with is_custom=True and a correctly
composed system prompt; shape is uniform across both. Also direct tests of
the pure _build_custom_persona_system composer and the key-derivation /
collision-validation helpers used by the admin create endpoint.

Run from the backend directory:
    ./venv/bin/python -m tests.test_persona_service
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.custom_persona import CustomPersona
from templates.personas import PERSONAS
import services.persona_service as persona_service
from services.persona_service import (
    _build_custom_persona_system,
    _summarize_priorities,
    assign_custom_color,
    derive_key,
    get_all_personas,
    validate_new_key,
)

_UNIFORM_KEYS = {"key", "name", "title", "initials", "system", "bio", "color", "text_color", "is_custom"}


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_custom_row(**overrides) -> CustomPersona:
    defaults = dict(
        key="vp_engineering",
        name="Priya Sharma",
        title="VP of Engineering",
        initials="PS",
        color="bg-indigo-600",
        priorities="Shipping reliable systems on schedule and keeping the team's engineering headcount cost under control.",
        style="Blunt and direct. Wants concrete numbers before agreeing to anything and pushes back hard on unquantified risk claims.",
        created_by="admin-user-id",
    )
    defaults.update(overrides)
    return CustomPersona(**defaults)


def test_get_all_personas_includes_all_builtins():
    db = _make_db()
    try:
        merged = get_all_personas(db)
        for key, p in PERSONAS.items():
            assert key in merged, f"built-in {key} missing from merged personas"
            entry = merged[key]
            assert entry["is_custom"] is False
            assert entry["name"] == p["name"]
            assert entry["system"] == p["system"]
            assert entry["bio"] == p["bio"]
            assert entry["bio"], "every built-in must have a non-empty bio"
            assert set(entry.keys()) == _UNIFORM_KEYS
    finally:
        db.close()


def test_get_all_personas_includes_custom_row():
    db = _make_db()
    try:
        row = _make_custom_row()
        db.add(row)
        db.commit()

        merged = get_all_personas(db)
        assert "vp_engineering" in merged
        entry = merged["vp_engineering"]
        assert entry["is_custom"] is True
        assert entry["name"] == "Priya Sharma"
        assert entry["title"] == "VP of Engineering"
        assert set(entry.keys()) == _UNIFORM_KEYS
        # Composed system prompt actually reflects the persona.
        assert "Priya Sharma" in entry["system"]
        assert "VP of Engineering" in entry["system"]
        assert entry["system"].endswith("You never start your response by introducing yourself.")
        # bio synthesized from priorities, non-empty
        assert entry["bio"]
    finally:
        db.close()


def test_get_all_personas_shape_uniform_across_builtin_and_custom():
    db = _make_db()
    try:
        db.add(_make_custom_row())
        db.commit()
        merged = get_all_personas(db)
        shapes = {frozenset(v.keys()) for v in merged.values()}
        assert len(shapes) == 1, f"inconsistent shapes across entries: {shapes}"
        assert shapes.pop() == _UNIFORM_KEYS
    finally:
        db.close()


def test_build_custom_persona_system_pure_function():
    row = _make_custom_row(
        name="Jordan Lee",
        title="Chief Risk Officer",
        priorities="Regulatory exposure, downside tail risk, and audit trail completeness above all else.",
        style="Terse, skeptical, always asks 'what's the worst case' before signing off.",
    )
    system = _build_custom_persona_system(row)

    assert "Jordan Lee" in system
    assert "Chief Risk Officer" in system
    assert "Regulatory exposure" in system
    assert "worst case" in system
    assert system.endswith("You never start your response by introducing yourself.")


def test_summarize_priorities_short_and_long():
    assert _summarize_priorities("Ships fast. Cares about velocity.") == "Ships fast."
    long_text = "This is a very long priorities field " * 6
    summary = _summarize_priorities(long_text)
    assert len(summary) <= 145
    assert summary.endswith("…")


def test_derive_key_slugifies_name():
    assert derive_key("Jane Q. Doe") == "jane_q_doe"
    assert derive_key("  VP of Engineering!!  ") == "vp_of_engineering"


def test_validate_new_key_rejects_builtin_collision():
    db = _make_db()
    try:
        try:
            validate_new_key(db, "tech_ceo")
            assert False, "expected ValueError for built-in key collision"
        except ValueError as exc:
            assert "built-in" in str(exc)
    finally:
        db.close()


def test_validate_new_key_rejects_existing_custom_collision():
    db = _make_db()
    try:
        db.add(_make_custom_row(key="dup_key"))
        db.commit()
        try:
            validate_new_key(db, "dup_key")
            assert False, "expected ValueError for existing custom key collision"
        except ValueError as exc:
            assert "custom persona" in str(exc)
    finally:
        db.close()


def test_validate_new_key_accepts_fresh_key():
    db = _make_db()
    try:
        validate_new_key(db, "brand_new_key")  # must not raise
    finally:
        db.close()


def test_assign_custom_color_cycles_through_palette():
    palette_len = len(persona_service.CUSTOM_PALETTE)
    colors = [assign_custom_color(i) for i in range(palette_len * 2)]
    assert colors[:palette_len] == colors[palette_len:]
    assert len(set(colors[:palette_len])) == palette_len  # all distinct within one cycle
    # Custom palette must not overlap with built-in colors.
    builtin_colors = {c for c, _ in persona_service.BUILTIN_COLORS.values()}
    assert not (set(colors) & builtin_colors)


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning persona_service tests...\n")

    _run("get_all_personas includes all builtins", test_get_all_personas_includes_all_builtins)
    _run("get_all_personas includes custom row", test_get_all_personas_includes_custom_row)
    _run("get_all_personas shape uniform across builtin and custom", test_get_all_personas_shape_uniform_across_builtin_and_custom)
    _run("build_custom_persona_system pure function", test_build_custom_persona_system_pure_function)
    _run("summarize_priorities short and long", test_summarize_priorities_short_and_long)
    _run("derive_key slugifies name", test_derive_key_slugifies_name)
    _run("validate_new_key rejects builtin collision", test_validate_new_key_rejects_builtin_collision)
    _run("validate_new_key rejects existing custom collision", test_validate_new_key_rejects_existing_custom_collision)
    _run("validate_new_key accepts fresh key", test_validate_new_key_accepts_fresh_key)
    _run("assign_custom_color cycles through palette", test_assign_custom_color_cycles_through_palette)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
