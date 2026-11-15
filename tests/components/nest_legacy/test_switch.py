"""Tests for the Nest Legacy switch platform."""

from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
)
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
from .const import HEAT_LINK_SERIAL, TEMP_SENSOR_KEY, TEMP_SENSOR_SERIAL, THERMOSTAT_KEY

from pytest_homeassistant.common import MockConfigEntry, snapshot_platform

PATHLIGHT_ENTITY = "switch.hallway_hallway_protect_pathlight"
STREAMING_ENTITY = "switch.front_door_front_door_doorbell_streaming"
SENSOR_ENTITY = "switch.hallway_hallway_study_sensor_control_thermostat"
HOME_AWAY_ASSIST_ENTITY = "switch.hallway_hallway_heat_link_home_away_assist"


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


@pytest.mark.parametrize(
    ("service", "expected"),
    [(SERVICE_TURN_ON, True), (SERVICE_TURN_OFF, False)],
)
@pytest.mark.usefixtures("heat_link")
async def test_hot_water_home_away_assist(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    service: str,
    expected: bool,
) -> None:
    """The Home/Away Assist setting is its own switch now; see issue #68."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(HOME_AWAY_ASSIST_ENTITY).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: HOME_AWAY_ASSIST_ENTITY}, blocking=True
    )

    device, data = mock_nest_client.async_set_device_data.call_args[0]
    assert device.serial_number == HEAT_LINK_SERIAL
    assert data == {"hot_water_away_enabled": expected}


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
    observe_data: dict[str, dict[str, Any]],
) -> None:
    """A remote sensor can be made the active one; see issues #19 and #20."""
    rcs = observe_data[THERMOSTAT_KEY][
        nest_hvac_pb2.RemoteComfortSensingSettingsTrait.DESCRIPTOR.full_name
    ]
    rcs.activeRcsSelection.rcsSourceType = nest_hvac_pb2.RemoteComfortSensingSettingsTrait.RcsSourceType.RCS_SOURCE_TYPE_SINGLE_SENSOR
    rcs.activeRcsSelection.activeRcsSensor.resourceId = TEMP_SENSOR_KEY
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SENSOR_ENTITY).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SENSOR_ENTITY}, blocking=True
    )

    _, data = mock_nest_client.async_set_device_data.call_args[0]
    assert data == {"is_active_sensor": False}


@pytest.mark.parametrize("include_kryptonite_bucket", [True, False])
async def test_rest_thermostat_remote_sensor_control(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
    observe_data: dict[str, dict[str, Any]],
    include_kryptonite_bucket: bool,
) -> None:
    """A sensor paired with a REST thermostat exposes the active sensor switch; see issue #103."""
    del observe_data[THERMOSTAT_KEY]
    app_launch_data["device.15AA00AA00AA0AAA"] = {
        "serial_number": "15AA00AA00AA0AAA",
        "where_id": "where-hallway",
        "temperature_scale": "C",
    }
    app_launch_data["shared.15AA00AA00AA0AAA"] = {
        "target_temperature": 21.0,
        "target_temperature_type": "heat",
        "current_temperature": 20.0,
        "can_heat": True,
    }
    app_launch_data["track.15AA00AA00AA0AAA"] = {"online": True}
    app_launch_data["rcs_settings.15AA00AA00AA0AAA"] = {
        "associated_rcs_sensors": [f"kryptonite.{TEMP_SENSOR_SERIAL}"],
        "active_rcs_sensors": [],
    }
    if include_kryptonite_bucket:
        app_launch_data[f"kryptonite.{TEMP_SENSOR_SERIAL}"] = {
            "serial_number": TEMP_SENSOR_SERIAL,
            "where_id": "where-hallway",
            "description": "Study Sensor",
            "current_temperature": 19.5,
            "battery_level": 71,
            "last_updated_at": 9999999999,
        }

    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(SENSOR_ENTITY)
    assert state is not None
    assert state.state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SENSOR_ENTITY}, blocking=True
    )

    call = mock_nest_client.async_set_device_data.call_args
    device, data = call.args
    assert device.serial_number == TEMP_SENSOR_SERIAL
    assert device.is_protobuf is (not include_kryptonite_bucket)
    assert device.associated_thermostat_object_key == "device.15AA00AA00AA0AAA"
    assert data == {"is_active_sensor": True}
    assert call.kwargs == {"current_traits": None}


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
