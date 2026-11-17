"""Deterministic travel estimates without sleeping or transmitting RF."""

import pytest

from custom_components.harf_dooya.const import BUTTON_CLOSE, BUTTON_OPEN
from custom_components.harf_dooya.motion import Motion


@pytest.mark.parametrize(
    "command,end", [(BUTTON_OPEN, 100), (BUTTON_CLOSE, 0)]
)
def test_unknown_full_travel(command, end):
    motion = Motion()
    assert motion.percentage is None
    motion.observe(command, 10, 20)
    assert motion.direction == command
    motion.update(29.9)
    assert motion.percentage is None
    motion.update(30)
    assert motion.percentage == end
    assert motion.direction is None


@pytest.mark.parametrize(
    "command,start,mid,near,end", [(1, 0, 50, 99, 100), (3, 100, 50, 1, 0)]
)
def test_known_travel_percentage(command, start, mid, near, end):
    motion = Motion(position=start)
    motion.observe(command, 100, 20)
    motion.update(110)
    assert motion.percentage == mid
    motion.update(119.9)
    assert motion.percentage == near
    assert motion.direction == command
    motion.update(120)
    assert motion.percentage == end
    assert motion.direction is None


def test_stop_freezes_exact_elapsed_percentage_then_reverse():
    motion = Motion(position=0)
    motion.observe(1, 10, 20)
    motion.observe(5, 17, 20)  # Stop between periodic updates.
    assert motion.percentage == 35
    assert motion.direction is None
    motion.update(100)
    assert motion.percentage == 35
    motion.observe(3, 100, 20)
    motion.update(103)
    assert motion.percentage == 20
    motion.update(107)
    assert motion.percentage == 0
    assert motion.direction is None


def test_reversal_uses_current_position_without_a_tick():
    motion = Motion(position=100)
    motion.observe(3, 0, 20)
    motion.observe(1, 5, 20)
    assert motion.percentage == 75
    motion.update(7)
    assert motion.percentage == 85
    motion.update(10)
    assert motion.percentage == 100


def test_unknown_stop_and_reversal_require_uninterrupted_travel():
    motion = Motion()
    motion.observe(1, 0, 20)
    motion.observe(5, 19, 20)
    assert motion.direction is None
    assert motion.percentage is None
    motion.observe(1, 100, 20)
    motion.observe(3, 119, 20)
    motion.update(138.9)
    assert motion.percentage is None
    motion.update(139)
    assert motion.percentage == 0
    motion.observe(5, 140, 20)
    assert motion.percentage == 0
    assert motion.direction is None
    motion.update(200)
    assert motion.percentage == 0


@pytest.mark.parametrize("command", [1, 12, 7])
def test_repeated_direction_and_nonmovement_do_not_restart(command):
    motion = Motion()
    motion.observe(1, 0, 20)
    motion.observe(command, 15, 40)
    assert motion.finish == 20
    assert motion.travel_time == 20
    motion.update(20)
    assert motion.percentage == 100


@pytest.mark.parametrize("command,start", [(1, 100), (3, 0)])
def test_command_at_endpoint_is_already_complete(command, start):
    motion = Motion(position=start)
    motion.observe(command, 0, 20)
    assert motion.percentage == start
    assert motion.direction is None


def test_rounding_does_not_report_endpoint_early():
    motion = Motion(position=100)
    motion.observe(3, 0, 100)
    motion.update(99.9)
    assert motion.percentage == 1
    motion.update(100)
    assert motion.percentage == 0
