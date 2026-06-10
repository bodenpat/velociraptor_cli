"""Cursor pagination (PLAN.md §2): paged endpoints return {size, cursor, data}.

`paginate` yields items page by page. `--all` traversal is capped by a page
budget (PLAN.md §9.6) so a pathological cursor loop can't run forever; hitting
the budget raises UsageError rather than silently truncating.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from .errors import APIError, UsageError

DEFAULT_PAGE_BUDGET = 100


def paginate(
    fetch_page: Callable[[str | None], Any],
    *,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[Any]:
    """Iterate items across cursor pages.

    fetch_page(cursor) must perform one GET and return the decoded response.
    Handles both the {size, cursor, data} envelope and bare-array responses
    (bare arrays are a single page by definition).
    """
    cursor: str | None = None
    seen_cursors: set[str] = set()
    yielded = 0
    for _page in range(page_budget):
        response = fetch_page(cursor)
        if response is None:
            return
        if isinstance(response, list):  # bare array: no envelope, no cursor
            items, cursor = response, None
        elif isinstance(response, dict):
            items = response.get("data") or []
            cursor = response.get("cursor") or None
        else:
            raise APIError(f"Unexpected paged response type: {type(response).__name__}")

        for item in items:
            yield item
            yielded += 1
            if max_items is not None and yielded >= max_items:
                return

        if not cursor or not items:
            return
        if cursor in seen_cursors:  # server bug guard: cursor loop
            return
        seen_cursors.add(cursor)

    raise UsageError(
        f"Pagination exceeded the page budget ({page_budget} pages). "
        f"Narrow the query or raise --page-budget."
    )


def collect(fetch_page: Callable[[str | None], Any], **kwargs) -> list:
    return list(paginate(fetch_page, **kwargs))
