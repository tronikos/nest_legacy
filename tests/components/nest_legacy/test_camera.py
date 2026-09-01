"""Tests for the Nest Legacy camera platform."""

from collections.abc import Generator
from unittest.mock import patch

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.CAMERA]


@pytest.fixture(autouse=True)
def stable_access_token() -> Generator[None]:
    """Keep the camera access token out of the snapshot diff."""
    with patch("random.SystemRandom.getrandbits", return_value=123123123123):
        yield


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All camera entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)
