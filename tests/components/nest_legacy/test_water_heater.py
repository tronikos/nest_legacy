"""Tests for the Nest Legacy water heater platform."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.water_heater import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_OPERATION_LIST,
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
    SERVICE_SET_TEMPERATURE,
    STATE_OFF,
    WaterHeaterEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .const import THERMOSTAT_KEY, hot_water_traits

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


@pytest.fixture
def protobuf_heat_link(
    app_launch_data: dict[str, Any], observe_data: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Move hot water onto the protobuf pathway, the one Heat Links run on."""
    device = app_launch_data[f"device.{THERMOSTAT}"]
    device["has_hot_water_control"] = False
    device["has_hot_water_temperature"] = False
    traits = hot_water_traits()
    observe_data[THERMOSTAT_KEY].update(traits)
    return traits


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


@pytest.mark.usefixtures("protobuf_heat_link")
async def test_away_mode_is_not_offered(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Away is reported, not set: the API has no away flag to write; see issue #68."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert (
        not state.attributes[ATTR_SUPPORTED_FEATURES]
        & WaterHeaterEntityFeature.AWAY_MODE
    )
    assert state.attributes["away_active"] is False

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            WATER_HEATER_DOMAIN,
            "set_away_mode",
            {ATTR_ENTITY_ID: ENTITY_ID, "away_mode": True},
            blocking=True,
        )


@pytest.mark.usefixtures("protobuf_heat_link")
async def test_next_transition_time(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The next hot water schedule change is exposed; see issue #70."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.attributes["next_transition_time"] == datetime(
        2026, 9, 6, 5, 30, tzinfo=UTC
    )


async def test_no_temperature_without_a_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    protobuf_heat_link: dict[str, Any],
) -> None:
    """A Heat Link with no sensor reports no temperature at all; see issue #69."""
    capabilities = protobuf_heat_link[
        nest_hvac_pb2.HvacEquipmentCapabilitiesTrait.DESCRIPTOR.full_name
    ]
    capabilities.hasHotWaterTemperature = False
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] is None


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


async def test_heat_link_without_capability_flags(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    protobuf_heat_link: dict[str, Any],
) -> None:
    """The hot water traits alone prove control when the capabilities omit it.

    Some Heat Links report heat stages on HvacEquipmentCapabilitiesTrait without
    ever setting the hot water flags, while the thermostat does publish
    HotWaterTrait and HotWaterSettingsTrait.
    """
    capabilities = protobuf_heat_link[
        nest_hvac_pb2.HvacEquipmentCapabilitiesTrait.DESCRIPTOR.full_name
    ]
    capabilities.hasHotWaterControl = False
    capabilities.hasHotWaterTemperature = False
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.attributes[ATTR_OPERATION_MODE] == "schedule"
    assert "boost_1h" in state.attributes[ATTR_OPERATION_LIST]
