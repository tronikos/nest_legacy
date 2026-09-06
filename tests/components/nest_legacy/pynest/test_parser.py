"""Tests for the pynest parser."""

from typing import Any

from custom_components.nest_legacy.pynest.enums import (
    HotWaterMode,
    LockBoltState,
    StructureMode,
    TemperatureScale,
    ThermostatHvacMode,
    ThermostatHvacStage,
    ThermostatHvacState,
)
from custom_components.nest_legacy.pynest.models import (
    NestHeatLink,
    NestLock,
    NestStructure,
    NestThermostat,
)
from custom_components.nest_legacy.pynest.parser import NestParser
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.weave.trait import (
    description_pb2 as weave_description_pb2,
)
import pytest

from homeassistant.core import HomeAssistant

from .. import async_load_fixture_json
from ..const import (
    CAMERA_SERIAL,
    LOCK_SERIAL,
    PROTECT_SERIAL,
    TEMP_SENSOR_SERIAL,
    THERMOSTAT_KEY,
    THERMOSTAT_SERIAL,
    protobuf_updates,
)

THERMOSTAT = "09AA00AA00AA0AAA"


@pytest.fixture
async def raw_data(hass: HomeAssistant) -> dict[str, Any]:
    """Return the REST and protobuf payloads the coordinator would hold."""
    data = await async_load_fixture_json(hass, "app_launch.json")
    data.update(protobuf_updates())
    return data


@pytest.fixture
def parser() -> NestParser:
    """Return a parser."""
    return NestParser()


def _by_serial(parser: NestParser, raw_data: dict[str, Any]) -> dict[str, Any]:
    """Return the parsed devices keyed by serial number."""
    return {
        device.serial_number: device for device in parser.parse_all(raw_data).devices
    }


def _hvac_state(
    raw_data: dict[str, Any],
) -> nest_hvac_pb2.HvacControlTrait.HvacState:
    """Return the mutable HVAC state of the protobuf thermostat."""
    hvac_trait: nest_hvac_pb2.HvacControlTrait = raw_data[THERMOSTAT_KEY][
        nest_hvac_pb2.HvacControlTrait.DESCRIPTOR.full_name
    ]
    return hvac_trait.hvacState


async def test_every_device_type_is_parsed(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """One payload produces one device per physical device plus the heat link."""
    devices = _by_serial(parser, raw_data)

    assert set(devices) == {
        "00000000-0000-0000-0000-000000000001",
        "09AA00AA00AA0AAA",
        "09AA00AA00AA0AAB",
        "09AA00AA00AA0AA1",
        "18B430CCCCCC0001",
        "18B430CCCCCC0002",
        "18B430DDDDDD0001",
        "18B430DDDDDD0002",
        "18B430DDDDDD0003",
        "18B430DDDDDD0004",
        "18B430DDDDDD0005",
    }


async def test_locations_come_from_both_apis(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Rooms are resolved from the REST wheres and the protobuf annotations."""
    devices = _by_serial(parser, raw_data)

    assert devices["09AA00AA00AA0AAA"].location == "Hallway"
    assert devices["18B430CCCCCC0001"].location == "Bedroom"
    assert devices[LOCK_SERIAL].location == "Front Door"


async def test_thermostat_values(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """The REST thermostat is parsed into the shape the entities expect."""
    thermostat = _by_serial(parser, raw_data)["09AA00AA00AA0AAA"]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.temperature_scale is TemperatureScale.FAHRENHEIT
    assert thermostat.hvac_mode is ThermostatHvacMode.HEAT
    assert thermostat.hvac_state is ThermostatHvacState.HEATING
    # Stages are protobuf only; the REST API reports heating without the stage.
    assert thermostat.hvac_stage is None
    assert thermostat.can_heat
    assert thermostat.can_cool
    assert thermostat.online


async def test_unparseable_device_is_skipped(
    parser: NestParser,
    raw_data: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A device missing its serial number is skipped, not fatal; see issue #9."""
    del raw_data[f"device.{THERMOSTAT}"]["serial_number"]

    devices = _by_serial(parser, raw_data)

    assert THERMOSTAT not in devices
    # The rest of the account still comes through.
    assert "09AA00AA00AA0AA1" in devices
    assert "due to a parsing error" in caplog.text


@pytest.mark.parametrize(
    ("scale", "expected"),
    [
        ("F", TemperatureScale.FAHRENHEIT),
        ("C", TemperatureScale.CELSIUS),
        # Unrecognised values fall back rather than dropping the thermostat.
        ("K", TemperatureScale.CELSIUS),
        (None, TemperatureScale.CELSIUS),
    ],
)
async def test_temperature_scale_fallback(
    parser: NestParser,
    raw_data: dict[str, Any],
    scale: str | None,
    expected: TemperatureScale,
) -> None:
    """An unknown temperature scale defaults to Celsius."""
    raw_data[f"device.{THERMOSTAT}"]["temperature_scale"] = scale

    thermostat = _by_serial(parser, raw_data)["09AA00AA00AA0AAA"]

    assert thermostat.temperature_scale is expected


async def test_unknown_hvac_mode_falls_back_to_off(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """An unrecognised target temperature type is reported as off."""
    raw_data[f"shared.{THERMOSTAT}"]["target_temperature_type"] = "something-new"

    thermostat = _by_serial(parser, raw_data)["09AA00AA00AA0AAA"]

    assert thermostat.hvac_mode is ThermostatHvacMode.OFF


async def test_eco_on_a_heat_only_thermostat(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Eco on a heat only device stays in heat; see issue #38.

    The REST API reports target_temperature_type as "eco" while eco is active,
    which must not be read as a heat/cool range.
    """
    shared = raw_data[f"shared.{THERMOSTAT}"]
    shared["target_temperature_type"] = "eco"
    shared["can_cool"] = False
    shared["eco"] = {"mode": "manual-eco"}
    shared["away_temperature_low"] = 12.0
    shared["away_temperature_high"] = 26.0

    thermostat = _by_serial(parser, raw_data)["09AA00AA00AA0AAA"]

    assert thermostat.is_eco_mode
    assert thermostat.hvac_mode is ThermostatHvacMode.HEAT
    # 12 C is 53.6 F, which the Fahrenheit thermostat snaps to 54 F.
    assert thermostat.target_temperature == pytest.approx(12.2222, abs=1e-4)


async def test_remote_sensor_takes_over_the_temperature(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """An active remote sensor supplies the thermostat's temperature; issue #20."""
    raw_data[f"rcs_settings.{THERMOSTAT}"]["active_rcs_sensors"] = [
        "kryptonite.18B430CCCCCC0001"
    ]

    devices = _by_serial(parser, raw_data)

    assert devices["09AA00AA00AA0AAA"].current_temperature == 19.5
    assert devices["18B430CCCCCC0001"].is_active_sensor


@pytest.mark.parametrize(
    ("heat_link_model", "expected"),
    [
        ("Amber-2.5", "Heat Link for Learning Thermostat (3rd gen, EU)"),
        ("Amber-1.6", "Heat Link for Learning Thermostat (2nd gen, EU)"),
        ("Agate-1.0", "Heat Link for Thermostat E (1st gen, EU)"),
        ("Something-9", "Heat Link (Something-9)"),
    ],
)
async def test_heat_link_model_names(
    parser: NestParser,
    raw_data: dict[str, Any],
    heat_link_model: str,
    expected: str,
) -> None:
    """Heat link hardware codes become names a user recognises; see issue #11."""
    raw_data[f"device.{THERMOSTAT}"]["heat_link_model"] = heat_link_model

    heat_link = _by_serial(parser, raw_data)["09AA00AA00AA0AAB"]

    assert isinstance(heat_link, NestHeatLink)
    assert heat_link.model == expected
    assert heat_link.hot_water_mode is HotWaterMode.SCHEDULE


async def test_heat_link_serial_number_provenance(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A heat link that reports no serial gets a derived one, not a hardware one."""
    heat_link = _by_serial(parser, raw_data)["09AA00AA00AA0AAB"]

    assert isinstance(heat_link, NestHeatLink)
    assert heat_link.has_own_serial_number
    assert heat_link.hardware_serial_number == "09AA00AA00AA0AAB"

    del raw_data[f"device.{THERMOSTAT}"]["heat_link_serial_number"]

    derived = _by_serial(parser, raw_data)[f"{THERMOSTAT}-hot-water"]

    assert isinstance(derived, NestHeatLink)
    assert not derived.has_own_serial_number
    assert derived.hardware_serial_number is None


async def test_no_heat_link_without_hot_water(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A thermostat with no hot water does not get a heat link."""
    device = raw_data[f"device.{THERMOSTAT}"]
    device["has_hot_water_control"] = False
    device["has_hot_water_temperature"] = False

    assert "09AA00AA00AA0AAB" not in _by_serial(parser, raw_data)


async def test_structure_needs_its_protobuf_key(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Without the protobuf resource id the structure cannot be controlled."""
    for key in [key for key in raw_data if key.startswith("STRUCTURE_")]:
        del raw_data[key]

    assert not [
        device
        for device in parser.parse_all(raw_data).devices
        if isinstance(device, NestStructure)
    ]


@pytest.mark.parametrize(
    ("away", "vacation", "expected"),
    [
        (False, False, StructureMode.HOME),
        (True, False, StructureMode.AWAY),
        (True, True, StructureMode.VACATION),
    ],
)
async def test_structure_mode(
    parser: NestParser,
    raw_data: dict[str, Any],
    away: bool,
    vacation: bool,
    expected: StructureMode,
) -> None:
    """The REST away and vacation flags map onto the structure mode."""
    structure = raw_data["structure.00000000-0000-0000-0000-000000000001"]
    structure["away"] = away
    structure["vacation_mode"] = vacation

    parsed = _by_serial(parser, raw_data)["00000000-0000-0000-0000-000000000001"]

    assert parsed.mode is expected


async def test_protobuf_lock(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """The lock's battery, bolt state and relock settings are parsed."""
    lock = _by_serial(parser, raw_data)[LOCK_SERIAL]

    assert isinstance(lock, NestLock)
    assert lock.is_protobuf
    assert lock.bolt_state is LockBoltState.LOCKED
    assert lock.battery_level == pytest.approx(85, abs=0.1)
    assert lock.auto_relock_on
    assert lock.auto_relock_duration == 120
    assert lock.max_auto_relock_duration == 600


async def test_protobuf_thermostat_dual_fuel(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """The dual fuel settings are read off the equipment trait; see issue #60."""
    thermostat = _by_serial(parser, raw_data)["18B430DDDDDD0002"]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.is_protobuf
    assert thermostat.has_dual_fuel
    assert thermostat.dual_fuel_breakpoint == pytest.approx(-2.187271, abs=1e-5)
    assert thermostat.temperature_scale is TemperatureScale.FAHRENHEIT


async def test_protobuf_thermostat_hardware_version(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """The device's own hardware model and revision reach hardware_version; PR #63."""
    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.product_id_description == (
        "Nest Thermostat E Display (1st Generation)"
    )
    assert thermostat.product_revision == 8
    assert thermostat.hardware_version == (
        "Nest Thermostat E Display (1st Generation) rev 8"
    )


async def test_protobuf_thermostat_hardware_version_without_revision(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """ProductRevision has no field presence, so an unset one must not read as rev 0."""
    identity: weave_description_pb2.DeviceIdentityTrait = raw_data[THERMOSTAT_KEY][
        weave_description_pb2.DeviceIdentityTrait.DESCRIPTOR.full_name
    ]
    identity.ClearField("productRevision")

    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.product_revision is None
    assert thermostat.hardware_version == "Nest Thermostat E Display (1st Generation)"


async def test_protobuf_thermostat_reports_no_stage_while_idle(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """An idle protobuf thermostat runs no stage; see issue #66."""
    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.hvac_state is ThermostatHvacState.OFF
    assert thermostat.hvac_stage is ThermostatHvacStage.OFF


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("coolStage1Active", ThermostatHvacStage.COOL_STAGE_1),
        ("coolStage2Active", ThermostatHvacStage.COOL_STAGE_2),
        ("coolStage3Active", ThermostatHvacStage.COOL_STAGE_3),
        ("heatStage1Active", ThermostatHvacStage.HEAT_STAGE_1),
        ("heatStage2Active", ThermostatHvacStage.HEAT_STAGE_2),
        ("heatStage3Active", ThermostatHvacStage.HEAT_STAGE_3),
        ("alternateHeatStage1Active", ThermostatHvacStage.ALTERNATE_HEAT_STAGE_1),
        ("alternateHeatStage2Active", ThermostatHvacStage.ALTERNATE_HEAT_STAGE_2),
        ("auxiliaryHeatActive", ThermostatHvacStage.AUXILIARY_HEAT),
        ("emergencyHeatActive", ThermostatHvacStage.EMERGENCY_HEAT),
    ],
)
def test_protobuf_thermostat_stage(
    parser: NestParser,
    raw_data: dict[str, Any],
    flag: str,
    expected: ThermostatHvacStage,
) -> None:
    """Every stage flag the thermostat reports is parsed; see issue #66."""
    setattr(_hvac_state(raw_data), flag, True)

    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.hvac_stage is expected


def test_protobuf_thermostat_stage_reports_the_most_capable_stage(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Stages stack, so supplemental heat and higher stages win; see issue #66."""
    hvac_state = _hvac_state(raw_data)
    hvac_state.coolStage1Active = True
    hvac_state.heatStage1Active = True
    hvac_state.heatStage2Active = True

    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert thermostat.hvac_stage is ThermostatHvacStage.HEAT_STAGE_2
    # The state stays collapsed; only the stage says which equipment is running.
    assert thermostat.hvac_state is ThermostatHvacState.HEATING

    hvac_state.auxiliaryHeatActive = True

    assert (
        _by_serial(parser, raw_data)[THERMOSTAT_SERIAL].hvac_stage
        is ThermostatHvacStage.AUXILIARY_HEAT
    )

    hvac_state.emergencyHeatActive = True

    assert (
        _by_serial(parser, raw_data)[THERMOSTAT_SERIAL].hvac_stage
        is ThermostatHvacStage.EMERGENCY_HEAT
    )


async def test_empty_payload(parser: NestParser) -> None:
    """An empty account parses to no devices rather than raising."""
    assert parser.parse_all({}).devices == []


async def test_protobuf_protect(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """A protobuf Protect is parsed with its alarms clear."""
    protect = _by_serial(parser, raw_data)[PROTECT_SERIAL]

    assert protect.is_protobuf
    assert protect.name == "Landing"
    assert not protect.smoke_status
    assert not protect.co_status


async def test_protobuf_camera(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """A protobuf camera reports whether it is recording."""
    camera = _by_serial(parser, raw_data)[CAMERA_SERIAL]

    assert camera.is_protobuf
    assert camera.name == "Driveway"
    assert camera.streaming_enabled


async def test_protobuf_temperature_sensor(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A protobuf remote sensor reports its temperature and battery."""
    sensor = _by_serial(parser, raw_data)[TEMP_SENSOR_SERIAL]

    assert sensor.is_protobuf
    assert sensor.current_temperature == 19.5
    assert sensor.battery_level == pytest.approx(71, abs=0.1)
    assert not sensor.is_active_sensor
