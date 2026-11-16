"""Direct receiver lifecycle checks without HA's threaded startup fixtures."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aioesphomeapi import (
    InfraredRFReceiveEvent,
    RadioFrequencyCapability,
    RadioFrequencyInfo,
)
from homeassistant.config_entries import ConfigEntryState

from custom_components.harf_dooya.receiver import EsphomeSource


class Entry:
    entry_id = "node"
    title = "RF node"
    state = ConfigEntryState.LOADED

    def async_on_state_change(self, callback):
        self.changed = callback
        return Mock()


def test_reconnect_and_stale_callbacks():
    async def run():
        entry = Entry()
        rx = RadioFrequencyInfo(
            key=7, device_id=2, capabilities=RadioFrequencyCapability.RECEIVER
        )
        client = Mock()
        client.list_entities_services = AsyncMock(return_value=([rx], []))
        data = entry.runtime_data = SimpleNamespace(
            available=True,
            client=client,
            async_subscribe_device_updated=Mock(return_value=Mock()),
        )
        hass = SimpleNamespace(
            async_create_task=lambda coroutine, name: asyncio.create_task(
                coroutine, name=name
            )
        )
        seen = []
        available = []
        stop = EsphomeSource(hass, entry).async_subscribe(seen.append, available.append)
        await asyncio.sleep(0)
        old = client.subscribe_infrared_rf_receive.call_args.args[0]
        old(InfraredRFReceiveEvent(key=7, device_id=1, timings=[1]))
        assert not seen
        old(InfraredRFReceiveEvent(key=7, device_id=2, timings=[328, -748]))
        assert seen == [[328, -748]] and available == [True]
        update = data.async_subscribe_device_updated.call_args.args[0]
        data.available = False
        update()
        assert seen[-1] == [] and available[-1] is False
        data.available = True
        update()
        await asyncio.sleep(0)
        old(InfraredRFReceiveEvent(key=7, device_id=2, timings=[9]))
        assert seen[-1] == []
        current = client.subscribe_infrared_rf_receive.call_args.args[0]
        current(InfraredRFReceiveEvent(key=7, device_id=2, timings=[328, -748]))
        assert seen[-1] == [328, -748]
        stop()
        current(InfraredRFReceiveEvent(key=7, device_id=2, timings=[10]))
        assert seen[-1] == [328, -748]
        assert client.subscribe_infrared_rf_receive.return_value.call_count == 2

    asyncio.run(run())


def test_late_discovery_after_cancellation_cannot_subscribe():
    async def run():
        entry = Entry()
        pending = asyncio.Future()
        client = Mock()

        async def listing():
            try:
                return await pending
            except asyncio.CancelledError:
                # Even a backend swallowing cancellation cannot revive a subscription.
                return [
                    RadioFrequencyInfo(
                        key=7, capabilities=RadioFrequencyCapability.RECEIVER
                    )
                ], []

        client.list_entities_services = listing
        entry.runtime_data = SimpleNamespace(
            available=True,
            client=client,
            async_subscribe_device_updated=Mock(return_value=Mock()),
        )
        tasks = []

        def create(coroutine, name):
            task = asyncio.create_task(coroutine, name=name)
            tasks.append(task)
            return task

        stop = EsphomeSource(
            SimpleNamespace(async_create_task=create), entry
        ).async_subscribe(lambda timings: None)
        await asyncio.sleep(0)
        stop()
        await asyncio.gather(*tasks)
        client.subscribe_infrared_rf_receive.assert_not_called()

    asyncio.run(run())
