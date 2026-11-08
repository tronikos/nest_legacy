"""Tests for the Nest Legacy climate platform."""

from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODES,
    ATTR_PRESET_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    FAN_AUTO,
    FAN_ON,
    PRESET_ECO,
    PRESET_NONE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from . import setup_integration
from .const import THERMOSTAT_KEY, THERMOSTAT_SERIAL

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

ENTITY_ID = "climate.hallway_hallway_upstairs"

_SET_POINT_TYPE = nest_hvac_pb2.SetPointScheduleSettingsTrait.SetPointType


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.CLIMATE]


@pytest.fixture
def thermostat(observe_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return the thermostat's traits, to change before setting up."""
    return observe_data[THERMOSTAT_KEY]


def _trait(traits: dict[str, Any], trait_class: type[Any]) -> Any:
    """Return the thermostat trait of the given class."""
    return traits[trait_class.DESCRIPTOR.full_name]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All climate entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_fahrenheit_values_are_whole_degrees(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A Fahrenheit thermostat reports whole degrees, never 78.00000000001.

    The API reports float32 Celsius, which turns into long fractions once
    converted; see issues #21, #24, #46, #54 and #55.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    # 21.11 C is 69.998 F and 22.222222 C is exactly 72 F.
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 70
    assert state.attributes[ATTR_TEMPERATURE] == 72
    assert state.attributes[ATTR_TARGET_TEMP_STEP] == 1.0


async def test_celsius_values_keep_half_degrees(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    thermostat: dict[str, Any],
) -> None:
    """A Celsius thermostat keeps the half degree resolution the device offers."""
    _trait(
        thermostat, nest_hvac_pb2.DisplaySettingsTrait
    ).temperatureScale = (
        nest_hvac_pb2.DisplaySettingsTrait.TemperatureScale.TEMPERATURE_SCALE_C
    )
    target = _trait(thermostat, nest_hvac_pb2.TargetTemperatureSettingsTrait)
    target.targetTemperature.heatingTarget.value = 21.5
    hass.config.units = METRIC_SYSTEM
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_TEMPERATURE] == 21.5
    assert state.attributes[ATTR_TARGET_TEMP_STEP] == 0.5


@pytest.mark.parametrize(
    ("requested", "expected_celsius"),
    [
        (72, 22.22222222222222),
        # A half degree request is snapped to the whole degree the device accepts,
        # instead of being sent on and rejected; see issue #54.
        (72.5, 22.22222222222222),
        (73, 22.77777777777778),
    ],
)
async def test_set_temperature_rounds_to_whole_fahrenheit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    requested: float,
    expected_celsius: float,
) -> None:
    """Outbound setpoints are snapped to the device's native precision."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: requested},
        blocking=True,
    )

    device, data = mock_nest_client.async_set_device_data.call_args[0]
    assert device.serial_number == THERMOSTAT_SERIAL
    assert data["target_temperature"] == pytest.approx(expected_celsius)


async def test_set_temperature_range(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    thermostat: dict[str, Any],
) -> None:
    """Heat/cool mode sends both setpoints."""
    target = _trait(thermostat, nest_hvac_pb2.TargetTemperatureSettingsTrait)
    target.targetTemperature.setpointType = _SET_POINT_TYPE.SET_POINT_TYPE_RANGE
    target.targetTemperature.heatingTarget.value = 20.0
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == HVACMode.HEAT_COOL
    assert state.attributes[ATTR_TARGET_TEMP_LOW] == 68
    assert state.attributes[ATTR_TARGET_TEMP_HIGH] == 76
    assert state.attributes[ATTR_TEMPERATURE] is None

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: ENTITY_ID,
            ATTR_TARGET_TEMP_LOW: 67,
            ATTR_TARGET_TEMP_HIGH: 77,
        },
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["target_temperature_low"] == pytest.approx(19.444444444444443)
    assert data["target_temperature_high"] == pytest.approx(25)


async def test_heat_only_thermostat(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    thermostat: dict[str, Any],
) -> None:
    """A heat only device only offers heat; see issue #38."""
    _trait(
        thermostat, nest_hvac_pb2.HvacEquipmentCapabilitiesTrait
    ).hasStage1Cool = False
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_HVAC_MODES] == [HVACMode.OFF, HVACMode.HEAT]


async def test_hvac_action(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    thermostat: dict[str, Any],
) -> None:
    """Cooling equipment reports the cooling action."""
    hvac = _trait(thermostat, nest_hvac_pb2.HvacControlTrait)
    hvac.hvacState.coolStage1Active = True
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING


async def test_set_hvac_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Selecting a mode sends the Nest name for it."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, "hvac_mode": HVACMode.HEAT_COOL},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"hvac_mode": "range"}


@pytest.mark.parametrize(
    ("preset", "expected"),
    [(PRESET_ECO, "manual-eco"), (PRESET_NONE, "schedule")],
)
async def test_set_preset_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    preset: str,
    expected: str,
) -> None:
    """Eco is expressed as the eco mode the API expects."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: preset},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"eco": {"mode": expected}}


async def test_eco_setpoint_leaves_eco_first(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    thermostat: dict[str, Any],
) -> None:
    """Changing the setpoint while in eco asks the client to leave eco first."""
    _trait(
        thermostat, nest_hvac_pb2.EcoModeStateTrait
    ).ecoMode = nest_hvac_pb2.EcoModeStateTrait.EcoMode.ECO_MODE_MANUAL_ECO
    eco = nest_hvac_pb2.EcoModeSettingsTrait()
    eco.ecoTemperatureHeat.enabled = True
    eco.ecoTemperatureHeat.value.value = 12.0
    eco.ecoTemperatureCool.value.value = 26.0
    thermostat[nest_hvac_pb2.EcoModeSettingsTrait.DESCRIPTOR.full_name] = eco
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_ECO

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 20},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["exit_eco"] is True


async def test_fan_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Turning the fan on sets a timeout, turning it off clears it."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_FAN_MODE] == FAN_AUTO

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_FAN_MODE: FAN_ON},
        blocking=True,
    )
    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["fan_timer_timeout"] > 0

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_FAN_MODE: FAN_AUTO},
        blocking=True,
    )
    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"fan_timer_timeout": 0}


async def test_set_fan_timer(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The set_fan_timer action runs the fan for the requested time; issue #57."""
    await hass.services.async_call(
        DOMAIN,
        "set_fan_timer",
        {ATTR_ENTITY_ID: ENTITY_ID, "duration": {"minutes": 30}},
        blocking=True,
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data["fan_timer_timeout"] > 0


async def test_set_fan_timer_rejects_non_positive_duration(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """A zero or negative fan timer duration raises ServiceValidationError."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_fan_timer",
            {ATTR_ENTITY_ID: ENTITY_ID, "duration": {"minutes": 0}},
            blocking=True,
        )


async def test_set_fan_timer_without_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    thermostat: dict[str, Any],
) -> None:
    """A thermostat with no fan rejects the action instead of silently passing."""
    del thermostat[nest_hvac_pb2.FanControlCapabilitiesTrait.DESCRIPTOR.full_name]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_fan_timer",
            {ATTR_ENTITY_ID: ENTITY_ID, "duration": {"minutes": 30}},
            blocking=True,
        )

    # Home Assistant rejects the call on required_features before the entity's
    # own fan_not_supported guard can run.
    assert err.value.translation_key == "service_not_supported"


async def test_command_failure_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A failed command surfaces as a Home Assistant error."""
    mock_nest_client.async_set_device_data.side_effect = TimeoutError

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: ENTITY_ID, "hvac_mode": HVACMode.OFF},
            blocking=True,
        )

    assert err.value.translation_key == "set_data_failed"
