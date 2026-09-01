"""Tests for the Nest Legacy water heater platform."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
    SERVICE_SET_TEMPERATURE,
    STATE_OFF,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

ENTITY_ID = "water_heater.hallway_hallway_heat_link"
THERMOSTAT = "09AA00AA00AA0AAA"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.WATER_HEATER]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All water heater entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_set_temperature(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Hot water temperature is settable; see issue #26."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 60},
        blocking=True,
    )

    device, data = mock_nest_client.async_set_device_data.call_args[0]
    assert device.serial_number == "09AA00AA00AA0AAB"
    assert data == {"hot_water_temperature": 60}


@pytest.mark.parametrize(
    ("operation_mode", "expected"),
    [
        (STATE_OFF, {"hot_water_mode": "off", "hot_water_boost": False}),
        ("schedule", {"hot_water_mode": "schedule", "hot_water_boost": False}),
    ],
)
async def test_set_operation_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    operation_mode: str,
    expected: dict[str, Any],
) -> None:
    """The Nest app's hot water modes are mirrored; see issue #15."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_OPERATION_MODE: operation_mode},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == expected


@pytest.mark.parametrize(
    ("operation_mode", "duration"),
    [("boost_30m", 1800), ("boost_1h", 3600), ("boost_2h", 7200)],
)
async def test_boost_keeps_the_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    operation_mode: str,
    duration: int,
) -> None:
    """A boost runs for the chosen time and leaves the schedule alone."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_OPERATION_MODE: operation_mode},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {
        "hot_water_mode": "schedule",
        "hot_water_boost": True,
        "hot_water_boost_duration": duration,
    }


async def test_away_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Away mode toggles the away flag."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        "set_away_mode",
        {ATTR_ENTITY_ID: ENTITY_ID, "away_mode": False},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"hot_water_away_enabled": False}


async def test_no_entity_without_hot_water(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """A thermostat with no heat link produces no water heater."""
    device = app_launch_data[f"device.{THERMOSTAT}"]
    device["has_hot_water_control"] = False
    device["has_hot_water_temperature"] = False
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID) is None
