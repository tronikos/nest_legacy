"""Tests for the Nest Legacy media source."""

import dataclasses
from http import HTTPStatus
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.pynest.exceptions import (
    NotAuthenticatedException,
    PynestException,
)
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

from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import ClientSessionGenerator

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


@pytest.mark.usefixtures("media_views")
async def test_media_source_handles_client_errors_and_malformed_paths(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """Network errors and invalid dates/pages raise clean errors instead of 500 tracebacks."""
    with pytest.raises(Unresolvable):
        await async_browse_media(
            hass,
            f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/not-a-date",
        )
    with pytest.raises(Unresolvable):
        await async_browse_media(
            hass,
            f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}/all/not-an-int",
        )

    mock_nest_client.async_get_camera_events.side_effect = ClientError("network down")
    with pytest.raises(Unresolvable):
        await async_browse_media(
            hass,
            f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}/all",
        )

    mock_nest_client.async_get_camera_event_media_stream = MagicMock(
        side_effect=TimeoutError
    )
    client = await hass_client()
    response = await client.get(
        f"/api/nest_legacy/event_media/{init_integration.entry_id}"
        f"/{CAMERA_SERIAL}/event-1/thumbnail"
    )
    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR


def _make_protobuf(entry: MockConfigEntry) -> None:
    """Turn the test camera into a Protobuf-only camera."""
    data = entry.runtime_data.data
    data[CAMERA_SERIAL] = dataclasses.replace(data[CAMERA_SERIAL], is_protobuf=True)


async def test_protobuf_camera_events_have_thumbnails(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    camera_events: list[dict[str, Any]],
) -> None:
    """Protobuf camera events get a thumbnail URL, served from the preview."""
    _make_protobuf(init_integration)

    browse = await async_browse_media(
        hass,
        f"{URI_SCHEME}{DOMAIN}/{init_integration.entry_id}/{CAMERA_SERIAL}/{DAY}/all",
    )

    playable = [child for child in browse.children if child.can_play]
    assert playable
    assert all(child.thumbnail.endswith("/thumbnail") for child in playable)


def _thumbnail_url(entry: MockConfigEntry) -> str:
    """Return the thumbnail URL of event-1."""
    return (
        f"/api/nest_legacy/event_media/{entry.entry_id}/{CAMERA_SERIAL}"
        "/event-1/thumbnail"
    )


@pytest.mark.usefixtures("media_views")
async def test_protobuf_thumbnail_is_the_first_frame(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """The thumbnail is decoded from the preview once, then kept."""
    _make_protobuf(init_integration)
    mock_nest_client.async_get_camera_event_preview = AsyncMock(return_value=b"segment")
    client = await hass_client()
    paths: list[str] = []

    async def _get_image(_hass: HomeAssistant, path: str, **kwargs: Any) -> bytes:
        paths.append(path)
        assert await hass.async_add_executor_job(Path(path).read_bytes) == b"segment"
        return b"jpeg"

    with patch(
        "custom_components.nest_legacy.media_source.async_get_image",
        side_effect=_get_image,
    ) as get_image:
        first = await client.get(_thumbnail_url(init_integration))
        second = await client.get(_thumbnail_url(init_integration))

    for response in (first, second):
        assert response.status == HTTPStatus.OK
        assert response.content_type == "image/jpeg"
        assert await response.read() == b"jpeg"
    mock_nest_client.async_get_camera_event_preview.assert_awaited_once()
    get_image.assert_called_once()
    assert paths[0].endswith(".mp4")
    assert not os.path.exists(paths[0])


@pytest.mark.usefixtures("media_views")
@pytest.mark.parametrize(
    "preview",
    [AsyncMock(return_value=None), AsyncMock(side_effect=ClientError("boom"))],
)
async def test_protobuf_thumbnail_not_found(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    preview: AsyncMock,
) -> None:
    """An event without a usable recording has no thumbnail, and no error."""
    _make_protobuf(init_integration)
    mock_nest_client.async_get_camera_event_preview = preview
    client = await hass_client()

    with patch(
        "custom_components.nest_legacy.media_source.async_get_image"
    ) as get_image:
        response = await client.get(_thumbnail_url(init_integration))

    assert response.status == HTTPStatus.NOT_FOUND
    get_image.assert_not_called()


@pytest.mark.usefixtures("media_views")
async def test_protobuf_thumbnail_reauthenticates_once(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A token rejected for the preview is renewed and the preview retried."""
    _make_protobuf(init_integration)
    mock_nest_client.async_get_camera_event_preview = AsyncMock(
        side_effect=[NotAuthenticatedException("401"), b"segment"]
    )
    client = await hass_client()
    coordinator = init_integration.runtime_data

    with (
        patch.object(
            coordinator, "async_reauthenticate", AsyncMock()
        ) as reauthenticate,
        patch(
            "custom_components.nest_legacy.media_source.async_get_image",
            AsyncMock(return_value=b"jpeg"),
        ),
    ):
        response = await client.get(_thumbnail_url(init_integration))

    assert response.status == HTTPStatus.OK
    reauthenticate.assert_awaited_once_with(force=True)


def _rejected_stream() -> MagicMock:
    """Return a context manager that fails as a rejected access token does."""
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(side_effect=NotAuthenticatedException("401"))
    stream.__aexit__ = AsyncMock(return_value=False)
    return stream


@pytest.mark.usefixtures("media_views")
async def test_clip_view_reauthenticates_once_on_a_rejected_token(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A token rejected before it expires is renewed and the clip retried."""
    mock_nest_client.async_get_camera_event_media_stream = MagicMock(
        side_effect=[_rejected_stream(), _media_stream(b"payload")]
    )
    client = await hass_client()
    coordinator = init_integration.runtime_data

    with patch.object(
        coordinator, "async_reauthenticate", AsyncMock()
    ) as reauthenticate:
        response = await client.get(
            f"/api/nest_legacy/event_media/{init_integration.entry_id}"
            f"/{CAMERA_SERIAL}/event-1/clip"
        )

    assert response.status == HTTPStatus.OK
    assert await response.read() == b"payload"
    reauthenticate.assert_awaited_once_with(force=True)


@pytest.mark.usefixtures("media_views")
async def test_clip_view_gives_up_after_one_reauthentication(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> None:
    """A token that is still rejected after renewing it is a server error."""
    mock_nest_client.async_get_camera_event_media_stream = MagicMock(
        side_effect=[_rejected_stream(), _rejected_stream()]
    )
    client = await hass_client()
    coordinator = init_integration.runtime_data

    with patch.object(
        coordinator, "async_reauthenticate", AsyncMock()
    ) as reauthenticate:
        response = await client.get(
            f"/api/nest_legacy/event_media/{init_integration.entry_id}"
            f"/{CAMERA_SERIAL}/event-1/clip"
        )

    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
    reauthenticate.assert_awaited_once_with(force=True)
