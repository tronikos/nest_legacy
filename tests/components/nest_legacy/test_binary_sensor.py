"""Tests for the Nest Legacy binary sensor platform."""

from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.pynest.protobuf_gen.weave.trait import (
    security_pb2 as weave_security_pb2,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .const import LOCK_KEY, PROTECT_SERIAL

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

SMOKE_ENTITY = "binary_sensor.hallway_hallway_protect_smoke"
CO_ENTITY = "binary_sensor.hallway_hallway_protect_carbon_monoxide"
OCCUPANCY_ENTITY = "binary_sensor.hallway_hallway_thermostat_occupancy"
TAMPER_ENTITY = "binary_sensor.front_door_front_door_tamper"
PROTECT_CONNECTIVITY_ENTITY = "binary_sensor.hallway_hallway_protect_connectivity"
HUSHED_ENTITY = "binary_sensor.hallway_hallway_protect_alarm_hushed"
ALS_TEST_ENTITY = "binary_sensor.hallway_hallway_protect_ambient_light_sensor_test"
_TAMPER_KEY = weave_security_pb2.TamperTrait.DESCRIPTOR.full_name


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.BINARY_SENSOR]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All binary sensor entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_alarms_are_clear_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A Protect with nothing wrong reports every alarm off."""
    assert hass.states.get(SMOKE_ENTITY).state == STATE_OFF
    assert hass.states.get(CO_ENTITY).state == STATE_OFF


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize(
    ("field", "entity_id"),
    [("smoke_status", SMOKE_ENTITY), ("co_status", CO_ENTITY)],
)
async def test_alarms_trigger(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
    field: str,
    entity_id: str,
) -> None:
    """A non zero alarm status turns the matching sensor on."""
    app_launch_data["topaz.09AA00AA00AA0AA1"][field] = 2
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(entity_id).state == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_occupancy_follows_the_structure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """Occupancy is the inverse of the structure's away flag."""
    assert_entry = app_launch_data["structure.00000000-0000-0000-0000-000000000001"]
    assert_entry["away"] = True
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(OCCUPANCY_ENTITY).state == STATE_OFF


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_lock_tamper(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_data: dict[str, dict[str, Any]],
) -> None:
    """A tampered lock raises its tamper sensor."""
    observe_data[LOCK_KEY][_TAMPER_KEY] = weave_security_pb2.TamperTrait(
        tamperState=weave_security_pb2.TamperTrait.TamperState.TAMPER_STATE_TAMPERED
    )
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(TAMPER_ENTITY).state == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_connectivity_online(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An online device reports connected."""
    assert hass.states.get(PROTECT_CONNECTIVITY_ENTITY).state == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_connectivity_stays_available_when_offline(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
) -> None:
    """An offline device reports disconnected instead of going unavailable."""
    app_launch_data["widget_track.09AA00AA00AA0AA1"]["online"] = False
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(PROTECT_CONNECTIVITY_ENTITY).state == STATE_OFF
    # The device's other entities still go unavailable while it is offline.
    assert hass.states.get(SMOKE_ENTITY).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize(
    ("field", "value", "entity_id"),
    [
        ("hushed_state", True, HUSHED_ENTITY),
        ("component_als_test_passed", False, ALS_TEST_ENTITY),
    ],
)
async def test_protect_extras_turn_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    app_launch_data: dict[str, Any],
    field: str,
    value: bool,
    entity_id: str,
) -> None:
    """A hushed alarm or a failed component test turns its sensor on."""
    app_launch_data["topaz.09AA00AA00AA0AA1"][field] = value
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(entity_id).state == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_no_hush_sensor_on_protobuf_protect(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The protobuf Protect has no hush data, so it gets no hush sensor."""
    assert not entity_registry.async_get_entity_id(
        Platform.BINARY_SENSOR, "nest_legacy", f"{PROTECT_SERIAL}-hushed_state"
    )
    assert entity_registry.async_get_entity_id(
        Platform.BINARY_SENSOR, "nest_legacy", f"{PROTECT_SERIAL}-online"
    )
