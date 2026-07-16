"""Friendly reference codes (migration 010): render + forgiving parse. Pure — no DB."""

from __future__ import annotations

import pytest

from app.utils.codes import parse_product_code, parse_rider_code, product_code, rider_code


@pytest.mark.parametrize(
    "n,expected", [(1, "PR0001"), (42, "PR0042"), (1234, "PR1234"), (12345, "PR12345")]
)
def test_product_code_pads_to_four_then_grows(n, expected):
    assert product_code(n) == expected


@pytest.mark.parametrize(
    "n,expected", [(1, "rider001"), (7, "rider007"), (42, "rider042"), (1000, "rider1000")]
)
def test_rider_code_pads_to_three_then_grows(n, expected):
    assert rider_code(n) == expected


@pytest.mark.parametrize("raw", ["PR0001", "pr0001", "PR1", "pr1", "  PR0001  ", "1"])
def test_parse_product_code_accepts_every_form_a_keeper_types(raw):
    assert parse_product_code(raw) == 1


@pytest.mark.parametrize("raw", ["rider007", "RIDER7", "Rider007", " rider7 ", "7"])
def test_parse_rider_code_accepts_every_form(raw):
    assert parse_rider_code(raw) == 7


@pytest.mark.parametrize("raw", ["", "   ", "PR", "PR0", "abc", "PR12x", "rider1", None])
def test_parse_product_code_rejects_junk_as_none(raw):
    # None (not an exception) — the caller turns it into the same "not found" a wrong UUID gets.
    assert parse_product_code(raw) is None


@pytest.mark.parametrize("raw", ["", "rider", "rider0", "xyz", "PR1"])
def test_parse_rider_code_rejects_junk_as_none(raw):
    assert parse_rider_code(raw) is None


def test_round_trip_render_then_parse():
    for n in (1, 9, 10, 99, 100, 4321):
        assert parse_product_code(product_code(n)) == n
        assert parse_rider_code(rider_code(n)) == n
