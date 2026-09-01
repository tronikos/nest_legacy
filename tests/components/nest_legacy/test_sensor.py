"""Tests for the Nest Legacy sensor platform."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from . import setup_integration

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

THERMOSTAT = "09AA00AA00AA0AAA"
TEMPERATURE_ENTITY = "sensor.hallway_hallway_thermostat_temperature"
SENSOR_TEMPERATURE_ENTITY = "sensor.bedroom_bedroom_sensor_temperature"
SENSOR_BATTERY_ENTITY = "sensor.bedroom_bedroom_sensor_battery_level"
PROTECT_BATTERY_ENTITY = "sensor.hallway_hallway_protect_battery_level"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.SENSOR]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All sensor entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_temperature_sensor_keeps_one_decimal(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The temperature sensor keeps the resolution the API reports; see issue #48.

    The climate entity rounds to whole degrees Fahrenheit for the setpoint UI,
    but the diagnostic sensor is not constrained by the device's step.
    """
    state = hass.states.get(TEMPERATURE_ENTITY)
    assert state is not None
    assert state.state == "21.11"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_remote_sensor_battery_is_not_zero(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Remote sensors report their real battery level; see issue #18."""
    state = hass.states.get(SENSOR_BATTERY_ENTITY)
    assert state is not None
    assert state.state == "92"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_protect_battery_is_scaled_from_millivolts(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The Protect reports millivolts, which become a percentage."""
    state = hass.states.get(PROTECT_BATTERY_ENTITY)
    assert state is not None
    assert 0 <= float(state.state) <= 100


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_internal_sensor_stays_readable_with_a_remote_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """The remote sensor does not hide its own reading; see issue #20."""
    app_launch_data["rcs_settings.09AA00AA00AA0AAA"]["active_rcs_sensors"] = [
        "kryptonite.18B430CCCCCC0001"
    ]
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_integration(hass, mock_config_entry)

    # The thermostat now follows the remote sensor.
    assert hass.states.get(TEMPERATURE_ENTITY).state == "67.1"
    # And the remote sensor still reports itself.
    assert hass.states.get(SENSOR_TEMPERATURE_ENTITY).state == "67.1"
