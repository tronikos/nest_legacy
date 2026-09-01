"""Tests for the Nest Legacy event platform."""

from typing import Any

from custom_components.nest_legacy.events import NEST_LEGACY_EVENT
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.event import ATTR_EVENT_TYPE, DoorbellEventType
from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

CHIME_ENTITY = "event.front_door_front_door_doorbell_chime"
MOTION_ENTITY = "event.front_door_front_door_doorbell_motion"
CAMERA_SERIAL = "18B430CCCCCC0002"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.EVENT]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All event entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


def _fire(hass: HomeAssistant, types: list[str], **extra: Any) -> None:
    """Fire the bus event the coordinator publishes for a camera event."""
    hass.bus.async_fire(
        NEST_LEGACY_EVENT,
        {
            "serial_number": CAMERA_SERIAL,
            "nest_event": {"id": "event-1", "types": types, **extra},
        },
    )


async def test_doorbell_press_uses_the_standard_ring_type(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A doorbell press is reported as the standard ring event type."""
    assert hass.states.get(CHIME_ENTITY).state == STATE_UNKNOWN

    _fire(hass, ["doorbell"])
    await hass.async_block_till_done()

    state = hass.states.get(CHIME_ENTITY)
    assert state is not None
    assert state.attributes[ATTR_EVENT_TYPE] == DoorbellEventType.RING
    assert state.attributes["nest_event_id"] == "event-1"


async def test_most_specific_type_wins(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An event carrying several types is reported as the richest one."""
    _fire(hass, ["motion", "person", "face"], face_name="Someone")
    await hass.async_block_till_done()

    state = hass.states.get(MOTION_ENTITY)
    assert state is not None
    assert state.attributes[ATTR_EVENT_TYPE] == "camera_face"
    assert state.attributes["all_event_types"] == ["motion", "person", "face"]
    assert state.attributes["face_name"] == "Someone"


async def test_events_for_other_devices_are_ignored(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An event for a different camera does not update this entity."""
    hass.bus.async_fire(
        NEST_LEGACY_EVENT,
        {
            "serial_number": "some-other-camera",
            "nest_event": {"id": "event-2", "types": ["doorbell"]},
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get(CHIME_ENTITY).state == STATE_UNKNOWN


async def test_unrelated_event_types_are_ignored(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A motion event does not ring the chime entity."""
    _fire(hass, ["motion"])
    await hass.async_block_till_done()

    assert hass.states.get(CHIME_ENTITY).state == STATE_UNKNOWN
    assert hass.states.get(MOTION_ENTITY).state != STATE_UNKNOWN
