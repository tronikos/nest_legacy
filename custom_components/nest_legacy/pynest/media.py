"""Stored event media of Protobuf-only cameras.

These cameras are not in the legacy REST API, so nexusapi has nothing for them.
The Home app plays their history from a DASH manifest on the camera frontend,
which accepts the Google access token the integration already gets when it
authenticates. This module reads that manifest, joins the segments of one
recording into a single fragmented MP4, and adds the recording's audio track
to it when there is one.
"""

from collections.abc import AsyncIterator, Callable, Iterator
import datetime as dt
import logging
import re
from typing import NamedTuple
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
    return _int_attribute(representation, "width")


def _bandwidth(representation: ET.Element) -> int:
    """Return the bandwidth of a representation, or 0 if it has none."""
    return _int_attribute(representation, "bandwidth")


def _int_attribute(element: ET.Element, name: str) -> int:
    """Return an integer attribute, or 0 if it is missing or not a number."""
    try:
        return int(element.get(name) or 0)
    except ValueError:
        return 0


class EventClipStreams(NamedTuple):
    """The segment URLs of one recording, per stream."""

    video: list[str]
    audio: list[str]


def select_event_clip_segments(
    manifest: str,
    event_start: float,
    event_end: float,
    base_url: str = CAMERA_FRONTEND_URL,
) -> list[str]:
    """Pick the video segments of the recording period that holds an event."""
    return select_event_clip_streams(manifest, event_start, event_end, base_url).video


def select_event_clip_streams(
    manifest: str,
    event_start: float,
    event_end: float,
    base_url: str = CAMERA_FRONTEND_URL,
) -> EventClipStreams:
    """Pick the segments of the recording period that holds an event.

    The manifest lists every recording period in the requested window, each
    with its wall-clock start (programDateTime) and duration. The event's clip
    is the period that overlaps the event the most: of that period the widest
    video representation and, if the period has one, its audio. The segments
    of each stream are fragmented MP4 that play when joined in order. Relative
    URLs are resolved against base_url, and segments that are not on the
    camera frontend are not returned, since the Google access token is sent
    along with them.
    """
    try:
        # The manifest comes from Google over an authenticated HTTPS request,
        # and the expat that ships with Python guards against entity expansion.
        root = ET.fromstring(manifest)  # noqa: S314
    except ET.ParseError:
        _LOGGER.warning("Camera event manifest is not valid XML")
        return EventClipStreams([], [])
    # Match elements by local name, with or without the DASH namespace.
    for element in root.iter():
        element.tag = element.tag.rpartition("}")[2]

    best = EventClipStreams([], [])
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

        video = _best_representation_urls(period, "video", _width, base_url)
        if video:
            audio = _best_representation_urls(period, "audio", _bandwidth, base_url)
            best = EventClipStreams(video, audio)
            best_overlap = overlap
    return best


def _best_representation_urls(
    period: ET.Element,
    content_type: str,
    rank: Callable[[ET.Element], int],
    base_url: str,
) -> list[str]:
    """Return the segment URLs of the highest ranked usable representation."""
    best: list[str] = []
    best_rank = -1
    for adaptation in period.iter("AdaptationSet"):
        if adaptation.get("contentType") != content_type:
            continue
        for representation in adaptation.iter("Representation"):
            value = rank(representation)
            base = representation.findtext("BaseURL", "").strip()
            if not base or value <= best_rank:
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
            best_rank = value
            best = urls
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


class _Box(NamedTuple):
    """Where a box sits in a buffer."""

    type: bytes
    start: int
    end: int
    header: int

    @property
    def payload(self) -> int:
        """Return the offset of the box's payload."""
        return self.start + self.header


def _boxes(
    data: bytes | bytearray, start: int = 0, end: int | None = None
) -> list[_Box]:
    """Return the boxes in data[start:end]; raise ValueError on anything else."""
    end = len(data) if end is None else end
    boxes = []
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise ValueError("trailing bytes")
        size = int.from_bytes(data[offset : offset + 4], "big")
        header = 8
        if size == 1:
            if offset + 16 > end:
                raise ValueError("truncated largesize")
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise ValueError("bad box size")
        box_type = bytes(data[offset + 4 : offset + 8])
        boxes.append(_Box(box_type, offset, offset + size, header))
        offset += size
    return boxes


def _child(data: bytes | bytearray, box: _Box, *path: bytes) -> _Box:
    """Return the only box at path below box; raise ValueError if not one."""
    for box_type in path:
        found = [b for b in _boxes(data, box.payload, box.end) if b.type == box_type]
        if len(found) != 1:
            raise ValueError(f"expected one {box_type!r}")
        box = found[0]
    return box


def _u32(data: bytes | bytearray, offset: int) -> int:
    """Read a big-endian 32-bit integer."""
    return int.from_bytes(data[offset : offset + 4], "big")


def _set_u32(data: bytearray, offset: int, value: int) -> None:
    """Write a big-endian 32-bit integer."""
    data[offset : offset + 4] = value.to_bytes(4, "big")


def _full_box_field(data: bytes | bytearray, box: _Box, v0: int, v1: int) -> int:
    """Return the offset of a field in a full box, by the box's version."""
    return box.payload + (v1 if data[box.payload] == 1 else v0)


class _Fragment(NamedTuple):
    """One movie fragment: any boxes before its moof, the moof and its mdat."""

    data: bytearray
    time: float
    track_id_offset: int
    sequence_offset: int


class _FragmentedTrack(NamedTuple):
    """A single-track fragmented MP4, split into the parts muxing needs."""

    ftyp: bytes
    moov: bytearray
    trak: _Box
    trex: _Box
    track_id: int
    fragments: list[_Fragment]


def _parse_fragmented_track(data: bytes | bytearray) -> _FragmentedTrack:
    """Split a joined single-track fragmented MP4; raise ValueError otherwise."""
    ftyp = b""
    moov: bytearray | None = None
    trak = trex = None
    track_id = timescale = 0
    fragments: list[_Fragment] = []
    pending = bytearray()
    for box in _boxes(data):
        raw = data[box.start : box.end]
        if box.type == b"ftyp" and moov is None:
            ftyp = bytes(raw)
        elif box.type == b"moov" and moov is None:
            moov = bytearray(raw)
            root = _Box(b"moov", 0, len(moov), box.header)
            trak = _child(moov, root, b"trak")
            trex = _child(moov, root, b"mvex", b"trex")
            tkhd = _child(moov, trak, b"tkhd")
            track_id = _u32(moov, _full_box_field(moov, tkhd, 12, 20))
            mdhd = _child(moov, trak, b"mdia", b"mdhd")
            timescale = _u32(moov, _full_box_field(moov, mdhd, 12, 20))
            if not timescale or _u32(moov, trex.payload + 4) != track_id:
                raise ValueError("inconsistent track")
        elif box.type == b"moof" and moov is not None:
            fragment = pending + raw
            moof = _Box(b"moof", len(pending), len(fragment), box.header)
            mfhd = _child(fragment, moof, b"mfhd")
            tfhd = _child(fragment, moof, b"traf", b"tfhd")
            tfdt = _child(fragment, moof, b"traf", b"tfdt")
            if fragment[tfhd.payload + 3] & 0x01:
                # base-data-offset-present: sample data is addressed from the
                # start of the file, so the fragment cannot be moved.
                raise ValueError("absolute data offsets")
            if _u32(fragment, tfhd.payload + 4) != track_id:
                raise ValueError("fragment of another track")
            size = 8 if fragment[tfdt.payload] == 1 else 4
            decode_time = int.from_bytes(
                fragment[tfdt.payload + 4 : tfdt.payload + 4 + size], "big"
            )
            fragments.append(
                _Fragment(
                    fragment,
                    decode_time / timescale,
                    tfhd.payload + 4,
                    mfhd.payload + 4,
                )
            )
            pending = bytearray()
        elif box.type == b"mdat" and fragments and not pending:
            fragments[-1].data.extend(raw)
        elif moov is not None and box.type not in (b"ftyp", b"moov", b"mdat"):
            # Event messages and the like travel with the next fragment.
            pending.extend(raw)
        else:
            raise ValueError(f"unexpected {box.type!r}")
    if moov is None or trak is None or trex is None or not fragments:
        raise ValueError("no fragments")
    return _FragmentedTrack(ftyp, moov, trak, trex, track_id, fragments)


def _box_bytes(box_type: bytes, payload: bytes | bytearray) -> bytes:
    """Return one box with a 32-bit size."""
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def mux_fragmented_mp4(
    video: bytes | bytearray, audio: bytes | bytearray
) -> bytearray | None:
    """Combine a joined video clip and a joined audio clip into one fMP4.

    The camera frontend serves a recording's video and audio as separate
    streams, each a single-track fragmented MP4, and both number their track
    1. The result keeps the video's ftyp and moov, adds the audio track to the
    moov under the next free track ID, and interleaves the fragments of both
    by decode time. If either input is not in that shape, this returns None so
    the caller can serve the video alone.
    """
    try:
        v = _parse_fragmented_track(video)
        a = _parse_fragmented_track(audio)
    except (ValueError, IndexError) as err:
        _LOGGER.debug("Not adding the audio track to the event clip: %s", err)
        return None

    audio_id = v.track_id + 1
    a_trak = bytearray(a.moov[a.trak.start : a.trak.end])
    tkhd = _child(a_trak, _Box(b"trak", 0, len(a_trak), a.trak.header), b"tkhd")
    _set_u32(a_trak, _full_box_field(a_trak, tkhd, 12, 20), audio_id)
    a_trex = bytearray(a.moov[a.trex.start : a.trex.end])
    _set_u32(a_trex, a.trex.header + 4, audio_id)

    root = _boxes(v.moov)[0]
    payload = bytearray()
    for box in _boxes(v.moov, root.payload, root.end):
        raw = v.moov[box.start : box.end]
        if box.type == b"mvhd":
            mvhd = bytearray(raw)
            _set_u32(mvhd, len(mvhd) - 4, audio_id + 1)  # next_track_ID
            payload += mvhd
        elif box.type == b"trak":
            payload += raw + a_trak
        elif box.type == b"mvex":
            payload += _box_bytes(b"mvex", raw[box.header :] + a_trex)
        else:
            payload += raw

    for fragment in a.fragments:
        _set_u32(fragment.data, fragment.track_id_offset, audio_id)
    fragments = sorted(
        [(f, 0) for f in v.fragments] + [(f, 1) for f in a.fragments],
        key=lambda item: (item[0].time, item[1]),
    )
    muxed = bytearray(v.ftyp)
    muxed += _box_bytes(b"moov", payload)
    for sequence, (fragment, _) in enumerate(fragments, start=1):
        _set_u32(fragment.data, fragment.sequence_offset, sequence)
        muxed += fragment.data
    return muxed


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
