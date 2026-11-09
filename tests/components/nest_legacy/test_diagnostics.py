"""Tests for the Nest Legacy diagnostics."""

from typing import Any

from custom_components.nest_legacy.const import DOMAIN
from custom_components.nest_legacy.diagnostics import (
    TO_REDACT,
    _identifier_values,
    _redact_identifiers,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import THERMOSTAT_SERIAL

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
    get_diagnostics_for_device,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator


@pytest.fixture
def platforms() -> list[Platform]:
    """Diagnostics do not need any entities."""
    return [Platform.CLIMATE]


@pytest.mark.freeze_time("2026-01-01 00:00:00+00:00")
async def test_config_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """The config entry diagnostics are stable and redacted."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics == snapshot


async def test_credentials_are_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """The stored cookies and issue token never appear in a download."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert "OCAK=test" not in str(diagnostics)
    assert "iframerpc" not in str(diagnostics)


# Values from the app_launch fixture that identify the account, a device, a
# room or a camera stream, and so must not appear anywhere in a download.
IDENTIFYING_VALUES = [
    "09AA00AA00AA0AAA",
    "09AA00AA00AA0AA1",
    "18B430CCCCCC0001",
    "18B430CCCCCC0002",
    "00000000-0000-0000-0000-000000000001",
    "1234567890",
    "18b430000001",
    "nexusapi-test.dropcam.com",
    "home.nest.com/cameras",
    "Hallway Protect",
    "Bedroom Sensor",
    "Front Door",
    "Living Room",
    "Test Home",
    "where-hallway",
    "where-front-door",
]


async def test_identifying_values_are_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Serials, IDs, stream URLs and room names never appear in a download."""
    downloads = [
        await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    ]
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert devices
    downloads.extend(
        [
            await get_diagnostics_for_device(
                hass, hass_client, init_integration, device
            )
            for device in devices
        ]
    )

    for diagnostics in downloads:
        text = str(diagnostics)
        for value in IDENTIFYING_VALUES:
            assert value not in text


def test_redact_identifiers() -> None:
    """Identifiers in keys and references, addresses and links are replaced."""
    data = {
        "topaz.09AA01AC000000": {
            "last_seen_ip": "192.0.2.10",
            "ipv6": "2001:db8::1",
            "stream": "rtsps://stream.example.com/live",
            "software_version": "1.2.3.4",
            "swarm": ["device.09AA01AC000000"],
            "mode": "home",
        },
    }

    assert _redact_identifiers(data, ["09AA01AC000000"]) == {
        "topaz.**REDACTED_1**": {
            "last_seen_ip": "**REDACTED**",
            "ipv6": "**REDACTED**",
            "stream": "**REDACTED**",
            "software_version": "1.2.3.4",
            "swarm": ["device.**REDACTED_1**"],
            "mode": "home",
        },
    }


def test_identifier_keys_are_aliased_consistently() -> None:
    """Identifier values get the same placeholder as the keys that use them."""
    data = {
        "device.09AA01AC000000": {
            "serial_number": "09AA01AC000000",
            "where_id": "00000000-0000-0000-0000-00000000000a",
        },
        "heatlink.09AA01AC000001": {
            "serial_number": "09AA01AC000001",
            "associated_thermostat_object_key": "device.09AA01AC000000",
        },
        "STRUCTURE_00000000000000AB": {
            "nest.trait.structure.StructureInfoTrait": {
                "rtsStructureId": "structure.00000000-0000-0000-0000-000000000001",
            },
            "nest.trait.located.LocatedAnnotationsTrait": {
                "predefinedWheres": {
                    "0": {
                        "whereId": {"resourceId": "ANNOTATION_0000000000000007"},
                        "legacyUuid": "00000000-0000-0000-0000-00000000000a",
                    },
                },
                "customWheres": {
                    "0": {"whereId": {"resourceId": "ANNOTATION_7F3A9C0D1E2B4A65"}},
                },
            },
        },
        "DEVICE_00000000000000CD": {
            "nest.trait.located.DeviceLocatedSettingsTrait": {
                "whereAnnotationRid": {"resourceId": "ANNOTATION_7F3A9C0D1E2B4A65"},
            },
            "nest.trait.hvac.ExampleTrait": {
                "deviceId": {"resourceId": "DEVICE_00000000000000CD"},
                "structureIds": [{"resourceId": "STRUCTURE_00000000000000AB"}],
                "weaveDeviceId": 12345,
                "rtsDeviceId": "abc",
            },
        },
        "structure.00000000-0000-0000-0000-000000000001": {"away": False},
    }
    collected = _identifier_values(data)
    # Predefined rooms are the same in every home and are not collected.
    assert "0000000000000007" not in collected
    # Like _collect_identifiers, leave out IDs too short to alias safely.
    identifiers = sorted(
        identifier
        for identifier in collected
        | {
            "09AA01AC000000",
            "09AA01AC000001",
            "00000000000000AB",
            "00000000000000CD",
            "00000000-0000-0000-0000-000000000001",
        }
        if len(identifier) >= 6
    )

    redacted = _redact_identifiers(data, identifiers)

    def alias_of(value: str) -> str:
        return f"**REDACTED_{identifiers.index(value) + 1}**"

    thermostat = alias_of("09AA01AC000000")
    structure = alias_of("00000000-0000-0000-0000-000000000001")
    room = alias_of("00000000-0000-0000-0000-00000000000a")
    custom_room = alias_of("7F3A9C0D1E2B4A65")
    structure_rid = alias_of("00000000000000AB")
    device_rid = alias_of("00000000000000CD")

    device = redacted[f"device.{thermostat}"]
    assert device["serial_number"] == thermostat
    assert device["where_id"] == room
    heat_link = redacted[f"heatlink.{alias_of('09AA01AC000001')}"]
    assert heat_link["associated_thermostat_object_key"] == f"device.{thermostat}"

    structure_traits = redacted[f"STRUCTURE_{structure_rid}"]
    info = structure_traits["nest.trait.structure.StructureInfoTrait"]
    assert info["rtsStructureId"] == f"structure.{structure}"
    assert f"structure.{structure}" in redacted
    annotations = structure_traits["nest.trait.located.LocatedAnnotationsTrait"]
    predefined = annotations["predefinedWheres"]["0"]
    assert predefined["whereId"] == {"resourceId": "ANNOTATION_0000000000000007"}
    assert predefined["legacyUuid"] == room
    custom = annotations["customWheres"]["0"]
    assert custom["whereId"] == {"resourceId": f"ANNOTATION_{custom_room}"}

    device_traits = redacted[f"DEVICE_{device_rid}"]
    located = device_traits["nest.trait.located.DeviceLocatedSettingsTrait"]
    assert located["whereAnnotationRid"] == {"resourceId": f"ANNOTATION_{custom_room}"}
    example = device_traits["nest.trait.hvac.ExampleTrait"]
    assert example["deviceId"] == {"resourceId": f"DEVICE_{device_rid}"}
    assert example["structureIds"] == [{"resourceId": f"STRUCTURE_{structure_rid}"}]
    # A value that cannot be aliased (too short, or not a string) is blanked
    # rather than left in place.
    assert example["rtsDeviceId"] == "**REDACTED**"
    assert example["weaveDeviceId"] == "**REDACTED**"


def test_blame_and_actor_ids_are_redacted() -> None:
    """Account IDs in blame and actor fields never pass through raw."""
    data = {
        "nest.trait.occupancy.StructureModeTrait": {
            "primaryBlame": {
                "userBlame": {"gaiaId": "123456789012345678901"},
                "deviceBlame": {"hgsDeviceId": "0123456789abcdef"},
            },
        },
        "nest.trait.hvac.HvacActor": {"originatorRtsId": "user.0123456789"},
    }
    redacted = async_redact_data(data, TO_REDACT)
    identifiers = sorted(_identifier_values(redacted))

    text = str(_redact_identifiers(redacted, identifiers))

    assert identifiers == ["0123456789abcdef"]
    for value in ("123456789012345678901", "0123456789abcdef", "0123456789"):
        assert value not in text
    assert "**REDACTED_1**" in text


async def test_diagnostics_cross_references(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """A device, its home and its room read the same everywhere in a download."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )
    processed = diagnostics["processed_data"]
    raw = diagnostics["raw_api_data"]

    # A device's serial, its resource key, and the thermostat that lists a
    # temperature sensor among its remote comfort sensors.
    sensor = next(
        value
        for value in processed.values()
        if value.get("associated_thermostat_object_key")
    )
    thermostat_key = sensor["associated_thermostat_object_key"]
    assert thermostat_key.startswith("DEVICE_**REDACTED_")
    thermostat = next(
        value for value in processed.values() if value["object_key"] == thermostat_key
    )
    identity = raw[thermostat_key]["weave.trait.description.DeviceIdentityTrait"]
    assert identity["serialNumber"] == thermostat["serial_number"]
    assert processed[thermostat["serial_number"]] == thermostat
    rcs = raw[thermostat_key]["nest.trait.hvac.RemoteComfortSensingSettingsTrait"]
    assert [item["deviceId"]["resourceId"] for item in rcs["associatedRcsSensors"]] == [
        sensor["object_key"]
    ]

    # The camera's home is the REST structure the protobuf structure links to.
    camera = next(value for key, value in raw.items() if key.startswith("quartz."))
    structure = camera["structure_id"]
    assert structure.startswith("**REDACTED_")
    assert f"structure.{structure}" in raw
    assert f"where.{structure}" in raw
    assert processed[structure]["serial_number"] == structure
    protobuf_structure = next(
        value for key, value in raw.items() if key.startswith("STRUCTURE_")
    )
    info = protobuf_structure["nest.trait.structure.StructureInfoTrait"]
    assert info["rtsStructureId"] == f"structure.{structure}"

    # Room lookups: REST devices match the structure's where list, and
    # protobuf devices match the structure's annotations.
    rest_rooms = {where["where_id"] for where in raw[f"where.{structure}"]["wheres"]}
    assert camera["where_id"] in rest_rooms
    protect = next(value for key, value in raw.items() if key.startswith("topaz."))
    assert protect["where_id"] in rest_rooms
    annotations = protobuf_structure["nest.trait.located.LocatedAnnotationsTrait"]
    protobuf_rooms = {
        where["whereId"]["resourceId"]
        for where in annotations["predefinedWheres"].values()
    }
    device_rooms = {
        value["nest.trait.located.DeviceLocatedSettingsTrait"]["whereAnnotationRid"][
            "resourceId"
        ]
        for key, value in raw.items()
        if key.startswith("DEVICE_")
    }
    assert device_rooms
    assert device_rooms <= protobuf_rooms


async def test_device_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """The per device diagnostics include the device's own raw data."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, THERMOSTAT_SERIAL), init_integration.entry_id
    )
    assert device is not None

    diagnostics: dict[str, Any] = await get_diagnostics_for_device(
        hass, hass_client, init_integration, device
    )

    assert diagnostics == snapshot


async def test_device_diagnostics_use_same_placeholders(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A device download uses the same placeholders as the config entry download."""
    entry_diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, THERMOSTAT_SERIAL), init_integration.entry_id
    )
    assert device is not None

    device_diagnostics = await get_diagnostics_for_device(
        hass, hass_client, init_integration, device
    )

    processed = device_diagnostics["processed_data"]
    assert entry_diagnostics["processed_data"][processed["serial_number"]] == processed
    assert (
        entry_diagnostics["raw_api_data"][processed["object_key"]]
        == device_diagnostics["raw_data"]
    )


async def test_device_diagnostics_with_merged_identifiers(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A device merged with another integration still resolves its Nest serial."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, THERMOSTAT_SERIAL), init_integration.entry_id
    )
    assert device is not None
    device_registry.async_update_device(
        device.id,
        new_identifiers={
            ("other_domain", "foreign-id-1"),
            ("another_domain", "foreign-id-2"),
            (DOMAIN, THERMOSTAT_SERIAL),
        },
    )
    updated_device = device_registry.async_get(device.id)
    assert updated_device is not None

    device_diagnostics = await get_diagnostics_for_device(
        hass, hass_client, init_integration, updated_device
    )

    assert "error" not in device_diagnostics
    assert "processed_data" in device_diagnostics
