"""Stored event media of Protobuf-only cameras.

These cameras are not in the legacy REST API, so nexusapi has nothing for them.
The Home app plays their history from a DASH manifest on the camera frontend,
which accepts the Google access token the integration already gets when it
authenticates. This module reads that manifest and joins the segments of one
recording into a single fragmented MP4.
"""

from collections.abc import AsyncIterator, Iterator
import datetime as dt
import logging
import re
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

_LOGGER = logging.getLogger(__package__)

CAMERA_FRONTEND_HOST = "nest-camera-frontend.googleapis.com"
CAMERA_FRONTEND_URL = f"https://{CAMERA_FRONTEND_HOST}"
DASH_MANIFEST_URL_FORMAT = (
    CAMERA_FRONTEND_URL + "/dashmanifest/namespace/nest-phoenix-prod/device/{device_id}"
)

# ISO 8601 duration as DASH uses it, e.g. PT8.761S, PT1M2.5S or P0DT8.761S.
_NUMBER = r"(\d+(?:\.\d+)?)"
_DASH_DURATION = re.compile(
    rf"P(?:{_NUMBER}D)?(?:T(?:{_NUMBER}H)?(?:{_NUMBER}M)?(?:{_NUMBER}S)?)?"
)

# Boxes that describe or index one segment file. A joined clip keeps the
# first segment's ftyp and moov and drops the rest, so it is one stream.
_MP4_SEGMENT_HEADER_BOXES = frozenset({b"ftyp", b"moov"})
_MP4_SEGMENT_INDEX_BOXES = frozenset({b"mfra", b"free", b"sidx", b"styp"})


def iso_utc(timestamp: float) -> str:
    """Format a Unix timestamp the way the DASH manifest endpoint expects."""
    return (
        dt.datetime.fromtimestamp(timestamp, tz=dt.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_dash_duration(value: str | None) -> float:
    """Parse a DASH duration into seconds, or 0.0 if it is not one."""
    match = _DASH_DURATION.fullmatch(value or "")
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def is_camera_frontend_url(url: str) -> bool:
    """Return whether the Google access token may be sent to this URL."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host == CAMERA_FRONTEND_HOST or host.endswith(".googleapis.com")
    )


def _period_start(program_time: str) -> float | None:
    """Return the wall-clock start of a period, or None if it is unreadable."""
    try:
        start = dt.datetime.fromisoformat(program_time)
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.UTC)
    return start.timestamp()


def _width(representation: ET.Element) -> int:
    """Return the width of a representation, or 0 if it has none."""
    try:
        return int(representation.get("width") or 0)
    except ValueError:
        return 0


def select_event_clip_segments(
    manifest: str,
    event_start: float,
    event_end: float,
    base_url: str = CAMERA_FRONTEND_URL,
) -> list[str]:
    """Pick the video segments of the recording period that holds an event.

    The manifest lists every recording period in the requested window, each
    with its wall-clock start (programDateTime) and duration. The event's clip
    is the period that overlaps the event the most, and of that period the
    widest video representation. Its segments are fragmented MP4 that play
    when joined in order. Relative URLs are resolved against base_url, and
    segments that are not on the camera frontend are not returned, since the
    Google access token is sent along with them.
    """
    try:
        # The manifest comes from Google over an authenticated HTTPS request,
        # and the expat that ships with Python guards against entity expansion.
        root = ET.fromstring(manifest)  # noqa: S314
    except ET.ParseError:
        _LOGGER.warning("Camera event manifest is not valid XML")
        return []
    # Match elements by local name, with or without the DASH namespace.
    for element in root.iter():
        element.tag = element.tag.rpartition("}")[2]

    best: list[str] = []
    best_overlap = -1.0
    for period in root.iter("Period"):
        start = _period_start(period.get("programDateTime") or "")
        if start is None:
            continue
        end = start + parse_dash_duration(period.get("duration"))
        if event_end > event_start:
            overlap = min(end, event_end) - max(start, event_start)
            if overlap <= 0:
                # At most touching the event: a neighbouring recording.
                continue
        elif start <= event_start <= end:
            # Zero-length event: take the period it falls in.
            overlap = 0.0
        else:
            continue
        if overlap <= best_overlap:
            continue

        segments: list[str] = []
        widest = -1
        for adaptation in period.iter("AdaptationSet"):
            if adaptation.get("contentType") != "video":
                continue
            for representation in adaptation.iter("Representation"):
                width = _width(representation)
                base = representation.findtext("BaseURL", "").strip()
                if not base or width <= widest:
                    continue
                base = urljoin(base_url, base)
                urls = [
                    urljoin(base, segment.get("media", "").strip())
                    for segment in representation.iter("SegmentURL")
                    if segment.get("media", "").strip()
                ]
                if not urls:
                    continue
                if not all(is_camera_frontend_url(url) for url in urls):
                    _LOGGER.warning(
                        "Camera event manifest points outside the camera frontend"
                    )
                    continue
                widest = width
                segments = urls
        if segments:
            best = segments
            best_overlap = overlap
    return best


def _iter_mp4_boxes(data: bytes | memoryview) -> Iterator[tuple[bytes, memoryview]]:
    """Yield (type, box) for the top-level boxes of an MP4 file.

    The boxes are views into data, not copies. Anything that is not a box this
    can follow, including trailing bytes too short for a box header, is
    handed back unchanged with an empty type.
    """
    view = memoryview(data)
    offset = 0
    while offset + 8 <= len(view):
        size = int.from_bytes(view[offset : offset + 4], "big")
        box_type = bytes(view[offset + 4 : offset + 8])
        header = 8
        if size == 1:
            # 64-bit largesize follows the type.
            header = 16
            if offset + 16 <= len(view):
                size = int.from_bytes(view[offset + 8 : offset + 16], "big")
        elif size == 0:
            size = len(view) - offset
        if size < header or offset + size > len(view):
            break
        yield box_type, view[offset : offset + size]
        offset += size
    if offset < len(view):
        yield b"", view[offset:]


def append_mp4_segment(
    joined: bytearray, segment: bytes | memoryview, *, first: bool
) -> None:
    """Append one segment of a recording to the clip joined so far.

    Each segment the camera frontend serves is a complete fragmented MP4 file:
    its own ftyp and moov, then moof/mdat fragments whose decode times run on
    from the previous segment, then an mfra index. Played back to back that
    works in some players only, so the joined clip keeps one ftyp and moov
    and only the fragments of the later segments.
    """
    for box_type, box in _iter_mp4_boxes(segment):
        if box_type in _MP4_SEGMENT_INDEX_BOXES:
            continue
        if not first and box_type in _MP4_SEGMENT_HEADER_BOXES:
            continue
        joined.extend(box)


def join_fragmented_mp4(segments: list[bytes]) -> bytearray:
    """Join the segments of one recording into a single fragmented MP4."""
    joined = bytearray()
    for index, segment in enumerate(segments):
        append_mp4_segment(joined, segment, first=index == 0)
    return joined


class BufferedMediaResponse:
    """Media held in memory, served like an aiohttp response."""

    def __init__(self, data: bytearray) -> None:
        """Initialize with the complete media, which is not copied."""
        self._data = data
        self.content = _StreamReaderLike(data)

    async def read(self) -> bytes:
        """Return the complete media."""
        return bytes(self._data)


class _StreamReaderLike:
    """The part of aiohttp's StreamReader that the media views use."""

    def __init__(self, data: bytearray) -> None:
        """Initialize with the complete media."""
        self._data = data

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        """Yield the media in chunks."""
        view = memoryview(self._data)
        for offset in range(0, len(view), size):
            yield bytes(view[offset : offset + size])
