"""Tests for the Nest Legacy media source."""

from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.pynest.exceptions import PynestException
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
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

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


DAY = "2026-01-02"


def _event(event_id: str, start_time: int, types: list[str]) -> dict[str, Any]:
    """Return one camera event as the client reports it."""
    return {
        "id": event_id,
        "start_time": start_time,
        "end_time": start_time + 10,
        "types": types,
    }


@pytest.fixture
def camera_events(mock_nest_client: AsyncMock) -> list[dict[str, Any]]:
    """Return a day's worth of camera events, and serve them to the client."""
    events = [
        _event(f"event-{index}", 1767312000 + index * 60, ["motion"])
        for index in range(25)
    ]
    events.append(_event("event-doorbell", 1767315600, ["doorbell"]))
    mock_nest_client.async_get_camera_events.return_value = events
    return events


async def test_browse_event_types_for_a_day(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    camera_events: list[dict[str, Any]],
) -> None:
    """A day lists the event types it holds and how many of each."""
    browse = await async_browse_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}",
    )

    assert [child.title for child in browse.children] == [
        "All Events (26)",
        "Doorbell Events (1)",
        "Motion Events (25)",
    ]


async def test_browse_a_day_without_events(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A quiet day lists nothing rather than an empty All Events folder."""
    mock_nest_client.async_get_camera_events.return_value = []

    browse = await async_browse_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}",
    )

    assert browse.children == []


async def test_events_are_paginated(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    camera_events: list[dict[str, Any]],
) -> None:
    """A busy day is split into pages, newest first."""
    browse = await async_browse_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}/all",
    )

    playable = [child for child in browse.children if child.can_play]
    assert len(playable) == 20
    assert playable[0].title.startswith("Doorbell at ")
    assert playable[0].thumbnail is not None

    next_page = browse.children[-1]
    assert next_page.can_expand
    assert next_page.title.startswith("Next Page")

    second = await async_browse_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{next_page.identifier}"
    )
    assert len([child for child in second.children if child.can_play]) == 6


async def test_filtering_by_event_type(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    camera_events: list[dict[str, Any]],
    mock_nest_client: AsyncMock,
) -> None:
    """Browsing one event type asks the API for just that type."""
    await async_browse_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}/doorbell",
    )

    assert mock_nest_client.async_get_camera_events.call_args.kwargs["types"] == [
        "doorbell"
    ]


async def test_browse_reports_an_api_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """An API failure while browsing is reported, not shown as an empty day."""
    mock_nest_client.async_get_camera_events.side_effect = PynestException("boom")

    with pytest.raises(Unresolvable):
        await async_browse_media(
            hass,
            f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}",
        )


@pytest.fixture
async def media_views(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Register the media views, which happens on first use of the source."""
    await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")


def _media_stream(payload: bytes) -> MagicMock:
    """Return a context manager yielding a response carrying the payload."""
    response = MagicMock()
    response.read = AsyncMock(return_value=payload)

    async def _iter_chunked(_size: int) -> Any:
        yield payload

    response.content.iter_chunked = _iter_chunked
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=response)
    stream.__aexit__ = AsyncMock(return_value=False)
    return stream


@pytest.mark.usefixtures("media_views")
async def test_clip_and_thumbnail_views(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The proxy views stream the clip and the thumbnail through."""
    mock_nest_client.async_get_camera_event_media_stream = MagicMock(
        return_value=_media_stream(b"payload")
    )
    client = await hass_client()

    base = (
        f"/api/nest_legacy/event_media/{init_integration.entry_id}/{CAMERA_SERIAL}"
        "/event-1"
    )
    clip = await client.get(f"{base}/clip")
    assert clip.status == HTTPStatus.OK
    assert await clip.read() == b"payload"

    thumbnail = await client.get(f"{base}/thumbnail")
    assert thumbnail.status == HTTPStatus.OK
    assert thumbnail.content_type == "image/jpeg"


@pytest.mark.usefixtures("media_views")
async def test_views_reject_an_unknown_camera(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """A serial number that is not a camera is not served."""
    client = await hass_client()

    response = await client.get(
        f"/api/nest_legacy/event_media/{init_integration.entry_id}/nope/event-1/clip"
    )

    assert response.status == HTTPStatus.NOT_FOUND


@pytest.mark.usefixtures("media_views")
async def test_clip_view_reports_an_api_failure(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A failure fetching the clip is a server error, not an empty response."""
    mock_nest_client.async_get_camera_event_media_stream = MagicMock(
        side_effect=PynestException("boom")
    )
    client = await hass_client()

    response = await client.get(
        f"/api/nest_legacy/event_media/{init_integration.entry_id}"
        f"/{CAMERA_SERIAL}/event-1/clip"
    )

    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
