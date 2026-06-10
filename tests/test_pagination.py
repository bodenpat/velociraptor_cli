"""pagination.py: cursor traversal, budgets, envelope variants."""

from __future__ import annotations

import pytest

from vrcli.errors import UsageError
from vrcli.pagination import collect, paginate


def fake_pages(pages: dict[str | None, dict]):
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return pages[cursor]

    return fetch, calls


def test_single_page_no_cursor():
    fetch, calls = fake_pages({None: {"size": 2, "data": [1, 2]}})
    assert collect(fetch) == [1, 2]
    assert calls == [None]


def test_multi_page_follows_cursor():
    fetch, calls = fake_pages(
        {
            None: {"size": 2, "cursor": "c1", "data": [1, 2]},
            "c1": {"size": 2, "cursor": "c2", "data": [3, 4]},
            "c2": {"size": 1, "data": [5]},
        }
    )
    assert collect(fetch) == [1, 2, 3, 4, 5]
    assert calls == [None, "c1", "c2"]


def test_max_items_stops_early():
    fetch, calls = fake_pages(
        {
            None: {"size": 3, "cursor": "c1", "data": [1, 2, 3]},
            "c1": {"size": 3, "data": [4, 5, 6]},
        }
    )
    assert collect(fetch, max_items=2) == [1, 2]
    assert calls == [None]


def test_bare_array_is_single_page():
    fetch, calls = fake_pages({None: [1, 2, 3]})
    assert collect(fetch) == [1, 2, 3]
    assert calls == [None]


def test_empty_cursor_terminates():
    fetch, _ = fake_pages({None: {"size": 1, "cursor": "", "data": [1]}})
    assert collect(fetch) == [1]


def test_repeated_cursor_terminates():
    fetch, calls = fake_pages(
        {
            None: {"size": 1, "cursor": "same", "data": [1]},
            "same": {"size": 1, "cursor": "same", "data": [2]},
        }
    )
    assert collect(fetch) == [1, 2]
    assert calls == [None, "same"]


def test_page_budget_exceeded_raises():
    def fetch(cursor):
        n = int(cursor or 0)
        return {"size": 1, "cursor": str(n + 1), "data": [n]}

    with pytest.raises(UsageError, match="page budget"):
        collect(fetch, page_budget=5)


def test_none_response_yields_nothing():
    assert collect(lambda _c: None) == []


def test_generator_is_lazy():
    fetched = []

    def fetch(cursor):
        fetched.append(cursor)
        return {"size": 2, "cursor": "next", "data": [1, 2]} if cursor is None else {"data": [3]}

    gen = paginate(fetch)
    assert next(gen) == 1
    assert fetched == [None]
