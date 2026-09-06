"""Tests for the Nest Legacy camera platform."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.camera import async_get_image
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_snapshot_failure_raises_translated_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A snapshot the API refuses surfaces as a translated error."""
    mock_nest_client.async_get_camera_snapshot.side_effect = TimeoutError

    with pytest.raises(HomeAssistantError) as err:
        await async_get_image(hass, "camera.front_door_front_door_doorbell")

    assert err.value.translation_key == "camera_snapshot_failed"
