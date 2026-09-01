"""Tests for the Nest Legacy diagnostics."""

from typing import Any

from custom_components.nest_legacy.const import DOMAIN
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
