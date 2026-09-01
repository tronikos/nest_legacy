"""Tests for the Nest Legacy fan platform."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    DOMAIN as FAN_DOMAIN,
    SERVICE_SET_PERCENTAGE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

ENTITY_ID = "fan.hallway_hallway_thermostat_fan"
THERMOSTAT = "09AA00AA00AA0AAA"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.FAN]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All fan entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_turn_on_and_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Turning the fan on schedules a timeout and off clears it; see issue #3."""
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["fan_timer_timeout"] > 0
    assert "fan_timer_speed" not in data

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"fan_timer_timeout": 0}


async def test_multi_speed_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """A multi stage fan maps percentages onto stages; see issue #49."""
    device = app_launch_data[f"device.{THERMOSTAT}"]
    device["fan_capabilities"] = "stage3"
    device["fan_timer_speed"] = "stage2"
    device["fan_timer_timeout"] = 4102444800
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_PERCENTAGE] == 66

    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PERCENTAGE: 100},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["fan_timer_speed"] == "stage3"


async def test_set_percentage_zero_turns_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Dragging the slider to zero turns the fan off."""
    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PERCENTAGE: 0},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"fan_timer_timeout": 0}


async def test_no_fan_entity_without_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """A thermostat without a fan gets no fan entity."""
    app_launch_data[f"device.{THERMOSTAT}"]["has_fan"] = False
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID) is None
