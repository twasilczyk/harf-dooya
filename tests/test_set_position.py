"""Percentage targeting sends ordinary RF commands and cancels obsolete Stops."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.harf_dooya.protocol import make_command

from .test_decoder import remote_frame
from .test_learning import emit
from .test_remote_state import setup_remote
from .test_timed_position import assert_cover, cover_ids, motion_clock  # noqa: F401


async def prepare(hass, config_entry, receiver, position=0):
    _, event = await setup_remote(hass, config_entry, receiver)
    remote = config_entry.runtime_data
    remote.motions[1].position = position
    remote._notify()
    return remote, cover_ids(hass)[1], event


async def set_position(hass, entity_id, position):
    await hass.services.async_call(
        "cover", "set_cover_position",
        {"entity_id": entity_id, "position": position}, blocking=True,
    )


def take_timer(remote, channel=1):
    """Capture the real scheduled callback so tests can advance a synthetic clock."""
    handle = remote._position_targets[channel].timer
    assert handle is not None
    callback, args = handle._callback, handle._args
    handle.cancel()
    return lambda: callback(*args)


async def fire_stop(hass, remote, clock, seconds, channel=1):
    callback = take_timer(remote, channel)
    clock.now += seconds
    remote._update_positions()
    callback()
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize("start,target,button", [(0, 50, 1), (100, 25, 3), (40, 70, 1)])
async def test_set_position_sends_direction_then_stop(
    hass, transmitter, config_entry, receiver, motion_clock, start, target, button
):
    remote, entity_id, event = await prepare(hass, config_entry, receiver, start)
    await set_position(hass, entity_id, target)
    seconds = abs(target - start) / 100 * 30
    timer = remote._position_targets[1].timer
    assert timer.when() - hass.loop.time() == pytest.approx(seconds, abs=0.5)
    assert len(transmitter.commands) == 1
    assert_cover(hass, entity_id, start, "opening" if button == 1 else "closing")
    await fire_stop(hass, remote, motion_clock, seconds)
    assert_cover(hass, entity_id, target, "open")
    assert not remote._position_targets
    assert not remote._tx_tasks
    assert hass.states.get(event).state == "unknown"
    assert len(transmitter.commands) == 2
    for actual, command in zip(transmitter.commands, (button, 5), strict=True):
        expected = make_command(remote.remote_id, 1, command)
        assert actual.get_raw_timings() == expected.get_raw_timings()
        assert actual.frequency == 433_890_000
        assert actual.repeat_count == 2
    for channel in (0, 2, 3):
        assert_cover(hass, cover_ids(hass)[channel], None, "unknown")


@pytest.mark.parametrize(
    "target,endpoint,button",
    [(0, 0, 3), (1, 0, 3), (49, 0, 3), (50, 100, 1), (99, 100, 1), (100, 100, 1)],
)
async def test_unknown_target_runs_to_nearest_endpoint(
    hass, transmitter, config_entry, receiver, motion_clock, target, endpoint, button
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver, None)
    await set_position(hass, entity_id, target)
    assert_cover(hass, entity_id, None, "opening" if button == 1 else "closing")
    assert not remote._position_targets
    assert len(transmitter.commands) == 1
    assert transmitter.commands[0].get_raw_timings() == make_command(
        remote.remote_id, 1, button
    ).get_raw_timings()
    motion_clock.now += 30
    remote._update_positions()
    assert_cover(hass, entity_id, endpoint, "closed" if endpoint == 0 else "open")
    # Establishing an endpoint neither sends Stop nor resumes the original target.
    assert len(transmitter.commands) == 1
    assert not remote._position_targets


async def test_same_target_noop_or_immediate_stop(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver, 40)
    await set_position(hass, entity_id, 40)
    assert not transmitter.commands
    assert not remote._position_targets
    remote._observe(1, 1, "remote")
    await set_position(hass, entity_id, 40)
    assert len(transmitter.commands) == 1
    assert_cover(hass, entity_id, 40, "open")
    assert not remote._position_targets


@pytest.mark.parametrize("origin", ["ha", "remote"])
@pytest.mark.parametrize("channel", [0, 1])
@pytest.mark.parametrize("button", [1, 3, 5])
async def test_manual_movement_cancels_timed_stop(
    hass, transmitter, config_entry, receiver, motion_clock, origin, channel, button
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    await set_position(hass, entity_id, 50)
    handle = remote._position_targets[1].timer
    callback, args = handle._callback, handle._args
    motion_clock.now += 1  # Outside the transmitted command's echo window.
    if origin == "remote":
        await emit(hass, receiver, remote_frame(channel=channel, command=button) * 3)
    else:
        service = {1: "open_cover", 3: "close_cover", 5: "stop_cover"}[button]
        await hass.services.async_call(
            "cover", service, {"entity_id": cover_ids(hass)[channel]}, blocking=True
        )
    assert handle.cancelled()
    assert not remote._position_targets
    count = len(transmitter.commands)
    callback(*args)  # Even an already-dispatched obsolete callback must be harmless.
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(transmitter.commands) == count


async def test_echo_p2_unknown_and_other_channel_preserve_target(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, event = await prepare(hass, config_entry, receiver)
    await set_position(hass, entity_id, 50)
    target = remote._position_targets[1]
    await emit(hass, receiver, remote_frame(command=1) * 3)
    assert hass.states.get(event).state == "unknown"
    motion_clock.now += 1
    for channel, command in ((1, 12), (1, 7), (2, 5)):
        await emit(hass, receiver, remote_frame(channel=channel, command=command) * 3)
        assert remote._position_targets[1] is target
    await fire_stop(hass, remote, motion_clock, 14)
    assert_cover(hass, entity_id, 50, "open")
    assert len(transmitter.commands) == 2


async def test_retarget_recalculates_and_cancels_old_stop(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    await set_position(hass, entity_id, 50)
    obsolete = take_timer(remote)
    motion_clock.now += 6
    await set_position(hass, entity_id, 10)  # Now at 20%, so reverse.
    assert_cover(hass, entity_id, 20, "closing")
    assert remote._position_targets[1].timer.when() - hass.loop.time() == pytest.approx(3, abs=0.5)
    obsolete()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(transmitter.commands) == 2
    await fire_stop(hass, remote, motion_clock, 3)
    assert_cover(hass, entity_id, 10, "open")
    assert len(transmitter.commands) == 3


async def test_queued_target_uses_position_when_radio_becomes_free(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver, 100)
    remote._observe(1, 3, "remote")
    await remote._tx_lock.acquire()
    task = asyncio.create_task(remote.async_set_position(1, 60, transmitter.async_send_command))
    await asyncio.sleep(0)
    motion_clock.now += 15  # 50% now: opening, not closing, is needed to reach 60%.
    remote._tx_lock.release()
    await task
    assert_cover(hass, entity_id, 50, "opening")
    await fire_stop(hass, remote, motion_clock, 3)
    assert_cover(hass, entity_id, 60, "open")
    assert len(transmitter.commands) == 2


async def test_queued_stop_cancelled_by_remote_override(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    await set_position(hass, entity_id, 50)
    await remote._tx_lock.acquire()
    callback = take_timer(remote)
    motion_clock.now += 15
    callback()
    await asyncio.sleep(0)
    assert remote._tx_tasks  # Stop is waiting for the shared transmitter.
    await emit(hass, receiver, remote_frame(command=3) * 3)
    remote._tx_lock.release()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(transmitter.commands) == 1
    assert_cover(hass, entity_id, 50, "closing")


async def test_send_failure_never_schedules_stop(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    with patch.object(transmitter, "async_send_command", AsyncMock(side_effect=HomeAssistantError("failed"))):
        with pytest.raises(HomeAssistantError):
            await set_position(hass, entity_id, 50)
    assert not remote._position_targets
    assert_cover(hass, entity_id, 0, "closed")


async def test_stop_failure_does_not_claim_target_reached(
    hass, transmitter, config_entry, receiver, motion_clock, caplog
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    await set_position(hass, entity_id, 50)
    with patch.object(transmitter, "async_send_command", AsyncMock(side_effect=HomeAssistantError("offline"))):
        await fire_stop(hass, remote, motion_clock, 15)
    assert "Could not send timed Stop" in caplog.text
    assert_cover(hass, entity_id, 50, "opening")
    assert not remote._position_targets
    motion_clock.now += 3
    remote._update_positions()
    assert_cover(hass, entity_id, 60, "opening")


async def test_unload_cancels_automatic_stop(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    await set_position(hass, entity_id, 50)
    handle = remote._position_targets[1].timer
    callback, args = handle._callback, handle._args
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert handle.cancelled()
    assert not remote._position_targets
    callback(*args)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(transmitter.commands) == 1


async def test_new_target_supersedes_queued_start(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    await remote._tx_lock.acquire()
    first = asyncio.create_task(remote.async_set_position(1, 20, transmitter.async_send_command))
    await asyncio.sleep(0)
    second = asyncio.create_task(remote.async_set_position(1, 80, transmitter.async_send_command))
    await asyncio.sleep(0)
    remote._tx_lock.release()
    await asyncio.gather(first, second)
    assert len(transmitter.commands) == 1
    assert remote._position_targets[1].position == 80
    await fire_stop(hass, remote, motion_clock, 24)
    assert_cover(hass, entity_id, 80, "open")
    assert len(transmitter.commands) == 2


async def test_independent_targets_and_delayed_stop(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    remote.motions[2].position = 100
    remote._notify()
    await set_position(hass, entity_id, 50)
    second = cover_ids(hass)[2]
    await set_position(hass, second, 50)
    await fire_stop(hass, remote, motion_clock, 15)
    assert_cover(hass, entity_id, 50, "open")
    assert 2 in remote._position_targets
    # Queueing/latency may overshoot; do not forcibly report the requested 50%.
    await fire_stop(hass, remote, motion_clock, 3, channel=2)
    assert_cover(hass, second, 40, "open")
    assert len(transmitter.commands) == 4


async def test_real_timer_sends_stop(hass, transmitter, config_entry, receiver):
    """Exercise the actual scheduled callback, not just a manually advanced clock."""
    hass.config_entries.async_update_entry(
        config_entry, options={"travel_times": {"1": 1}}
    )
    remote, entity_id, _ = await prepare(hass, config_entry, receiver)
    stopped = asyncio.Event()
    original = transmitter.async_send_command

    async def send(command):
        await original(command)
        if len(transmitter.commands) == 2:
            stopped.set()

    with patch.object(transmitter, "async_send_command", side_effect=send):
        await set_position(hass, entity_id, 50)
        await asyncio.wait_for(stopped.wait(), timeout=3)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(transmitter.commands) == 2
    assert remote.motions[1].direction is None
    assert not remote._position_targets
    assert hass.states.get(entity_id).state == "open"
    expected = make_command(remote.remote_id, 1, 5)
    assert transmitter.commands[-1].get_raw_timings() == expected.get_raw_timings()


@pytest.mark.parametrize(
    "start,first,target,expected,delay",
    [(0, 50, 80, 20, 18), (0, 80, 30, 20, 3),
     (100, 50, 20, 80, 18), (100, 20, 70, 80, 3)],
)
async def test_retarget_same_direction_only_changes_stop_timer(
    hass, transmitter, config_entry, receiver, motion_clock,
    start, first, target, expected, delay,
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver, start)
    await set_position(hass, entity_id, first)
    obsolete = take_timer(remote)
    motion_clock.now += 6
    await set_position(hass, entity_id, target)
    assert_cover(hass, entity_id, expected, "opening" if start == 0 else "closing")
    assert remote._position_targets[1].timer.when() - hass.loop.time() == pytest.approx(delay, abs=0.5)
    obsolete()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(transmitter.commands) == 1  # No duplicate direction command.
    await fire_stop(hass, remote, motion_clock, delay)
    assert_cover(hass, entity_id, target, "open")
    assert len(transmitter.commands) == 2


@pytest.mark.parametrize("start,endpoint", [(0, 100), (100, 0)])
async def test_retarget_endpoint_cancels_stop_without_extra_time(
    hass, transmitter, config_entry, receiver, motion_clock, start, endpoint
):
    remote, entity_id, _ = await prepare(hass, config_entry, receiver, start)
    await set_position(hass, entity_id, 50)
    obsolete = take_timer(remote)
    motion_clock.now += 6
    await set_position(hass, entity_id, endpoint)
    assert not remote._position_targets
    obsolete()
    await hass.async_block_till_done(wait_background_tasks=True)
    motion_clock.now += 24
    remote._update_positions()
    assert_cover(hass, entity_id, endpoint, "open" if endpoint else "closed")
    assert len(transmitter.commands) == 1
    assert remote._position_timer is None


async def test_all_blinds_average_and_individual_target_timing(
    hass, transmitter, config_entry, receiver, motion_clock
):
    hass.config_entries.async_update_entry(
        config_entry, options={"travel_times": {"1": 10, "2": 20, "3": 30}}
    )
    remote, _, _ = await prepare(hass, config_entry, receiver, 0)
    entities = cover_ids(hass)
    remote.motions[2].position = 100
    remote._notify()
    assert_cover(hass, entities[0], None, "unknown")
    remote.motions[3].position = 20
    remote._notify()
    assert_cover(hass, entities[0], 40, "open")
    await set_position(hass, entities[0], 50)
    assert_cover(hass, entities[0], 40, "opening")
    for channel, button, delay in ((1, 1, 5), (2, 3, 10), (3, 1, 9)):
        assert transmitter.commands[channel - 1].get_raw_timings() == make_command(
            remote.remote_id, channel, button
        ).get_raw_timings()
        assert remote._position_targets[channel].timer.when() - hass.loop.time() == pytest.approx(delay, abs=1)
    callbacks = {channel: take_timer(remote, channel) for channel in (1, 2, 3)}
    for channel, elapsed in ((1, 5), (3, 9), (2, 10)):
        motion_clock.now = 100 + elapsed
        remote._update_positions()
        callbacks[channel]()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert_cover(hass, entities[channel], 50, "open")
    assert_cover(hass, entities[0], 50, "open")
    assert len(transmitter.commands) == 6
    assert not remote._position_targets


@pytest.mark.parametrize("endpoint,button", [(0, 3), (100, 1)])
async def test_all_blinds_endpoint_uses_individual_commands_without_stops(
    hass, transmitter, config_entry, receiver, motion_clock, endpoint, button
):
    remote, _, _ = await prepare(hass, config_entry, receiver, None)
    entities = cover_ids(hass)
    await set_position(hass, entities[0], endpoint)
    assert not remote._position_targets
    motion_clock.now += 30
    remote._update_positions()
    for entity in entities:
        assert_cover(hass, entity, endpoint, "open" if endpoint else "closed")
    assert len(transmitter.commands) == 3
    for channel, command in enumerate(transmitter.commands, 1):
        assert command.get_raw_timings() == make_command(
            remote.remote_id, channel, button
        ).get_raw_timings()


@pytest.mark.parametrize("origin", ["ha", "remote"])
@pytest.mark.parametrize("channel", [0, 2])
async def test_override_cancels_group_members_waiting_to_start(
    hass, transmitter, config_entry, receiver, motion_clock, origin, channel
):
    remote, _, _ = await prepare(hass, config_entry, receiver)
    for motion in remote.motions.values():
        motion.position = 0
    entities = cover_ids(hass)
    await remote._tx_lock.acquire()
    group = asyncio.create_task(set_position(hass, entities[0], 50))
    async with asyncio.timeout(2):
        while set(remote._position_targets) != {1, 2, 3}:
            await asyncio.sleep(0)
    assert set(remote._position_targets) == {1, 2, 3}
    if origin == "remote":
        remote.async_receive(remote_frame(channel=channel, command=5) * 3)
        override = None
    else:
        override = asyncio.create_task(hass.services.async_call(
            "cover", "stop_cover", {"entity_id": entities[channel]}, blocking=True
        ))
        async with asyncio.timeout(2):
            while (remote._position_targets if channel == 0
                   else channel in remote._position_targets):
                await asyncio.sleep(0)
    assert channel not in remote._position_targets
    if channel == 0:
        assert not remote._position_targets
    remote._tx_lock.release()
    await group
    if override is not None:
        await override
    remaining = {1, 3} if channel == 2 else set()
    assert set(remote._position_targets) == remaining
    expected = len(remaining) + (origin == "ha")
    assert len(transmitter.commands) == expected
    for affected in ({1, 2, 3} - remaining):
        assert_cover(hass, entities[affected], 0, "closed")


async def test_all_blinds_stop_interrupts_initial_command_sequence(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, _, _ = await prepare(hass, config_entry, receiver)
    for motion in remote.motions.values():
        motion.position = 0
    entity = cover_ids(hass)[0]
    sent = asyncio.Event()
    release = asyncio.Event()
    original = transmitter.async_send_command

    async def send(command):
        await original(command)
        if len(transmitter.commands) == 1:
            sent.set()
            await release.wait()

    with patch.object(transmitter, "async_send_command", side_effect=send):
        group = asyncio.create_task(set_position(hass, entity, 50))
        await asyncio.wait_for(sent.wait(), 2)
        stop = asyncio.create_task(hass.services.async_call(
            "cover", "stop_cover", {"entity_id": entity}, blocking=True
        ))
        async with asyncio.timeout(2):
            while remote._position_targets:
                await asyncio.sleep(0)
        release.set()
        await asyncio.gather(group, stop)
    assert len(transmitter.commands) == 2
    assert transmitter.commands[-1].get_raw_timings() == make_command(
        remote.remote_id, 0, 5
    ).get_raw_timings()
    assert_cover(hass, entity, 0, "closed")
    assert not remote._position_targets


async def test_all_blinds_send_failure_preserves_started_stop_timers(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, _, _ = await prepare(hass, config_entry, receiver)
    for motion in remote.motions.values():
        motion.position = 0
    original = transmitter.async_send_command

    async def send(command):
        if transmitter.commands:
            raise HomeAssistantError("offline")
        await original(command)

    with patch.object(transmitter, "async_send_command", side_effect=send):
        with pytest.raises(HomeAssistantError):
            await set_position(hass, cover_ids(hass)[0], 50)
    assert set(remote._position_targets) == {1}
    await fire_stop(hass, remote, motion_clock, 15)
    assert_cover(hass, cover_ids(hass)[1], 50, "open")
    assert len(transmitter.commands) == 2


async def test_all_blinds_rounding_does_not_claim_every_blind_closed(
    hass, transmitter, config_entry, receiver, motion_clock
):
    remote, _, _ = await prepare(hass, config_entry, receiver)
    for motion in remote.motions.values():
        motion.position = 0
    remote.motions[1].position = 1
    remote._notify()
    state = hass.states.get(cover_ids(hass)[0])
    assert state.attributes["current_position"] == 0  # Rounded average of 1, 0, 0.
    assert state.attributes["is_closed"] is False
    assert state.state == "open"


async def test_mixed_group_20_0_0_to_10_routes_each_motor_independently(
    hass, transmitter, config_entry, receiver, motion_clock
):
    """Regression for the reported mixed-direction group movement."""
    from custom_components.harf_dooya.protocol import Frame, decode_frame

    remote, _, _ = await prepare(hass, config_entry, receiver, 20)
    remote.motions[2].position = remote.motions[3].position = 0
    remote._notify()
    entities = cover_ids(hass)
    assert_cover(hass, entities[0], 7, "open")
    await set_position(hass, entities[0], 10)
    frames = [decode_frame(command.get_raw_timings()) for command in transmitter.commands]
    assert frames == [
        Frame(remote.remote_id, 1, 3),
        Frame(remote.remote_id, 2, 1),
        Frame(remote.remote_id, 3, 1),
    ]
    assert_cover(hass, entities[1], 20, "closing")
    assert_cover(hass, entities[2], 0, "opening")
    assert_cover(hass, entities[3], 0, "opening")
    stops = [take_timer(remote, channel) for channel in (1, 2, 3)]
    motion_clock.now += 3
    remote._update_positions()
    for stop in stops:
        stop()
    await hass.async_block_till_done(wait_background_tasks=True)
    frames = [decode_frame(command.get_raw_timings()) for command in transmitter.commands]
    assert frames[3:] == [Frame(remote.remote_id, channel, 5) for channel in (1, 2, 3)]
    for entity in entities:
        assert_cover(hass, entity, 10, "open")
