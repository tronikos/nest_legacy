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

from .const import CONF_COOKIES, CONF_ISSUE_TOKEN
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
    # Device, structure and resource identifiers
    "aux_primary_fabric_id",
    "deviceId",
    "deviceSerialNumber",
    "fabricId",
    "heatLinkSerialNumber",
    "heat_link_serial_number",
    "hgsStructureId",
    "ifj_primary_fabric_id",
    "primaryFabricId",
    "resourceId",
    "rtsDeviceId",
    "rtsSerialNumber",
    "rtsStructureId",
    "serialNumber",
    "serial_number",
    "structureId",
    "structureIds",
    "structure_id",
    "weaveDeviceId",
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
    "customWheres",
    "description",
    "fixtureNameLabel",
    "label",
    "name",
    "roomName",
    "spokenWhereId",
    "title",
    "whereId",
    "whereLabel",
    "where_id",
    # Opaque settings blobs
    "parameters",
    "service_config",
]

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


def _collect_identifiers(coordinator: NestCoordinator) -> list[str]:
    """Return serials, structure IDs and user IDs used as keys and references."""
    candidates: set[str | None] = set()
    for key, value in coordinator.data.items():
        candidates.add(key)
        if isinstance(value, NestDevice):
            candidates.add(value.serial_number)
            candidates.add(_identifier_from_key(value.object_key))
    for key in coordinator.get_raw_data_for_diagnostics():
        candidates.add(_identifier_from_key(key))
    candidates.add(coordinator.config_entry.unique_id)
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

    def walk(value: Any, key: Any = None) -> Any:
        if isinstance(value, dict):
            return {
                alias(k) if isinstance(k, str) else k: walk(v, k)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(item, key) for item in value]
        if isinstance(value, str):
            if value != REDACTED and _is_sensitive_value(key, value):
                return REDACTED
            return alias(value)
        return value

    return walk(data)


def _redact(data: dict[str, Any], coordinator: NestCoordinator) -> dict[str, Any]:
    """Redact sensitive keys, then identifiers, addresses and links."""
    return _redact_identifiers(
        async_redact_data(data, TO_REDACT), _collect_identifiers(coordinator)
    )


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

    processed_data = {
        key: dataclasses.asdict(value)
        for key, value in coordinator.data.items()
        if value
    }

    raw_api_data = _convert_protobuf_to_dict(coordinator.get_raw_data_for_diagnostics())

    data: dict[str, Any] = {
        "config_entry": entry.as_dict(),
        "processed_data": processed_data,
        "raw_api_data": raw_api_data,
    }

    return _redact(data, coordinator)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: NestConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device entry."""
    coordinator = entry.runtime_data
    identifier = next(iter(device.identifiers))
    serial_number = identifier[1]

    device_data = coordinator.data.get(serial_number)
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
