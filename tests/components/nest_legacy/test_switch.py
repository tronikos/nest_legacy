"""Tests for the Nest Legacy switch platform."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

PATHLIGHT_ENTITY = "switch.hallway_hallway_protect_pathlight"
STREAMING_ENTITY = "switch.front_door_front_door_doorbell_streaming"
SENSOR_ENTITY = "switch.bedroom_bedroom_sensor_control_thermostat"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.SWITCH]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All switch entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("service", "expected"),
    [(SERVICE_TURN_ON, True), (SERVICE_TURN_OFF, False)],
)
async def test_toggle(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    service: str,
    expected: bool,
) -> None:
    """A switch writes the flag it is named after."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: PATHLIGHT_ENTITY}, blocking=True
    )

    device, data = mock_nest_client.async_set_device_data.call_args[0]
    assert device.serial_number == "09AA00AA00AA0AA1"
    assert data == {"night_light_enable": expected}


async def test_states_follow_the_device(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Switch states come straight from the parsed device."""
    assert hass.states.get(PATHLIGHT_ENTITY).state == STATE_ON
    assert hass.states.get(STREAMING_ENTITY).state == STATE_ON


async def test_remote_sensor_control(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """A remote sensor can be made the active one; see issues #19 and #20."""
    app_launch_data["rcs_settings.09AA00AA00AA0AAA"]["active_rcs_sensors"] = [
        "kryptonite.18B430CCCCCC0001"
    ]
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SENSOR_ENTITY).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SENSOR_ENTITY}, blocking=True
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"is_active_sensor": False}


async def test_offline_device_is_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """Entities of an offline device report unavailable rather than a stale state."""
    app_launch_data["widget_track.09AA00AA00AA0AA1"]["online"] = False
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(PATHLIGHT_ENTITY).state == STATE_UNAVAILABLE
    assert hass.states.get(STREAMING_ENTITY).state != STATE_UNAVAILABLE
    assert hass.states.get(PATHLIGHT_ENTITY).state != STATE_OFF
