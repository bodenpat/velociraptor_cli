"""_common.py: --param parsing, collector args, durations, table rendering."""

from __future__ import annotations

import pytest

from vrcli.cli._common import (
    _render_table,
    build_collector_args,
    parse_artifact_params,
    parse_duration,
)
from vrcli.errors import UsageError


def test_param_prefixed_form():
    parsed = parse_artifact_params(("A.B:key=val",), ("A.B",))
    assert parsed == {"A.B": {"key": "val"}}


def test_param_shorthand_single_artifact():
    parsed = parse_artifact_params(("key=val",), ("A.B",))
    assert parsed == {"A.B": {"key": "val"}}


def test_param_shorthand_rejected_for_multiple_artifacts():
    with pytest.raises(UsageError, match="must be prefixed"):
        parse_artifact_params(("key=val",), ("A.B", "C.D"))


def test_param_unknown_artifact_rejected():
    with pytest.raises(UsageError, match="not in --artifact"):
        parse_artifact_params(("X.Y:key=val",), ("A.B",))


def test_param_value_may_contain_equals_and_colons():
    parsed = parse_artifact_params(("A.B:glob=C:\\Users\\**=weird",), ("A.B",))
    assert parsed["A.B"]["glob"] == "C:\\Users\\**=weird"


def test_param_missing_equals_rejected():
    with pytest.raises(UsageError):
        parse_artifact_params(("A.B:novalue",), ("A.B",))


def test_build_collector_args_shape():
    body = build_collector_args(
        ("A.B", "C.D"),
        ("A.B:k=v",),
        timeout=600,
        cpu_limit=20.0,
        max_upload_bytes=1024,
        urgent=True,
    )
    assert body["artifacts"] == ["A.B", "C.D"]
    assert body["specs"] == [
        {"artifact": "A.B", "parameters": {"env": [{"key": "k", "value": "v"}]}}
    ]
    assert body["timeout"] == 600
    assert body["cpu_limit"] == 20.0
    assert body["max_upload_bytes"] == 1024
    assert body["urgent"] is True


def test_build_collector_args_minimal():
    body = build_collector_args(("A.B",))
    assert body == {"artifacts": ["A.B"], "specs": []}


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("3600", 3600), ("30m", 1800), ("24h", 86400), ("7d", 604800), ("1w", 604800)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("bad", ["", "h", "7dd", "-5m", "1.5h"])
def test_parse_duration_rejects(bad):
    with pytest.raises(UsageError):
        parse_duration(bad)


def test_render_table_list_of_dicts():
    text = _render_table([{"a": 1, "b": "x"}, {"a": 2, "c": True}])
    lines = text.splitlines()
    assert "a" in lines[0] and "b" in lines[0] and "c" in lines[0]
    assert len(lines) == 4  # header, rule, 2 rows


def test_render_table_empty_list():
    assert _render_table([]) == "(no results)"
