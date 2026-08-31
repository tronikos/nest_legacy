"""Tests for the Nest Legacy setup and teardown."""

from unittest.mock import AsyncMock

from aiohttp import ClientError
from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.pynest.exceptions import (
    BadCredentialsException,
    NestServiceException,
)
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import setup_integration

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The entry loads, then unloads cleanly."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_devices_are_registered(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Every device in the app launch and observe payloads is registered."""
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )

    assert {next(iter(device.identifiers))[1] for device in devices} == {
        "00000000-0000-0000-0000-000000000001",
        "09AA00AA00AA0AAA",
        "09AA00AA00AA0AA1",
        "18B430CCCCCC0001",
        "18B430CCCCCC0002",
        "18B430DDDDDD0001",
        "18B430DDDDDD0002",
    }

    thermostat = device_registry.async_get_device_by_identifier(
        (DOMAIN, "09AA00AA00AA0AAA"), init_integration.entry_id
    )
    assert thermostat is not None
    assert thermostat.name == "Hallway Thermostat"
    assert thermostat.manufacturer == "Google"
    assert thermostat.model == "Learning Thermostat, 3rd Gen"
    assert thermostat.sw_version == "6.2-24"
    assert (dr.CONNECTION_NETWORK_MAC, "18:b4:30:00:00:01") in thermostat.connections


async def test_bad_credentials_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Rejected credentials put the entry into the reauth flow."""
    mock_nest_client.is_expired.return_value = True
    mock_nest_client.async_authenticate_with_google_credentials.side_effect = (
        BadCredentialsException("invalid")
    )

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


@pytest.mark.parametrize(
    "error",
    [
        NestServiceException("503"),
        ClientError("boom"),
        TimeoutError,
    ],
)
async def test_transient_errors_retry_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    error: Exception,
) -> None:
    """A transient failure asks Home Assistant to retry rather than reauthenticate."""
    mock_nest_client.is_expired.return_value = True
    mock_nest_client.async_authenticate_with_google_credentials.side_effect = error

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()
