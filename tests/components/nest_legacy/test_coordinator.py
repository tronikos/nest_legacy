"""Tests for the Nest Legacy coordinator's connection handling."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError
from custom_components.nest_legacy.coordinator import NestCoordinator
from custom_components.nest_legacy.pynest.exceptions import (
    BadCredentialsException,
    EmptyResponseException,
    NestServiceException,
    NotAuthenticatedException,
)
import pytest

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import LOCK_KEY, STRUCTURE_KEY

from pytest_homeassistant_custom_component.common import MockConfigEntry

REST_CLIMATE = "climate.hallway_hallway_thermostat"
PROTOBUF_CLIMATE = "climate.hallway_hallway_upstairs"


@pytest.fixture
def platforms() -> list[Platform]:
    """Only the climate platform is needed to observe availability."""
    return [Platform.CLIMATE]


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
    assert hass.states.get(REST_CLIMATE).state != STATE_UNAVAILABLE

    for _ in range(4):
        subscribe_results.put_nowait(ClientError("boom"))
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 5)

    coordinator = _coordinator(init_integration)
    assert coordinator.subscriber_healthy is False
    assert hass.states.get(REST_CLIMATE).state == STATE_UNAVAILABLE
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
    assert hass.states.get(REST_CLIMATE).state == STATE_UNAVAILABLE

    subscribe_results.put_nowait({})
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 6)

    assert _coordinator(init_integration).subscriber_healthy is True
    assert hass.states.get(REST_CLIMATE).state != STATE_UNAVAILABLE


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
    assert hass.states.get(REST_CLIMATE).state != STATE_UNAVAILABLE


async def test_subscriber_applies_updates(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    subscribe_results: asyncio.Queue[Any],
) -> None:
    """An update from the long poll reaches the entity."""
    subscribe_results.put_nowait(
        {"shared.09AA00AA00AA0AAA": {"target_temperature_type": "off"}}
    )
    await _settle(hass, mock_nest_client.async_subscribe_for_updates, 2)

    assert hass.states.get(REST_CLIMATE).state == HVACMode.OFF


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
    assert hass.states.get(REST_CLIMATE).state != STATE_UNAVAILABLE


async def test_command_retries_after_reauthenticating(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """An expired session during a command is refreshed and the command retried."""
    mock_nest_client.async_set_device_data.side_effect = [
        NotAuthenticatedException("expired"),
        None,
    ]

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: REST_CLIMATE, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )

    assert mock_nest_client.async_set_device_data.await_count == 2
    mock_nest_client.async_authenticate_with_google_credentials.assert_awaited()


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
            {ATTR_ENTITY_ID: REST_CLIMATE, "hvac_mode": HVACMode.OFF},
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
