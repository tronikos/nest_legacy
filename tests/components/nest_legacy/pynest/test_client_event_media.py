"""Tests for the stored event media of Protobuf-only cameras."""

from http import HTTPStatus
from typing import Any

from custom_components.nest_legacy.pynest.client import (
    NestClient,
    join_fragmented_mp4,
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


def _history_response() -> bytes:
    """Return a camera history answer with one event, as the API sends it."""
    history = _HISTORY_RESPONSE()
    event = history.cameraEventWindow.cameraEvent.add(eventId="event-1")
    event.eventType.append(
        _HISTORY_RESPONSE.CameraEventTimeWindow.EventType.EVENT_MOTION
    )
    event.startTime.seconds = EVENT_START
    event.endTime.seconds = EVENT_END
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
    client: NestClient, aioclient_mock: AiohttpClientMocker, **kwargs: Any
) -> list[dict[str, Any]]:
    """List the camera's events, which the media lookups depend on."""
    aioclient_mock.post(SEND_COMMAND_URL, content=_history_response(**kwargs))
    return await client.async_get_camera_events(_protobuf_camera())


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


def test_join_passes_through_what_it_cannot_parse() -> None:
    """Bytes that are not MP4 boxes are kept rather than dropped."""
    assert join_fragmented_mp4([b"not an mp4"]) == b"not an mp4"


@pytest.mark.parametrize(
    "manifest",
    [
        "not xml",
        _manifest(),
        _manifest(_period("later", "2026-09-21T15:00:00Z", "PT10S")),
    ],
)
def test_clip_not_found(manifest: str) -> None:
    """No clip is returned when the manifest holds nothing for the event."""
    assert select_event_clip_segments(manifest, EVENT_START, EVENT_END) == []


async def test_protobuf_event_clip(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The clip is the event's recording, joined from its video segments."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.get(
        MANIFEST_URL,
        text=_manifest(_period("during", "2026-09-21T14:13:18Z", "PT12S")),
    )
    fragments = FRAGMENTS_URL.format(period="during", stream=0)
    aioclient_mock.get(fragments + "0.m4v", content=_segment(b"1", b"2"))
    aioclient_mock.get(fragments + "1.m4v", content=_segment(b"3"))

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is not None
        chunks = [chunk async for chunk in response.content.iter_chunked(4)]

    assert b"".join(chunks) == join_fragmented_mp4(
        [_segment(b"1", b"2"), _segment(b"3")]
    )
    manifest_call = next(
        call for call in aioclient_mock.mock_calls if "dashmanifest" in str(call[1])
    )
    assert manifest_call[1].query.getall("types") == ["1", "2"]
    assert manifest_call[1].query["start_time"] == "2026-09-21T14:13:10.000Z"
    assert manifest_call[1].query["end_time"] == "2026-09-21T14:13:38.000Z"
    assert manifest_call[3]["Authorization"] == "Bearer google-access-token"


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


async def test_protobuf_event_manifest_failure(
    google_client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused manifest gives no clip."""
    await _list_events(google_client, aioclient_mock)
    aioclient_mock.get(MANIFEST_URL, status=HTTPStatus.FORBIDDEN)

    async with google_client.async_get_camera_event_media_stream(
        _protobuf_camera(), "event-1", format="mp4"
    ) as response:
        assert response is None


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
