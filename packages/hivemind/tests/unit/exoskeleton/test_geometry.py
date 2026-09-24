"""Unit tests for hivemind.exoskeleton.geometry: Point, ScreenSize and Region's text form."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from hivemind.exoskeleton.geometry import MAX_PIXEL, Point, Region, ScreenSize

_COORD = st.integers(min_value=0, max_value=MAX_PIXEL)
_SIDE = st.integers(min_value=1, max_value=MAX_PIXEL)


@pytest.mark.parametrize(("x", "y"), [(-1, 0), (0, -1), (MAX_PIXEL + 1, 0)])
def test_point_refuses_a_coordinate_out_of_range(x: int, y: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        Point(x, y)


def test_screen_contains_only_points_strictly_inside_it() -> None:
    screen = ScreenSize(100, 50)

    assert screen.contains(Point(0, 0))
    assert screen.contains(Point(99, 49))
    assert not screen.contains(Point(100, 0))
    assert not screen.contains(Point(0, 50))


@given(_COORD, _COORD, _SIDE, _SIDE)
def test_region_text_form_round_trips(x: int, y: int, width: int, height: int) -> None:
    region = Region(x=x, y=y, width=width, height=height)

    assert Region.parse(region.spec()) == region


@pytest.mark.parametrize("text", ["", "1,2,3", "1,2,3,4,5", "a,b,c,d", "-1,0,1,1", "1, 2, 3, 4"])
def test_region_parse_refuses_anything_but_four_integers(text: str) -> None:
    with pytest.raises(ValueError, match="x,y,width,height"):
        Region.parse(text)


def test_region_refuses_an_empty_rectangle() -> None:
    with pytest.raises(ValidationError):
        Region(x=0, y=0, width=0, height=1)


def test_region_renders_imagemagicks_crop_geometry() -> None:
    assert Region(x=10, y=20, width=300, height=40).crop_geometry() == "300x40+10+20"


def test_region_fits_only_when_its_far_corner_is_on_the_screen() -> None:
    screen = ScreenSize(100, 50)

    assert Region(x=0, y=0, width=100, height=50).fits(screen)
    assert not Region(x=1, y=0, width=100, height=50).fits(screen)
    assert not Region(x=0, y=49, width=1, height=2).fits(screen)
