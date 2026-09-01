"""Tests for the Nest Legacy media source."""

from custom_components.nest_legacy.const import DOMAIN
import pytest

from homeassistant.components.media_source import (
    URI_SCHEME,
    Unresolvable,
    async_browse_media,
    async_resolve_media,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

CAMERA_SERIAL = "18B430CCCCCC0002"


@pytest.fixture
def platforms() -> list[Platform]:
    """Media browsing looks up camera entities."""
    return [Platform.CAMERA]


@pytest.fixture(autouse=True)
async def setup_media_source(hass: HomeAssistant) -> None:
    """Set up the media source integration."""
    assert await async_setup_component(hass, "media_source", {})


async def test_browse_lists_cameras(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The root of the media source lists the account's cameras."""
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")

    assert browse.title == "Cameras"
    assert [child.title for child in browse.children] == [
        "Front Door Doorbell",
        "Front Door Driveway",
    ]
    assert browse.children[0].identifier.endswith(CAMERA_SERIAL)


async def test_browse_days_for_camera(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Drilling into a camera offers the days that can be browsed."""
    browse = await async_browse_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}",
    )

    assert browse.children
    assert all(child.can_expand for child in browse.children)


async def test_resolve_media(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An event resolves to the clip proxy URL."""
    resolved = await async_resolve_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/event-1",
        None,
    )

    assert resolved.mime_type == "video/mp4"
    assert CAMERA_SERIAL in resolved.url
    assert resolved.url.endswith("/event-1/clip")


async def test_resolve_rejects_a_malformed_identifier(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A truncated identifier is rejected rather than producing a bad URL."""
    with pytest.raises(Unresolvable):
        await async_resolve_media(
            hass, f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}", None
        )


async def test_browse_unknown_config_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Browsing an entry that no longer exists is reported, not swallowed."""
    with pytest.raises(Unresolvable):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/does-not-exist/x")
