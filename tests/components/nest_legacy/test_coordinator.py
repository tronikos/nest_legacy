"""Tests for the Nest Legacy coordinator's connection handling."""

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError
from custom_components.nest_legacy.const import CONF_EVENT_POLL_INTERVAL, DOMAIN
from custom_components.nest_legacy.coordinator import NestCoordinator
from custom_components.nest_legacy.pynest.exceptions import (
    AuthenticationFailedException,
    BadCredentialsException,
    EmptyResponseException,
    NestServiceException,
    NotAuthenticatedException,
)
from custom_components.nest_legacy.pynest.models import NestCamera
import pytest

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import LOCK_KEY, STRUCTURE_KEY

from pytest_homeassistant_custom_component.common import MockConfigEntry

REST_SMOKE = "binary_sensor.hallway_hallway_protect_smoke"
PROTOBUF_CLIMATE = "climate.hallway_hallway_upstairs"
REST_PROTECT_KEY = "topaz.09AA00AA00AA0AA1"
CAMERA_SERIAL = "18B430CCCCCC0002"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up a protobuf climate entity and a REST binary sensor to watch."""
    return [Platform.BINARY_SENSOR, Platform.CLIMATE]


@pytest.fixture(autouse=True)
def no_backoff_delay() -> Any:
    """Run the retry loops without waiting out the backoff."""
    with (
        patch("custom_components.nest_legacy.coordinator.INITIAL_BACKOFF_SECONDS", 0),
        patch("custom_components.nest_legacy.coordinator.MAX_BACKOFF_SECONDS", 0),
    ):
        yield


def _coordinator(config_entry: MockConfigEntry) -> NestCoordinator:
    """Return the coordinator behind a loaded config entry."""
    return config_entry.runtime_data


async def _settle(hass: HomeAssistant, subscribes: AsyncMock, count: int) -> None:
    """Wait until the REST poll has been attempted the given number of times."""
    for _ in range(200):
        await asyncio.sleep(0)
        if subscribes.await_count >= count:
            break
    await hass.async_block_till_done()


async def test_bad_credentials_stops_the_subscriber(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
) -> None:
    """Rejected credentials stop polling and ask the user to sign in again.

    See issues #8, #28 and #58: this is the only path that should ever get
    here, because it takes the integration down until the user acts.
    """
    subscribe_results.put_nowait(BadCredentialsException("invalid"))
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 2)

    coordinator = _coordinator(init_integration)
    assert coordinator.subscriber_healthy is False
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


@pytest.mark.parametrize(
    "error",
    [
        NestServiceException("503"),
        ClientError("boom"),
        NotAuthenticatedException("expired"),
    ],
)
async def test_transient_errors_keep_the_subscriber_running(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
    error: Exception,
) -> None:
    """A retryable failure never starts a reauth flow.

    This is the contract PR #59 relies on when it maps 5xx/408/429 onto
    NestServiceException instead of BadCredentialsException.
    """
    for _ in range(3):
        subscribe_results.put_nowait(error)
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 4)

    assert not hass.config_entries.flow.async_progress()
    assert mock_nest_client.async_subscribe_for_updates.await_count > 3


async def test_repeated_failures_mark_entities_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
) -> None:
    """After a few failures the REST-backed entities go unavailable."""
    assert hass.states.get(REST_SMOKE).state != STATE_UNAVAILABLE

    for _ in range(4):
        subscribe_results.put_nowait(ClientError("boom"))
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 5)

    coordinator = _coordinator(init_integration)
    assert coordinator.subscriber_healthy is False
    assert hass.states.get(REST_SMOKE).state == STATE_UNAVAILABLE
    # The protobuf stream is still up, so its devices stay available.
    assert hass.states.get(PROTOBUF_CLIMATE).state != STATE_UNAVAILABLE


async def test_subscriber_recovers(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
) -> None:
    """A successful poll restores availability."""
    for _ in range(4):
        subscribe_results.put_nowait(ClientError("boom"))
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 5)
    assert hass.states.get(REST_SMOKE).state == STATE_UNAVAILABLE

    subscribe_results.put_nowait({})
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 6)

    assert _coordinator(init_integration).subscriber_healthy is True
    assert hass.states.get(REST_SMOKE).state != STATE_UNAVAILABLE


async def test_dropped_connections_are_not_failures(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
) -> None:
    """The long poll timing out is how it is supposed to end, not an error."""
    for _ in range(5):
        subscribe_results.put_nowait(EmptyResponseException())
        subscribe_results.put_nowait(TimeoutError())
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 10)

    assert _coordinator(init_integration).subscriber_healthy is True
    assert hass.states.get(REST_SMOKE).state != STATE_UNAVAILABLE


async def test_subscriber_applies_updates(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
    app_launch_data: dict[str, Any],
) -> None:
    """An update from the long poll reaches the entity."""
    subscribe_results.put_nowait(
        {REST_PROTECT_KEY: {**app_launch_data[REST_PROTECT_KEY], "smoke_status": 2}}
    )
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 2)

    assert hass.states.get(REST_SMOKE).state == STATE_ON


async def test_observer_failure_only_affects_protobuf_devices(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_results: asyncio.Queue[Any],
) -> None:
    """A protobuf stream outage leaves the REST devices alone."""
    # Drop the open stream, then fail every attempt to reconnect.
    mock_nest_client.async_observe_for_updates.side_effect = ClientError("boom")
    observe_results.put_nowait(ClientError("boom"))
    for _ in range(200):
        await asyncio.sleep(0)
        if not _coordinator(init_integration).observer_healthy:
            break
    await hass.async_block_till_done()

    assert _coordinator(init_integration).observer_healthy is False
    assert hass.states.get(PROTOBUF_CLIMATE).state == STATE_UNAVAILABLE
    assert hass.states.get(REST_SMOKE).state != STATE_UNAVAILABLE


@pytest.mark.parametrize(
    "error",
    [
        NotAuthenticatedException("expired"),
        # PERMISSION_DENIED for a stale session, see issue #81.
        AuthenticationFailedException("authentication failed"),
    ],
)
async def test_command_retries_after_reauthenticating(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    error: Exception,
) -> None:
    """An expired session during a command is refreshed and the command retried."""
    mock_nest_client.async_set_device_data.side_effect = [error, None]

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: PROTOBUF_CLIMATE, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )

    assert mock_nest_client.async_set_device_data.await_count == 2
    mock_nest_client.async_authenticate_with_google_credentials.assert_awaited()


async def test_camera_events_retry_after_reauthenticating(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """An expired session while polling events is refreshed and the poll retried.

    The camera session token is short-lived, so the event poll runs into an
    expired session regularly. Recovering from it keeps the camera in the
    poll instead of dropping it for the rest of the client lifetime.
    See issue #81.
    """
    # The background poll task calls this too, so the first call fails with the
    # expired session and every later call succeeds.
    mock_nest_client.async_get_camera_events.side_effect = (
        AuthenticationFailedException("authentication failed"),
        *([None] * 50),
    )
    camera = NestCamera(
        object_key="DEVICE_0000000000000003",
        serial_number=CAMERA_SERIAL,
        name="Camera",
        location="Living Room",
        is_protobuf=True,
        online=True,
        streaming_enabled=True,
    )

    await _coordinator(init_integration)._async_process_events_for_device(
        camera, start_time=0, poll_interval=5
    )

    assert mock_nest_client.async_get_camera_events.await_count >= 2
    mock_nest_client.async_authenticate_with_google_credentials.assert_awaited()


async def test_camera_events_give_up_after_a_failed_reauthentication(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """If reauthenticating also fails the event poll gives up on that camera."""
    # Every call fails, so the retry after re-authenticating fails as well and
    # the poll gives up on that camera.
    mock_nest_client.async_get_camera_events.side_effect = (
        AuthenticationFailedException("authentication failed")
    )
    camera = NestCamera(
        object_key="DEVICE_0000000000000003",
        serial_number=CAMERA_SERIAL,
        name="Camera",
        location="Living Room",
        is_protobuf=True,
        online=True,
        streaming_enabled=True,
    )

    await _coordinator(init_integration)._async_process_events_for_device(
        camera, start_time=0, poll_interval=5
    )

    assert mock_nest_client.async_get_camera_events.await_count >= 2


async def test_command_gives_up_after_a_failed_reauthentication(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """If reauthenticating also fails the command reports an error."""
    mock_nest_client.async_set_device_data.side_effect = NotAuthenticatedException(
        "expired"
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: PROTOBUF_CLIMATE, "hvac_mode": HVACMode.OFF},
            blocking=True,
        )


async def test_get_guests(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Guests are read out of the raw protobuf data, keyed by resource."""
    coordinator = _coordinator(init_integration)
    raw = coordinator.get_raw_data_for_diagnostics()

    assert LOCK_KEY in raw
    assert STRUCTURE_KEY in raw
    assert coordinator.get_guests() == {}


@pytest.mark.parametrize(
    ("options", "expect_polling"),
    [
        ({}, True),
        ({CONF_EVENT_POLL_INTERVAL: 30}, True),
        ({CONF_EVENT_POLL_INTERVAL: 0}, False),
    ],
    ids=["default", "interval", "zero"],
)
async def test_event_poll_interval_of_zero_disables_polling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    options: dict[str, Any],
    expect_polling: bool,
) -> None:
    """A 0 interval is what turns camera event polling off.

    The poll must never start, rather than start and then do nothing, so there
    is nothing left to wake up on a timer or to log about.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options=options)

    with patch(
        "custom_components.nest_legacy.coordinator.NestCoordinator"
        "._async_poll_camera_events",
        new_callable=AsyncMock,
    ) as poll:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert poll.called is expect_polling


def _polled_serials(coordinator: NestCoordinator) -> set[str]:
    """Return the serial numbers the camera event poll would query."""
    return {camera.serial_number for camera in coordinator._cameras_to_poll()}


async def test_disabled_camera_is_not_polled(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A camera disabled in the device registry drops out of the event poll.

    Disabling a device disables every entity that could show its events, so
    polling it only spends API calls. Re-enabling it brings it straight back
    without a reload, because the filter is evaluated on every poll.
    """
    coordinator = _coordinator(init_integration)
    camera = coordinator.data[CAMERA_SERIAL]
    assert isinstance(camera, NestCamera)
    coordinator.data[CAMERA_SERIAL] = replace(
        camera, online=True, streaming_enabled=True
    )
    device = device_registry.async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, CAMERA_SERIAL)},
    )
    assert CAMERA_SERIAL in _polled_serials(coordinator)

    device_registry.async_update_device(
        device.id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    assert CAMERA_SERIAL not in _polled_serials(coordinator)

    device_registry.async_update_device(device.id, disabled_by=None)
    assert CAMERA_SERIAL in _polled_serials(coordinator)
