"""Tests for the Nest Legacy actions."""

from datetime import time, timedelta
from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    guest_pb2 as nest_guest_pb2,
)
import pytest

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from . import setup_integration
from .const import LOCK_KEY, LOCK_SERIAL, STRUCTURE_KEY

from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def platforms() -> list[Platform]:
    """The lock actions only need the lock platform."""
    return [Platform.LOCK]


def _lock_device_id(device_registry: dr.DeviceRegistry, entry: MockConfigEntry) -> str:
    """Return the Home Assistant device id of the lock."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, LOCK_SERIAL), entry.entry_id
    )
    assert device is not None
    return device.id


async def test_list_guests(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_data: dict[str, dict[str, Any]],
) -> None:
    """Guests are read out of the structure's protobuf traits."""
    guests = nest_guest_pb2.GuestsTrait()
    guest = guests.guests.add()
    guest.id.resourceId = "GUEST_1234"
    guest.name = "Guest"
    observe_data[STRUCTURE_KEY][nest_guest_pb2.GuestsTrait.DESCRIPTOR.full_name] = (
        guests
    )
    await setup_integration(hass, mock_config_entry)

    response = await hass.services.async_call(
        DOMAIN,
        "list_guests",
        {"config_entry_id": mock_config_entry.entry_id},
        blocking=True,
        return_response=True,
    )

    assert list(response["guests"]) == [STRUCTURE_KEY]


async def test_list_guests_without_any(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An account with no guests gets an empty mapping rather than an error."""
    response = await hass.services.async_call(
        DOMAIN,
        "list_guests",
        {"config_entry_id": init_integration.entry_id},
        blocking=True,
        return_response=True,
    )

    assert response == {"guests": {}}


async def test_set_user_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A weekly schedule is translated into the day bitmask the API wants."""
    await hass.services.async_call(
        DOMAIN,
        "set_user_schedule",
        {
            "device_id": _lock_device_id(device_registry, init_integration),
            "user_id": "GUEST_1234",
            "days_of_week": ["monday", "saturday"],
            "start_time": time(9, 30),
            "duration": timedelta(hours=2),
        },
        blocking=True,
    )

    device, user_id, daily, timebox = (
        mock_nest_client.async_set_user_schedule.call_args[0]
    )
    assert device.object_key == LOCK_KEY
    assert user_id == "GUEST_1234"
    assert daily == [
        {
            "days_of_week": [2, 64],
            "start_time": {"hour": 9, "minute": 30, "second": 0},
            "duration_seconds": 7200,
        }
    ]
    assert timebox is None


async def test_delete_user_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Deleting a schedule reaches the client."""
    await hass.services.async_call(
        DOMAIN,
        "delete_user_schedule",
        {
            "device_id": _lock_device_id(device_registry, init_integration),
            "user_id": "GUEST_1234",
        },
        blocking=True,
    )

    device, user_id = mock_nest_client.async_delete_user_schedule.call_args[0]
    assert device.object_key == LOCK_KEY
    assert user_id == "GUEST_1234"


async def test_unknown_device_is_rejected(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A device id that is not ours is reported as a validation error."""
    other = device_registry.async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={("other_domain", "not-a-nest-device")},
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_user_schedule",
            {"device_id": other.id, "user_id": "GUEST_1234"},
            blocking=True,
        )
