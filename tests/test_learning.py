"""Learn through real HA progress flows without transmitting anything."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioesphomeapi import (
    InfraredInfo,
    InfraredRFReceiveEvent,
    RadioFrequencyCapability,
    RadioFrequencyInfo,
)
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.dispatcher import DATA_DISPATCHER, async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harf_dooya.receiver import SIGNAL_RX_FRAME

from .test_decoder import remote_frame


async def start_learning(hass, transmitter, receiver, domain="hackrf_proxy"):
    result = await hass.config_entries.flow.async_init(
        "harf_dooya", context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"transmitter": transmitter.entity_id}
    )
    assert result["step_id"] == "receiver"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receiver": f"{domain}:{receiver.entry_id}"}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    await asyncio.sleep(0)
    return result


async def emit(hass, receiver, timings):
    async_dispatcher_send(
        hass, SIGNAL_RX_FRAME.format(receiver.entry_id), {"timings": timings}
    )
    await hass.async_block_till_done()


@pytest.mark.parametrize("command", [1, 3, 5, 12, 7])
async def test_learning_split_and_cleanup(hass, transmitter, receiver, command):
    result = await start_learning(hass, transmitter, receiver)
    signal = SIGNAL_RX_FRAME.format(receiver.entry_id)
    burst = remote_frame(channel=255, command=command) * 3
    await emit(hass, receiver, burst[:100])
    assert hass.config_entries.flow.async_get(result["flow_id"])["step_id"] == "learn"
    await emit(hass, receiver, burst[100:])
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not hass.data[DATA_DISPATCHER].get(signal)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "finish"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"channels": 3}
    )
    assert result["data"]["remote_id"] == 0x123C14
    assert result["data"]["channels"] == 3
    assert result["result"].unique_id == "123c14"
    await hass.async_block_till_done()
    assert transmitter.commands == []
    assert await hass.config_entries.async_unload(result["result"].entry_id)
    assert not hass.data[DATA_DISPATCHER].get(signal)


async def test_cancel(hass, transmitter, receiver):
    result = await start_learning(hass, transmitter, receiver)
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not hass.data[DATA_DISPATCHER].get(SIGNAL_RX_FRAME.format(receiver.entry_id))


async def test_timeout_retry_and_manual(hass, transmitter, receiver):
    with patch("custom_components.harf_dooya.config_flow.LEARN_TIMEOUT", 0):
        result = await start_learning(hass, transmitter, receiver)
        await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "retry"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"learn": True}
    )
    assert result["step_id"] == "receiver"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receiver": "none"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"remote_id": "1195028"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"channels": 1}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert "receiver" not in result["data"]
    await hass.async_block_till_done()
    assert not transmitter.commands


async def test_subscription_failure_and_fallback(hass, transmitter, receiver):
    with patch(
        "custom_components.harf_dooya.receiver.HackrfProxySource.async_subscribe",
        side_effect=RuntimeError,
    ):
        result = await start_learning(hass, transmitter, receiver)
        await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "retry"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"learn": False}
    )
    assert result["step_id"] == "manual"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_duplicate_learned_remote(hass, transmitter, receiver, config_entry):
    hass.config_entries.async_update_entry(config_entry, unique_id="123c14")
    result = await start_learning(hass, transmitter, receiver)
    await emit(hass, receiver, remote_frame() * 2)
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["reason"] == "already_configured"


async def test_esphome_learning_without_cached_receiver(hass, transmitter):
    entry = MockConfigEntry(domain="esphome", title="RF node")
    entry.add_to_hass(hass)
    client = MagicMock()
    client.list_entities_services = AsyncMock(
        return_value=(
            [
                InfraredInfo(key=1),
                RadioFrequencyInfo(
                    key=7, device_id=2, capabilities=RadioFrequencyCapability.RECEIVER
                ),
                RadioFrequencyInfo(
                    key=8, capabilities=RadioFrequencyCapability.TRANSMITTER
                ),
            ],
            [],
        )
    )
    entry.runtime_data = MagicMock(available=True, client=client, info={})
    entry.mock_state(hass, ConfigEntryState.LOADED)
    try:
        result = await start_learning(hass, transmitter, entry, "esphome")
        await hass.async_block_till_done()
        receive = client.subscribe_infrared_rf_receive.call_args.args[0]
        for key, device_id in [(1, 0), (8, 0), (7, 0), (99, 2)]:
            receive(
                InfraredRFReceiveEvent(
                    key=key, device_id=device_id, timings=remote_frame() * 3
                )
            )
        assert (
            hass.config_entries.flow.async_get(result["flow_id"])["step_id"] == "learn"
        )
        receive(InfraredRFReceiveEvent(key=7, device_id=2, timings=remote_frame() * 3))
        await hass.async_block_till_done(wait_background_tasks=True)
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["step_id"] == "finish"
        client.subscribe_infrared_rf_receive.return_value.assert_called_once()
        hass.config_entries.flow.async_abort(result["flow_id"])
    finally:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
