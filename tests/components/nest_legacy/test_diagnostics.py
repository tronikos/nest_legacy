"""Tests for the Nest Legacy diagnostics."""

from typing import Any

from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.diagnostics import _redact_identifiers
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
    get_diagnostics_for_device,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator


@pytest.fixture
def platforms() -> list[Platform]:
    """Diagnostics do not need any entities."""
    return [Platform.CLIMATE]


@pytest.mark.freeze_time("2026-01-01 00:00:00+00:00")
async def test_config_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """The config entry diagnostics are stable and redacted."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics == snapshot


async def test_credentials_are_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """The stored cookies and issue token never appear in a download."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert "OCAK=test" not in str(diagnostics)
    assert "iframerpc" not in str(diagnostics)


# Values from the app_launch fixture that identify the account, a device, a
# room or a camera stream, and so must not appear anywhere in a download.
IDENTIFYING_VALUES = [
    "09AA00AA00AA0AAA",
    "09AA00AA00AA0AA1",
    "18B430CCCCCC0001",
    "18B430CCCCCC0002",
    "00000000-0000-0000-0000-000000000001",
    "1234567890",
    "18b430000001",
    "nexusapi-test.dropcam.com",
    "home.nest.com/cameras",
    "Hallway Protect",
    "Bedroom Sensor",
    "Front Door",
    "Living Room",
    "Test Home",
    "where-hallway",
    "where-front-door",
]


async def test_identifying_values_are_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Serials, IDs, stream URLs and room names never appear in a download."""
    downloads = [
        await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    ]
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert devices
    downloads.extend(
        [
            await get_diagnostics_for_device(
                hass, hass_client, init_integration, device
            )
            for device in devices
        ]
    )

    for diagnostics in downloads:
        text = str(diagnostics)
        for value in IDENTIFYING_VALUES:
            assert value not in text


def test_redact_identifiers() -> None:
    """Identifiers in keys and references, addresses and links are replaced."""
    data = {
        "topaz.09AA01AC000000": {
            "last_seen_ip": "192.0.2.10",
            "ipv6": "2001:db8::1",
            "stream": "rtsps://stream.example.com/live",
            "software_version": "1.2.3.4",
            "swarm": ["device.09AA01AC000000"],
            "mode": "home",
        },
    }

    assert _redact_identifiers(data, ["09AA01AC000000"]) == {
        "topaz.**REDACTED_1**": {
            "last_seen_ip": "**REDACTED**",
            "ipv6": "**REDACTED**",
            "stream": "**REDACTED**",
            "software_version": "1.2.3.4",
            "swarm": ["device.**REDACTED_1**"],
            "mode": "home",
        },
    }


async def test_device_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """The per device diagnostics include the device's own raw data."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "09AA00AA00AA0AAA"), init_integration.entry_id
    )
    assert device is not None

    diagnostics: dict[str, Any] = await get_diagnostics_for_device(
        hass, hass_client, init_integration, device
    )

    assert diagnostics == snapshot
