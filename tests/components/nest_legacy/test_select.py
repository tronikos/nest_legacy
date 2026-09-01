"""Tests for the Nest Legacy select platform."""

from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .const import THERMOSTAT_KEY, dual_fuel_trait

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

BRIGHTNESS_ENTITY = "select.hallway_hallway_protect_night_light_brightness"
HOME_AWAY_ENTITY = "select.test_home"
OVERRIDE_ENTITY = "select.hallway_hallway_upstairs_dual_fuel_breakpoint_override"
_TRAIT_KEY = nest_hvac_pb2.EquipmentSettingsTrait.DESCRIPTOR.full_name


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.SELECT]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All select entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_night_light_brightness(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The Protect pathlight brightness maps onto the numeric API levels."""
    state = hass.states.get(BRIGHTNESS_ENTITY)
    assert state is not None
    assert state.state == "medium"
    assert state.attributes["options"] == ["low", "medium", "high"]

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: BRIGHTNESS_ENTITY, ATTR_OPTION: "high"},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"night_light_brightness": 3}


async def test_home_away_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The structure exposes the home/away modes the app offers."""
    state = hass.states.get(HOME_AWAY_ENTITY)
    assert state is not None
    assert state.state == "home"

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: HOME_AWAY_ENTITY, ATTR_OPTION: "away"},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"mode": "away"}


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("DUAL_FUEL_OVERRIDE_NONE", "none"),
        ("DUAL_FUEL_OVERRIDE_ALWAYS_ALT", "always_alternate_heat"),
        ("DUAL_FUEL_OVERRIDE_ALWAYS_PRIMARY", "never_alternate_heat"),
    ],
)
async def test_dual_fuel_override_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_data: dict[str, dict[str, Any]],
    override: str,
    expected: str,
) -> None:
    """Every override the thermostat can report has a matching option."""
    observe_data[THERMOSTAT_KEY][_TRAIT_KEY] = dual_fuel_trait(
        override=getattr(
            nest_hvac_pb2.EquipmentSettingsTrait.DualFuelOverride, override
        )
    )
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(OVERRIDE_ENTITY)
    assert state is not None
    assert state.state == expected


async def test_select_dual_fuel_override(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Choosing an override sends the option straight through; see issue #60."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OVERRIDE_ENTITY, ATTR_OPTION: "always_alternate_heat"},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"dual_fuel_breakpoint_override": "always_alternate_heat"}
