"""Tests for the stored event media of Protobuf-only cameras."""

from http import HTTPStatus
import logging
from typing import Any
from unittest.mock import patch

from custom_components.nest_legacy.pynest import client as client_module
from custom_components.nest_legacy.pynest.client import NestClient
from custom_components.nest_legacy.pynest.exceptions import NotAuthenticatedException
from custom_components.nest_legacy.pynest.media import (
    is_camera_frontend_url,
    join_fragmented_mp4,
    parse_dash_duration,
    select_event_clip_segments,
)
from custom_components.nest_legacy.pynest.models import NestCamera
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    history_pb2 as nest_history_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nestlabs.gateway import v1_pb2
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .test_client import (
    GOOGLE_TOKEN_RESPONSE,
    ISSUE_TOKEN_URL,
    JWT_URL,
    SESSION_RESPONSE,
    SESSION_URL,
)

from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

DEVICE_ID = "DEVICE_0000000000000003"
SEND_COMMAND_URL = (
    "https://grpc-web.production.nest.com/nestlabs.gateway.v1.ResourceApi/SendCommand"
)
MANIFEST_URL = (
    "https://nest-camera-frontend.googleapis.com/dashmanifest/namespace"
    f"/nest-phoenix-prod/device/{DEVICE_ID}"
)
FRAGMENTS_URL = (
    "https://nest-camera-frontend.googleapis.com/download/recordings/prod"
    f"/devices/{DEVICE_ID}/periods/{{period}}/streams/{{stream}}/fragments/"
)

# 2026-09-21T14:13:20Z, an arbitrary moment for the event under test.
EVENT_START = 1790000000
EVENT_END = EVENT_START + 8


_HISTORY_RESPONSE = (
    nest_history_pb2.CameraObservationHistoryTrait.CameraObservationHistoryResponse
)


def _period(
    period_id: str,
    program_date_time: str,
    duration: str,
    *,
    widths: tuple[int, ...] = (1920,),
    segments: int = 2,
) -> str:
    """Return one DASH Period with a video and an audio adaptation set."""
    representations = "".join(
        f"<Representation id='{width}' width='{width}' height='{width * 9 // 16}'>"
        f"<BaseURL>{FRAGMENTS_URL.format(period=period_id, stream=index)}</BaseURL>"
        "<SegmentList>"
        + "".join(f"<SegmentURL media='{n}.m4v'/>" for n in range(segments))
        + "</SegmentList></Representation>"
        for index, width in enumerate(widths)
    )
    return (
        f"<Period programDateTime='{program_date_time}' duration='{duration}'>"
        f"<AdaptationSet contentType='video' mimeType='video/mp4'>{representations}"
        "</AdaptationSet>"
        "<AdaptationSet contentType='audio' mimeType='audio/mp4'>"
        "<Representation id='audio'>"
        f"<BaseURL>{FRAGMENTS_URL.format(period=period_id, stream=9)}</BaseURL>"
        "<SegmentList><SegmentURL media='0.m4a'/></SegmentList>"
        "</Representation></AdaptationSet></Period>"
    )


def _video_period(program_date_time: str, representation: str) -> str:
    """Return a DASH Period with one hand-written video representation."""
    return (
        f"<Period programDateTime='{program_date_time}' duration='PT12S'>"
        "<AdaptationSet contentType='video' mimeType='video/mp4'>"
        f"{representation}</AdaptationSet></Period>"
    )


def _box(box_type: bytes, payload: bytes = b"") -> bytes:
    """Return one MP4 box."""
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _segment(*fragments: bytes) -> bytes:
    """Return a segment as the camera frontend serves it: a complete fMP4 file."""
    body = b"".join(_box(b"moof", f) + _box(b"mdat", f) for f in fragments)
    return (
        _box(b"ftyp", b"iso5")
        + _box(b"moov", b"init")
        + body
        + _box(b"free")
        + _box(b"mfra", b"index")
    )


def _manifest(*periods: str, namespace: bool = True) -> str:
    """Return a DASH manifest holding these periods."""
    xmlns = " xmlns='urn:mpeg:dash:schema:mpd:2011'" if namespace else ""
    return f"<?xml version='1.0'?><MPD{xmlns} type='static'>{''.join(periods)}</MPD>"


def _protobuf_camera() -> NestCamera:
    """Return a Protobuf-only camera."""
    return NestCamera(
        object_key=DEVICE_ID,
        serial_number="18B430DDDDDD0002",
        name="Camera",
        is_protobuf=True,
    )


def _history_response(
    events: tuple[tuple[str, int, int | None], ...] = (
        ("event-1", EVENT_START, EVENT_END),
    ),
) -> bytes:
    """Return a camera history answer, as the API sends it.

    Each event is (id, start, end); an end of None is left unset, as the API
    does for an event that is still going on.
    """
    history = _HISTORY_RESPONSE()
    for event_id, start, end in events:
        event = history.cameraEventWindow.cameraEvent.add(eventId=event_id)
        event.eventType.append(
            _HISTORY_RESPONSE.CameraEventTimeWindow.EventType.EVENT_MOTION
        )
        event.startTime.seconds = start
        if end is not None:
            event.endTime.seconds = end
    response = v1_pb2.SendCommandResponse()
    operation = response.sendCommandResponse.add().traitOperations.add()
    operation.event.event.Pack(history, type_url_prefix="type.nestlabs.com/")
    return response.SerializeToString()


@pytest.fixture
async def google_client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> NestClient:
    """Return a client authenticated with Google credentials."""
    aioclient_mock.get(ISSUE_TOKEN_URL, json=GOOGLE_TOKEN_RESPONSE)
    aioclient_mock.post(JWT_URL, json={"jwt": "nest-jwt"})
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    client = NestClient(async_get_clientsession(hass))
    await client.async_authenticate_with_google_credentials(ISSUE_TOKEN_URL, "SID=a")
    aioclient_mock.clear_requests()
    return client


async def _list_events(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    history: bytes | None = None,
) -> list[dict[str, Any]]:
    """List the camera's events, which the media lookups depend on."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(SEND_COMMAND_URL, content=history or _history_response())
    return await client.async_get_camera_events(_protobuf_camera())


def _serve_clip(aioclient_mock: AiohttpClientMocker) -> None:
    """Serve the manifest and segments of the recording holding event-1."""
    aioclient_mock.get(
        MANIFEST_URL,
        text=_manifest(_period("during", "2026-09-21T14:13:18Z", "PT12S")),
    )
    fragments = FRAGMENTS_URL.format(period="during", stream=0)
    aioclient_mock.get(fragments + "0.m4v", content=_segment(b"1", b"2"))
    aioclient_mock.get(fragments + "1.m4v", content=_segment(b"3"))


def _manifest_call(aioclient_mock: AiohttpClientMocker) -> Any:
    """Return the request made for the manifest."""
    return next(
        call for call in aioclient_mock.mock_calls if "dashmanifest" in str(call[1])
    )


# --- Manifest -------------------------------------------------------------


def test_clip_is_the_period_that_overlaps_the_event_most() -> None:
    """A neighbouring recording in the padded window is not picked."""
    manifest = _manifest(
        # Ends two seconds into the event.
        _period("before", "2026-09-21T14:13:12.000Z", "PT10S"),
        # Holds the whole event.
        _period("during", "2026-09-21T14:13:18.500Z", "PT12.25S"),
    )

    segments = select_event_clip_segments(manifest, EVENT_START, EVENT_END)

    assert segments == [
        FRAGMENTS_URL.format(period="during", stream=0) + "0.m4v",
        FRAGMENTS_URL.format(period="during", stream=0) + "1.m4v",
    ]


def test_clip_uses_the_widest_video_and_no_audio() -> None:
    """The full-resolution video is joined; the audio set is a separate track."""
    manifest = _manifest(
        _period("during", "2026-09-21T14:13:18Z", "PT12S", widths=(640, 1920))
    )

    segments = select_event_clip_segments(manifest, EVENT_START, EVENT_END)

    assert segments == [
        FRAGMENTS_URL.format(period="during", stream=1) + "0.m4v",
        FRAGMENTS_URL.format(period="during", stream=1) + "1.m4v",
    ]


def test_clip_manifest_without_namespace() -> None:
    """Elements are matched by name whether or not the manifest is namespaced."""
    manifest = _manifest(
        _period("during", "2026-09-21T14:13:18Z", "PT1M2.5S"), namespace=False
    )

    assert len(select_event_clip_segments(manifest, EVENT_START, EVENT_END)) == 2


def test_clip_for_an_event_without_duration() -> None:
    """An event that has no end yet is matched to the period it falls in."""
    manifest = _manifest(
        _period("other", "2026-09-21T14:12:00Z", "PT10S"),
        _period("during", "2026-09-21T14:13:18Z", "PT12S"),
    )

    segments = select_event_clip_segments(manifest, EVENT_START, EVENT_START)

    assert segments[0].startswith(FRAGMENTS_URL.format(period="during", stream=0))


@pytest.mark.parametrize(
    "period",
    [
        # Ends exactly when the event starts.
        _period("before", "2026-09-21T14:13:10Z", "PT10S"),
        # Starts exactly when the event ends.
        _period("after", "2026-09-21T14:13:28Z", "PT10S"),
    ],
)
def test_clip_ignores_a_period_that_only_touches_the_event(period: str) -> None:
    """A recording that shares only a boundary with the event is not its clip."""
    assert select_event_clip_segments(_manifest(period), EVENT_START, EVENT_END) == []


def test_clip_period_without_timezone_is_utc() -> None:
    """A programDateTime without an offset is read as UTC."""
    manifest = _manifest(_period("during", "2026-09-21T14:13:18", "PT12S"))

    assert len(select_event_clip_segments(manifest, EVENT_START, EVENT_END)) == 2


def test_clip_skips_unreadable_representations() -> None:
    """A width that is not a number counts as 0, and blank URLs are trimmed."""
    fragments = FRAGMENTS_URL.format(period="during", stream=0)
    manifest = _manifest(
        _video_period(
            "2026-09-21T14:13:18Z",
            f"<Representation width='wide'><BaseURL>\n  {fragments}\n</BaseURL>"
            "<SegmentList><SegmentURL media=' 0.m4v '/></SegmentList>"
            "</Representation>",
        )
    )

    assert select_event_clip_segments(manifest, EVENT_START, EVENT_END) == [
        fragments + "0.m4v"
    ]


def test_clip_resolves_relative_urls_against_the_manifest() -> None:
    """A relative BaseURL is resolved against the URL the manifest came from."""
    manifest = _manifest(
        _video_period(
            "2026-09-21T14:13:18Z",
            "<Representation width='1920'><BaseURL>/download/p/</BaseURL>"
            "<SegmentList><SegmentURL media='0.m4v'/></SegmentList>"
            "</Representation>",
        )
    )

    assert select_event_clip_segments(
        manifest, EVENT_START, EVENT_END, base_url=MANIFEST_URL
    ) == ["https://nest-camera-frontend.googleapis.com/download/p/0.m4v"]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com/download/",
        "http://nest-camera-frontend.googleapis.com/download/",
        "https://nest-camera-frontend.googleapis.com.example.com/download/",
    ],
)
def test_clip_rejects_segments_outside_the_camera_frontend(base_url: str) -> None:
    """The access token is never sent to a host the manifest names elsewhere."""
    manifest = _manifest(
        _video_period(
            "2026-09-21T14:13:18Z",
            f"<Representation width='1920'><BaseURL>{base_url}</BaseURL>"
            "<SegmentList><SegmentURL media='0.m4v'/></SegmentList>"
            "</Representation>",
        )
    )

    assert select_event_clip_segments(manifest, EVENT_START, EVENT_END) == []


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://nest-camera-frontend.googleapis.com/x.m4v", True),
        ("https://other.googleapis.com/x.m4v", True),
        ("http://nest-camera-frontend.googleapis.com/x.m4v", False),
        ("https://googleapis.com.example.com/x.m4v", False),
        ("https://example.com/x.m4v", False),
    ],
)
def test_camera_frontend_url(url: str, allowed: bool) -> None:
    """Only HTTPS URLs on Google's API hosts get the access token."""
    assert is_camera_frontend_url(url) is allowed


@pytest.mark.parametrize(
    "manifest",
    [
        "not xml",
        _manifest(),
        _manifest(_period("later", "2026-09-21T15:00:00Z", "PT10S")),
        _manifest(_period("bad-time", "yesterday", "PT10S")),
    ],
)
def test_clip_not_found(manifest: str) -> None:
    """No clip is returned when the manifest holds nothing for the event."""
    assert select_event_clip_segments(manifest, EVENT_START, EVENT_END) == []


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("PT8.761S", 8.761),
        ("PT1M2.5S", 62.5),
        ("PT1H", 3600.0),
        ("P0DT8.761S", 8.761),
        ("P1D", 86400.0),
        ("PT.S", 0.0),
        ("PT1..2S", 0.0),
        ("8S", 0.0),
        ("", 0.0),
        (None, 0.0),
    ],
)
def test_parse_dash_duration(value: str | None, seconds: float) -> None:
    """Durations are read as ISO 8601; anything else is no duration."""
    assert parse_dash_duration(value) == pytest.approx(seconds)


# --- Joining segments -----------------------------------------------------


def test_join_keeps_one_header_and_all_fragments() -> None:
    """The joined clip is one fMP4: one ftyp and moov, every moof and mdat."""
    joined = join_fragmented_mp4([_segment(b"1", b"2"), _segment(b"3")])

    assert joined == (
        _box(b"ftyp", b"iso5")
        + _box(b"moov", b"init")
        + _box(b"moof", b"1")
        + _box(b"mdat", b"1")
        + _box(b"moof", b"2")
        + _box(b"mdat", b"2")
        + _box(b"moof", b"3")
        + _box(b"mdat", b"3")
    )


@pytest.mark.parametrize(
    "data",
    [
        b"not an mp4",
        b"short",
        # A box, then trailing bytes too short for another box header.
        _box(b"moof", b"1") + b"tail",
    ],
)
def test_join_passes_through_what_it_cannot_parse(data: bytes) -> None:
    """Bytes that are not MP4 boxes are kept rather than dropped."""
    assert join_fragmented_mp4([data]) == data


def test_join_follows_64_bit_box_sizes() -> None:
    """A box with a 64-bit largesize is followed past its 16-byte header."""
    largesize = (1).to_bytes(4, "big") + b"mdat" + (20).to_bytes(8, "big") + b"data"
    segment = _box(b"ftyp", b"iso5") + largesize + _box(b"mfra")

    assert join_fragmented_mp4([segment]) == _box(b"ftyp", b"iso5") + largesize


def test_join_rejects_a_64_bit_size_below_its_header() -> None:
    """A largesize smaller than the 16-byte header is not followed."""
    broken = (1).to_bytes(4, "big") + b"mdat" + (12).to_bytes(8, "big")

    assert join_fragmented_mp4([broken]) == broken


# --- Client ---------------------------------------------------------------


async def test_protobuf_event_clip(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The clip is the event's recording, joined from its video segments."""
    await _list_events(google_client, aioclient_mock)
    _serve_clip(aioclient_mock)

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is not None
        chunks = [chunk async for chunk in response.content.iter_chunked(4)]
        assert await response.read() == b"".join(chunks)

    assert b"".join(chunks) == join_fragmented_mp4(
        [_segment(b"1", b"2"), _segment(b"3")]
    )
    manifest_call = _manifest_call(aioclient_mock)
    assert manifest_call[1].query.getall("types") == ["1", "2"]
    assert manifest_call[1].query["start_time"] == "2026-09-21T14:13:10.000Z"
    assert manifest_call[1].query["end_time"] == "2026-09-21T14:13:38.000Z"
    assert manifest_call[3]["Authorization"] == "Bearer google-access-token"


async def test_protobuf_event_clip_without_end_time(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An event with no end yet asks for the window around its start."""
    events = await _list_events(
        google_client,
        aioclient_mock,
        _history_response((("event-1", EVENT_START, None),)),
    )
    assert events[0]["end_time"] == 0.0
    _serve_clip(aioclient_mock)

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is not None

    manifest_call = _manifest_call(aioclient_mock)
    assert manifest_call[1].query["start_time"] == "2026-09-21T14:13:10.000Z"
    assert manifest_call[1].query["end_time"] == "2026-09-21T14:13:30.000Z"


async def test_protobuf_event_clip_segment_failure(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A clip with a missing segment is not served half."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.get(
        MANIFEST_URL,
        text=_manifest(_period("during", "2026-09-21T14:13:18Z", "PT12S")),
    )
    fragments = FRAGMENTS_URL.format(period="during", stream=0)
    aioclient_mock.get(fragments + "0.m4v", content=_segment(b"1"))
    aioclient_mock.get(fragments + "1.m4v", status=HTTPStatus.INTERNAL_SERVER_ERROR)

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is None


async def test_protobuf_event_manifest_forbidden(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A forbidden manifest gives no clip, without re-authenticating."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.get(MANIFEST_URL, status=HTTPStatus.FORBIDDEN)

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is None


async def test_protobuf_event_manifest_not_found_is_not_an_error(
    google_client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A recording that is not uploaded yet is no error in the log."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.get(MANIFEST_URL, status=HTTPStatus.NOT_FOUND)

    with caplog.at_level(logging.DEBUG):
        async with google_client.async_get_camera_event_media_stream(
            _protobuf_camera(), "event-1", format="mp4"
        ) as response:
            assert response is None

    assert "not found" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_protobuf_event_manifest_without_clip(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A manifest without a recording for the event gives no clip."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.get(MANIFEST_URL, text=_manifest())

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is None


@pytest.mark.parametrize("rejected", ["manifest", "segment"])
async def test_protobuf_event_media_rejected_token(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker, rejected: str
) -> None:
    """A 401 means the access token is stale, so the caller can re-authenticate."""
    await _list_events(google_client, aioclient_mock)
    if rejected == "manifest":
        aioclient_mock.get(MANIFEST_URL, status=HTTPStatus.UNAUTHORIZED)
    else:
        aioclient_mock.get(
            MANIFEST_URL,
            text=_manifest(_period("during", "2026-09-21T14:13:18Z", "PT12S")),
        )
        fragments = FRAGMENTS_URL.format(period="during", stream=0)
        aioclient_mock.get(fragments + "0.m4v", status=HTTPStatus.UNAUTHORIZED)

    with pytest.raises(NotAuthenticatedException):
        async with google_client.async_get_camera_event_media_stream(
            _protobuf_camera(), "event-1", format="mp4"
        ):
            pass


async def test_protobuf_event_clip_too_large(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A recording larger than the in-memory bound is not served."""
    await _list_events(google_client, aioclient_mock)
    _serve_clip(aioclient_mock)

    with patch.object(client_module, "_MAX_EVENT_CLIP_BYTES", 16):
        async with google_client.async_get_camera_event_media_stream(
            _protobuf_camera(), "event-1", format="mp4"
        ) as response:
            assert response is None


async def test_protobuf_events_listed_last_stay_playable(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Browsing an older day after a full cache keeps that day's events."""
    recent = tuple(
        (f"recent-{n}", EVENT_START + 86400 + n * 60, EVENT_START + 86400 + n * 60 + 8)
        for n in range(500)
    )
    await _list_events(google_client, aioclient_mock, _history_response(recent))
    await _list_events(google_client, aioclient_mock)
    _serve_clip(aioclient_mock)

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is not None

    # The event listed longest ago made room, however recent it is.
    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "recent-0", format="mp4"
    ) as response:
        assert response is None


async def test_protobuf_events_beyond_the_cache_keep_the_newest(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """If one listing holds more events than the cache, its newest are kept."""
    events = tuple(
        (f"event-{n}", EVENT_START - n * 60, EVENT_START - n * 60 + 8)
        for n in range(1, 502)
    )
    await _list_events(google_client, aioclient_mock, _history_response(events))
    aioclient_mock.get(MANIFEST_URL, text=_manifest())

    # The newest event is still known: its manifest is fetched.
    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ):
        pass
    assert any("dashmanifest" in str(call[1]) for call in aioclient_mock.mock_calls)

    # The oldest is not: nothing is fetched for it.
    aioclient_mock.clear_requests()
    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-501", format="mp4"
    ) as response:
        assert response is None
    assert not aioclient_mock.mock_calls


async def test_protobuf_event_media_for_an_unknown_event(
    google_client: NestClient,
) -> None:
    """Media is only served for events the client has listed."""
    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is None


async def test_protobuf_event_has_no_thumbnail(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The events carry no snapshot URL, so no thumbnail is fetched."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.clear_requests()

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", height=92, format="jpeg"
    ) as response:
        assert response is None
    assert not aioclient_mock.mock_calls


async def test_protobuf_event_media_needs_a_google_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A client signed in with a legacy Nest token has no event media."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    client = NestClient(async_get_clientsession(hass))
    await client._async_get_session("test-token")
    await _list_events(client, aioclient_mock)

    async with client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is None
