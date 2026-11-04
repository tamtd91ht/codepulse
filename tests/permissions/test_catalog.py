"""docs/specs/21: the catalog and the built-in sets."""

import importlib.util
import pathlib

from app.permissions.catalog import (
    AUTO_ROLES,
    BUILTIN_SETS,
    BY_KEY,
    CATALOG,
    FLAG,
    everything,
    merge,
    valid_item,
)


def test_keys_are_unique_and_scopes_ordered() -> None:
    assert len(BY_KEY) == len(CATALOG)
    order = ("own", "team", "project", "all")
    for p in CATALOG:
        assert list(p.scopes) == sorted(p.scopes, key=order.index), p.key


def test_builtin_sets_hold_only_valid_items_one_per_role() -> None:
    assert [b.auto_role for b in BUILTIN_SETS] == list(AUTO_ROLES)
    for b in BUILTIN_SETS:
        for key, scope in b.items.items():
            assert valid_item(key, scope), (b.auto_role, key, scope)
    # No admin screen in a built-in set: admins only until a set is made for it.
    assert not any(k.startswith("admin.") for b in BUILTIN_SETS for k in b.items)


def test_merge_keeps_the_widest_scope_and_drops_unknown_items() -> None:
    merged = merge(
        [
            ("review.view", "own"),
            ("review.view", "project"),
            ("review.view", "team"),
            ("insights.view", FLAG),
            ("gone.permission", "all"),
            ("dispute.raise", "all"),  # not a scope dispute.raise has
        ]
    )
    assert merged == {"review.view": "project", "insights.view": FLAG}


def test_admins_hold_everything_at_its_widest() -> None:
    all_of = everything()
    assert set(all_of) == set(BY_KEY)
    assert all_of["review.view"] == "all" and all_of["insights.view"] == FLAG
    assert all_of["finding.close_request"] == "own"  # its only scope


def test_the_latest_migration_holds_the_builtin_sets_of_the_catalog() -> None:
    """The built-in sets are seeded by migrations (literal copies, never imports of the
    catalog): the newest one that carries them must equal the catalog."""
    path = sorted(pathlib.Path("migrations/versions").glob("*_d2e4f6a8b0c3_report_permissions.py"))[
        -1
    ]
    spec = importlib.util.spec_from_file_location("perm_defaults", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {b.auto_role: dict(b.items) for b in BUILTIN_SETS} == module.BUILTIN
