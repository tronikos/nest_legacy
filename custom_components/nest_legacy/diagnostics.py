"""Provides diagnostics for Nest."""

import dataclasses
import ipaddress
import re
from typing import Any

from aiohttp import ClientError
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_COOKIES, CONF_ISSUE_TOKEN, DOMAIN
from .coordinator import NestConfigEntry, NestCoordinator
from .pynest.exceptions import PynestException
from .pynest.models import NestDevice, NestHeatLink

# REST buckets use snake_case keys. Protobuf traits are converted with their
# .proto field names, which are camelCase, so both spellings are listed.
TO_REDACT = [
    CONF_ACCESS_TOKEN,
    CONF_COOKIES,
    CONF_ISSUE_TOKEN,
    # Credentials and tokens
    "access_token",
    "cookie",
    "invitationToken",
    "issuetoken",
    "pairingToken",
    "pairing_token",
    "pincode",
    "pinHash",
    "token",
    "topaz_hush_key",
    "userNfcToken",
    "userNfcTokens",
    "userPincode",
    "userPincodes",
    # People and accounts
    "email",
    "emergency_contact_description",
    "emergency_contact_phone",
    "guestEmail",
    "initiatingUserId",
    "gaiaId",
    "originatorRtsId",
    "phoneNumber",
    "phone_numbers",
    "previousUserId",
    "profile_image_url",
    "rtsUserId",
    "targetUserId",
    "touchedUserId",
    "unique_id",
    "user",
    "userId",
    "userName",
    "userResourceId",
    "user_id",
    "userid",
    # Home address, location and geofencing
    "address",
    "addressLines",
    "address_lines",
    "city",
    "country",
    "fenceId",
    "fence_id",
    "geoCoordinate",
    "geofence_id",
    "geofences",
    "latitude",
    "location",
    "longitude",
    "postalCode",
    "postal_code",
    "rtsFenceId",
    "street_address",
    "structurePostalCode",
    "sunrise",
    "sunset",
    "temp_c",
    "time_zone",
    "timezoneName",
    "zip",
    # Fabric IDs are never used as references, so they are blanked rather
    # than aliased like the identifiers in IDENTIFIER_KEYS.
    "aux_primary_fabric_id",
    "fabricId",
    "ifj_primary_fabric_id",
    "primaryFabricId",
    # Network
    "bssid",
    "bssidHash",
    "extAddr",
    "extAddress",
    "hashedIpv6Address",
    "ipAddress",
    "ipAddresses",
    "ip_address",
    "last_ip",
    "local_ip",
    "local_ipv6",
    "macAddress",
    "mac_address",
    "networkName",
    "ssid",
    "thread_ip_address",
    "thread_mac_address",
    "wifi_ip_address",
    "wifi_mac_address",
    # Camera streams, snapshots and links
    "clipUrl",
    "dashUrl",
    "directHost",
    "downloadUrl",
    "hlsUrl",
    "imageUrl",
    "liveImageUrl",
    "liveUrl",
    "nexus_api_http_server_url",
    "snapshotUrl",
    "web_url",
    # Names and room ("where") labels chosen by the user
    "description",
    "fixtureNameLabel",
    "label",
    "name",
    "roomName",
    "title",
    "whereLabel",
    # Opaque settings blobs
    "parameters",
    "service_config",
]

# Keys whose values identify a device, structure or room and are referenced
# from elsewhere: ``device.<serial>`` keys, ``associated_thermostat_object_key``,
# a structure's ``rtsStructureId``, a device's ``whereAnnotationRid``. Their
# values are replaced with the same placeholder everywhere in a download
# instead of being blanked, so those cross-references can still be followed.
# Values nested under these keys (a ResourceId's ``resourceId``, a StringRef's
# ``literal``) are treated the same way.
IDENTIFIER_KEYS = frozenset(
    {
        "deviceId",
        "deviceSerialNumber",
        "heatLinkSerialNumber",
        "heat_link_serial_number",
        "hgsDeviceId",
        "hgsStructureId",
        "legacyUuid",
        "resourceId",
        "rtsDeviceId",
        "rtsSerialNumber",
        "rtsStructureId",
        "serialNumber",
        "serial_number",
        "spokenWhereId",
        "structureId",
        "structureIds",
        "structure_id",
        "weaveDeviceId",
        "whereId",
        "whereLegacyUuid",
        "where_id",
    }
)

# Google's predefined rooms and fixtures use small fixed annotation IDs that
# are the same in every home, so they are left readable. Larger values are
# treated as identifiers.
_ANNOTATION_RE = re.compile(r"^ANNOTATION_([0-9A-F]{16})$", re.IGNORECASE)
_MAX_PREDEFINED_ANNOTATION = 0xFFFFFFFF

# Values that are redacted whatever key they are stored under, so that
# addresses and links in fields not listed above never leave the system.
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

# Identifiers shorter than this are not replaced inside other strings.
_MIN_IDENTIFIER_LENGTH = 6


def _convert_protobuf_to_dict(data: Any) -> Any:
    """Convert protobuf messages to dicts recursively."""
    if isinstance(data, Message):
        return MessageToDict(data, preserving_proto_field_name=True)
    if isinstance(data, dict):
        return {k: _convert_protobuf_to_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_convert_protobuf_to_dict(item) for item in data]
    return data


def _identifier_from_key(key: str) -> str | None:
    """Return the identifier in a key like ``topaz.<serial>`` or ``DEVICE_<id>``."""
    prefix, separator, suffix = key.partition(".")
    if separator:
        return suffix
    prefix, separator, suffix = key.partition("_")
    if separator and prefix.isupper():
        return suffix
    return None


def _is_predefined_annotation(value: str) -> bool:
    """Return True for a predefined room or fixture ID like ``ANNOTATION_<n>``."""
    match = _ANNOTATION_RE.match(value)
    return match is not None and int(match[1], 16) <= _MAX_PREDEFINED_ANNOTATION


def _identifier_values(data: Any, in_identifier: bool = False) -> set[str]:
    """Return the identifiers stored under IDENTIFIER_KEYS anywhere in data.

    A value with a prefix (``structure.<id>``, ``DEVICE_<id>``) contributes the
    bare ID, so it gets the same placeholder as that ID elsewhere.
    """
    if isinstance(data, dict):
        return set().union(
            *(
                _identifier_values(value, in_identifier or key in IDENTIFIER_KEYS)
                for key, value in data.items()
            )
        )
    if isinstance(data, list):
        return set().union(*(_identifier_values(item, in_identifier) for item in data))
    if (
        in_identifier
        and isinstance(data, str)
        and data
        and data != REDACTED
        and not _is_predefined_annotation(data)
    ):
        return {_identifier_from_key(data) or data}
    return set()


def _collect_identifiers(coordinator: NestCoordinator, data: Any) -> list[str]:
    """Return the serials and structure, room and user IDs in a download."""
    candidates: set[str | None] = set()
    for key, value in coordinator.data.items():
        candidates.add(key)
        if isinstance(value, NestDevice):
            candidates.add(value.serial_number)
            candidates.add(_identifier_from_key(value.object_key))
    for key in coordinator.get_raw_data_for_diagnostics():
        candidates.add(_identifier_from_key(key))
    candidates.add(coordinator.config_entry.unique_id)
    candidates |= _identifier_values(data)
    return sorted(
        candidate
        for candidate in candidates
        if candidate and len(candidate) >= _MIN_IDENTIFIER_LENGTH
    )


def _is_sensitive_value(key: Any, value: str) -> bool:
    """Return True for IP addresses and URLs."""
    if _URL_RE.match(value):
        return True
    if isinstance(key, str) and "version" in key.lower():
        # Firmware versions such as 1.2.3.4 also parse as IPv4 addresses.
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _redact_identifiers(data: Any, identifiers: list[str]) -> Any:
    """Replace identifiers in keys and values with stable placeholders.

    Serials and structure IDs are also dictionary keys and parts of other
    strings (``topaz.<serial>``), which async_redact_data does not touch.
    Each identifier gets its own placeholder so references stay readable.
    A value under IDENTIFIER_KEYS that cannot be replaced is blanked.
    """
    if not identifiers:
        pattern = None
    else:
        aliases = {
            identifier: f"**REDACTED_{index}**"
            for index, identifier in enumerate(identifiers, start=1)
        }
        pattern = re.compile(
            "|".join(re.escape(i) for i in sorted(identifiers, key=len, reverse=True))
        )

    def alias(text: str) -> str:
        if pattern is None:
            return text
        return pattern.sub(lambda match: aliases[match.group(0)], text)

    def alias_identifier(value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if not isinstance(value, str):
            return REDACTED
        if not value or value == REDACTED or _is_predefined_annotation(value):
            return value
        aliased = alias(value)
        return aliased if aliased != value else REDACTED

    def walk(value: Any, key: Any = None, in_identifier: bool = False) -> Any:
        if isinstance(value, dict):
            return {
                alias(k) if isinstance(k, str) else k: walk(
                    v, k, in_identifier or k in IDENTIFIER_KEYS
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(item, key, in_identifier) for item in value]
        if in_identifier:
            return alias_identifier(value)
        if isinstance(value, str):
            if value != REDACTED and _is_sensitive_value(key, value):
                return REDACTED
            return alias(value)
        return value

    return walk(data)


def _processed_data(coordinator: NestCoordinator) -> dict[str, Any]:
    """Return the coordinator's parsed devices and structures as dicts."""
    return {
        key: dataclasses.asdict(value)
        for key, value in coordinator.data.items()
        if value
    }


def _raw_api_data(coordinator: NestCoordinator) -> Any:
    """Return the raw REST buckets and protobuf resources as dicts."""
    return _convert_protobuf_to_dict(coordinator.get_raw_data_for_diagnostics())


def _redact(data: dict[str, Any], coordinator: NestCoordinator) -> dict[str, Any]:
    """Redact sensitive keys, then identifiers, addresses and links.

    Identifiers are collected from all of the coordinator's data, not only the
    part being downloaded, so a device download uses the same placeholders as
    a config entry download taken at the same time.
    """
    redacted = async_redact_data(data, TO_REDACT)
    everything = async_redact_data(
        {
            "processed_data": _processed_data(coordinator),
            "raw_api_data": _raw_api_data(coordinator),
        },
        TO_REDACT,
    )
    identifiers = _collect_identifiers(coordinator, [redacted, everything])
    return _redact_identifiers(redacted, identifiers)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NestConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    # Ensure we have the latest data for diagnostics
    try:
        if coordinator.client.is_expired():
            await coordinator.async_reauthenticate()
    except (ClientError, TimeoutError, PynestException, HomeAssistantError) as e:
        return {"error": f"Authentication failed during diagnostics: {e}"}

    data: dict[str, Any] = {
        "config_entry": entry.as_dict(),
        "processed_data": _processed_data(coordinator),
        "raw_api_data": _raw_api_data(coordinator),
    }

    return _redact(data, coordinator)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: NestConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device entry."""
    coordinator = entry.runtime_data
    serial_number = next(
        (id_val for domain, id_val in device.identifiers if domain == DOMAIN),
        None,
    )

    device_data = coordinator.data.get(serial_number) if serial_number else None
    if not isinstance(device_data, NestDevice):
        return {"error": "Device not found in coordinator data"}

    raw_data = coordinator.get_raw_data_for_diagnostics()
    device_raw_data = raw_data.get(device_data.object_key)

    # For Heat Links, fall back to associated thermostat data if available
    if (
        not device_raw_data
        and isinstance(device_data, NestHeatLink)
        and device_data.associated_thermostat_object_key
    ):
        device_raw_data = raw_data.get(device_data.associated_thermostat_object_key)

    device_raw_data = _convert_protobuf_to_dict(device_raw_data)

    data: dict[str, Any] = {
        "device_entry": {
            "name": device.name,
            "model": device.model,
            "sw_version": device.sw_version,
            "hw_version": device.hw_version,
            "manufacturer": device.manufacturer,
        },
        "processed_data": dataclasses.asdict(device_data),
        "raw_data": device_raw_data,
    }

    return _redact(data, coordinator)
