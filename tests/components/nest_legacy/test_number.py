"""Tests for the Nest Legacy number platform."""

from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.pynest.enums import DualFuelBreakpointOverride
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from . import setup_integration
from .const import THERMOSTAT_KEY, dual_fuel_trait

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

BREAKPOINT_ENTITY = "number.hallway_hallway_upstairs_dual_fuel_breakpoint"
RELOCK_ENTITY = "number.front_door_front_door_auto_relock_duration"
_TRAIT_KEY = nest_hvac_pb2.EquipmentSettingsTrait.DESCRIPTOR.full_name


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.NUMBER]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All number entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_auto_relock_duration(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The lock reports and accepts its auto relock duration; see issue #30."""
    state = hass.states.get(RELOCK_ENTITY)
    assert state is not None
    assert state.state == "120"
    assert state.attributes["max"] == 600

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: RELOCK_ENTITY, ATTR_VALUE: 300},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"auto_relock_duration": 300}


async def test_dual_fuel_breakpoint(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The breakpoint is shown in whole Fahrenheit degrees; see issue #60."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(BREAKPOINT_ENTITY)
    assert state is not None
    # -2.187271 C is 28.06 F, which is the 28 F the Nest app shows.
    assert float(state.state) == pytest.approx(28.06, abs=0.01)
    assert state.attributes["min"] == -25
    assert state.attributes["max"] == 50
    assert state.attributes["step"] == 1


async def test_dual_fuel_breakpoint_unknown_while_overridden(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_data: dict[str, dict[str, Any]],
) -> None:
    """While an override is active the thermostat reports a placeholder value."""
    override = nest_hvac_pb2.EquipmentSettingsTrait.DualFuelOverride
    observe_data[THERMOSTAT_KEY][_TRAIT_KEY] = dual_fuel_trait(
        breakpoint_celsius=-1.0,
        override=override.DUAL_FUEL_OVERRIDE_ALWAYS_ALT,
    )
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(BREAKPOINT_ENTITY)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_setting_breakpoint_clears_the_override(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Picking a temperature cancels always/never alt heat, as the app does."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: BREAKPOINT_ENTITY, ATTR_VALUE: 32},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["dual_fuel_breakpoint"] == pytest.approx(0)
    assert data["dual_fuel_breakpoint_override"] is DualFuelBreakpointOverride.NONE


async def test_no_entity_without_dual_fuel(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_data: dict[str, dict[str, Any]],
) -> None:
    """A single fuel system does not get the dual fuel controls."""
    observe_data[THERMOSTAT_KEY][_TRAIT_KEY] = dual_fuel_trait(dual_fuel=False)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(BREAKPOINT_ENTITY) is None
