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
    NestCamera,
    NestDoorbell,
    NestHeatLink,
    NestLock,
    NestStructure,
    NestTempSensor,
    NestThermostat,
)
from custom_components.nest_legacy.pynest.parser import NestParser
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
    located_pb2 as nest_located_pb2,
    occupancy_pb2 as nest_occupancy_pb2,
    structure_pb2 as nest_structure_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait.product import (
    protect_pb2 as nest_protect_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.weave import (
    common_pb2 as weave_common_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.weave.trait import (
    description_pb2 as weave_description_pb2,
)
import pytest

from homeassistant.core import HomeAssistant

from .. import async_load_fixture_json
from ..const import (
    CAMERA_SERIAL,
    HEAT_LINK_SERIAL,
    HOT_WATER_TRANSITION_SECONDS,
    LOCK_KEY,
    LOCK_SERIAL,
    PROTECT_KEY,
    PROTECT_SERIAL,
    STRUCTURE_KEY,
    TEMP_SENSOR_KEY,
    TEMP_SENSOR_SERIAL,
    THERMOSTAT_KEY,
    THERMOSTAT_SERIAL,
    hot_water_traits,
    protobuf_updates,
)

PROTECT = "09AA00AA00AA0AA1"

_HEAT_LINK_CONNECTION = nest_hvac_pb2.HeatLinkSettingsTrait.HeatLinkConnectionType


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


def _thermostat_trait(raw_data: dict[str, Any], trait_class: type[Any]) -> Any:
    """Return the protobuf thermostat's trait of the given class."""
    return raw_data[THERMOSTAT_KEY][trait_class.DESCRIPTOR.full_name]


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
    """One payload produces one device per physical device."""
    devices = _by_serial(parser, raw_data)

    assert set(devices) == {
        "00000000-0000-0000-0000-000000000001",
        PROTECT,
        "18B430CCCCCC0002",
        LOCK_SERIAL,
        THERMOSTAT_SERIAL,
        PROTECT_SERIAL,
        CAMERA_SERIAL,
        TEMP_SENSOR_SERIAL,
    }


async def test_rest_thermostat_and_sensor_are_parsed(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """REST-only thermostats, Heat Links and sensors are parsed alongside protobuf ones; see issue #99."""
    raw_data["device.09AA00AA00AA0AAA"] = {
        "serial_number": "09AA00AA00AA0AAA",
        "where_id": "00000000-0000-0000-0000-000000000001",
        "current_schedule_mode": "HEAT",
        "leaf": True,
        "current_humidity": 48,
        "backplate_temperature": 20.5,
        "temperature_scale": "C",
        "has_hot_water_control": True,
        "has_hot_water_temperature": True,
        "hot_water_active": True,
        "hot_water_boiling_state": True,
        "hot_water_away_enabled": True,
        "hot_water_away_active": False,
        "hot_water_boost_time_to_end": 0,
        "current_water_temperature": 52.0,
        "hot_water_temperature": 60.0,
        "heat_link_serial_number": "09AA00AA00AA0HL1",
        "heat_link_model": "Amber-2.5",
        "heat_link_sw_version": "2.1-9",
        "eco": {"mode": "schedule"},
    }
    raw_data["shared.09AA00AA00AA0AAA"] = {
        "target_temperature": 21.0,
        "target_temperature_type": "heat",
        "current_temperature": 20.0,
        "can_heat": True,
        "can_cool": False,
        "hvac_heater_state": True,
    }
    raw_data["track.09AA00AA00AA0AAA"] = {"online": True}
    raw_data["link.09AA00AA00AA0AAA"] = {
        "structure": "structure.00000000-0000-0000-0000-000000000001"
    }
    raw_data["rcs_settings.09AA00AA00AA0AAA"] = {
        "associated_rcs_sensors": ["kryptonite.18B430CCCCCC0001"],
        "active_rcs_sensors": ["kryptonite.18B430CCCCCC0001"],
    }
    raw_data["kryptonite.18B430CCCCCC0001"] = {
        "serial_number": "18B430CCCCCC0001",
        "where_id": "00000000-0000-0000-0000-000000000003",
        "current_temperature": 19.25,
        "battery_level": 88,
        "model": "Kryptonite-1.0",
    }

    devices = _by_serial(parser, raw_data)

    # Protobuf thermostat is still parsed.
    assert devices[THERMOSTAT_SERIAL].is_protobuf
    # REST thermostat, its Heat Link, and its remote sensor are also parsed.
    thermostat = devices["09AA00AA00AA0AAA"]
    assert isinstance(thermostat, NestThermostat)
    assert not thermostat.is_protobuf
    assert thermostat.model == "Learning Thermostat (3rd gen)"
    assert thermostat.hvac_mode is ThermostatHvacMode.HEAT
    assert thermostat.hvac_state is ThermostatHvacState.HEATING
    assert thermostat.hvac_stage is None
    assert thermostat.target_temperature == 21.0
    assert thermostat.current_temperature == 19.25
    assert thermostat.backplate_temperature == 20.5

    heat_link = devices["09AA00AA00AA0HL1"]
    assert isinstance(heat_link, NestHeatLink)
    assert not heat_link.is_protobuf
    assert heat_link.associated_thermostat_object_key == "device.09AA00AA00AA0AAA"
    assert heat_link.hot_water_active
    assert heat_link.current_temperature == 52.0
    assert heat_link.target_temperature == 60.0

    sensor = devices["18B430CCCCCC0001"]
    assert isinstance(sensor, NestTempSensor)
    assert not sensor.is_protobuf
    assert sensor.is_active_sensor
    assert sensor.associated_thermostat_object_key == "device.09AA00AA00AA0AAA"
    assert sensor.current_temperature == 19.25


async def test_protobuf_thermostat_supersedes_rest_thermostat(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A thermostat or sensor on both APIs is parsed once from protobuf."""
    raw_data[f"device.{THERMOSTAT_SERIAL}"] = {
        "serial_number": THERMOSTAT_SERIAL,
        "has_hot_water_control": True,
        "heat_link_serial_number": "09AA00AA00AA0HL2",
    }
    raw_data[f"shared.{THERMOSTAT_SERIAL}"] = {"target_temperature": 18.0}
    raw_data[f"kryptonite.{TEMP_SENSOR_SERIAL}"] = {
        "serial_number": TEMP_SENSOR_SERIAL,
        "current_temperature": 15.0,
    }

    parsed = parser.parse_all(raw_data).devices
    thermostats = [d for d in parsed if d.serial_number == THERMOSTAT_SERIAL]
    sensors = [d for d in parsed if d.serial_number == TEMP_SENSOR_SERIAL]

    assert len(thermostats) == 1
    assert thermostats[0].is_protobuf
    assert len(sensors) == 1
    assert sensors[0].is_protobuf
    assert not [d for d in parsed if d.serial_number == "09AA00AA00AA0HL2"]


async def test_rest_thermostat_eco_and_mode_fallbacks(
    parser: NestParser,
    raw_data: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REST thermostat eco mode and unknown target_temperature_type are handled."""
    raw_data["device.15AA00AA00AA0AAA"] = {
        "serial_number": "15AA00AA00AA0AAA",
        "temperature_scale": "F",
        "away_temperature_low": 12.0,
        "away_temperature_low_enabled": True,
        "away_temperature_high": 26.0,
        "away_temperature_high_enabled": False,
        "eco": {"mode": "manual-eco"},
    }
    raw_data["shared.15AA00AA00AA0AAA"] = {
        "target_temperature": 21.0,
        "target_temperature_type": "invalid-mode",
        "can_heat": True,
        "can_cool": False,
    }

    thermostat = _by_serial(parser, raw_data)["15AA00AA00AA0AAA"]
    assert isinstance(thermostat, NestThermostat)
    assert thermostat.model == "Thermostat E"
    assert thermostat.temperature_scale is TemperatureScale.FAHRENHEIT
    assert thermostat.is_eco_mode
    assert thermostat.hvac_mode is ThermostatHvacMode.HEAT
    assert thermostat.target_temperature == pytest.approx(12.2222, abs=1e-4)

    raw_data["device.15AA00AA00AA0AAA"]["eco"] = {"mode": "schedule"}
    thermostat = _by_serial(parser, raw_data)["15AA00AA00AA0AAA"]
    assert thermostat.hvac_mode is ThermostatHvacMode.OFF
    assert "Unsupported value for ThermostatHvacMode" in caplog.text


async def test_locations_come_from_both_apis(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Rooms are resolved from the REST wheres and the protobuf annotations."""
    devices = _by_serial(parser, raw_data)

    assert devices[PROTECT].location == "Hallway"
    assert devices[LOCK_SERIAL].location == "Front Door"


async def test_room_names_are_trimmed(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Stray whitespace in a room name stays out of the location."""
    for where in raw_data["where.00000000-0000-0000-0000-000000000001"]["wheres"]:
        where["name"] = f" {where['name']} "
    located = raw_data[LOCK_KEY][
        nest_located_pb2.DeviceLocatedSettingsTrait.DESCRIPTOR.full_name
    ]
    located.whereLabel.literal = " Kids Room"

    devices = _by_serial(parser, raw_data)

    assert devices[PROTECT].location == "Hallway"
    assert devices[LOCK_SERIAL].location == "Kids Room"


async def test_thermostat_values(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """The thermostat is parsed into the shape the entities expect."""
    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert isinstance(thermostat, NestThermostat)
    assert thermostat.temperature_scale is TemperatureScale.FAHRENHEIT
    assert thermostat.hvac_mode is ThermostatHvacMode.HEAT
    assert thermostat.hvac_state is ThermostatHvacState.OFF
    assert thermostat.hvac_stage is ThermostatHvacStage.OFF
    # 22.222222 C is exactly 72 F.
    assert thermostat.target_temperature == pytest.approx(22.2222, abs=1e-4)
    assert thermostat.current_temperature == pytest.approx(21.11)
    assert thermostat.backplate_temperature == pytest.approx(21.05)
    assert thermostat.current_humidity == 43
    assert thermostat.target_humidity == 35
    assert thermostat.has_humidifier
    assert thermostat.has_fan
    assert thermostat.can_heat
    assert thermostat.can_cool
    assert thermostat.online


async def test_unparseable_device_is_skipped(
    parser: NestParser,
    raw_data: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A device missing its serial number is skipped, not fatal; see issue #9."""
    del raw_data[f"topaz.{PROTECT}"]["serial_number"]

    devices = _by_serial(parser, raw_data)

    assert PROTECT not in devices
    # The rest of the account still comes through.
    assert THERMOSTAT_SERIAL in devices
    assert "due to a parsing error" in caplog.text


@pytest.mark.parametrize(
    ("scale", "expected"),
    [
        (
            nest_hvac_pb2.DisplaySettingsTrait.TemperatureScale.TEMPERATURE_SCALE_F,
            TemperatureScale.FAHRENHEIT,
        ),
        (
            nest_hvac_pb2.DisplaySettingsTrait.TemperatureScale.TEMPERATURE_SCALE_C,
            TemperatureScale.CELSIUS,
        ),
        # A scale the thermostat leaves unset falls back to Celsius.
        (
            nest_hvac_pb2.DisplaySettingsTrait.TemperatureScale.TEMPERATURE_SCALE_UNSPECIFIED,
            TemperatureScale.CELSIUS,
        ),
    ],
)
async def test_temperature_scale(
    parser: NestParser,
    raw_data: dict[str, Any],
    scale: nest_hvac_pb2.DisplaySettingsTrait.TemperatureScale.ValueType,
    expected: TemperatureScale,
) -> None:
    """The display scale decides between Fahrenheit and Celsius."""
    _thermostat_trait(
        raw_data, nest_hvac_pb2.DisplaySettingsTrait
    ).temperatureScale = scale

    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert thermostat.temperature_scale is expected


async def test_disabled_target_temperature_is_off(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A thermostat with its target temperature disabled is off."""
    target = _thermostat_trait(raw_data, nest_hvac_pb2.TargetTemperatureSettingsTrait)
    target.enabled.value = False

    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert thermostat.hvac_mode is ThermostatHvacMode.OFF
    assert thermostat.target_temperature is None


async def test_eco_on_a_heat_only_thermostat(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Eco on a heat only device stays in heat; see issue #38.

    Without the eco enabled flags, the equipment decides between heat, cool
    and a heat/cool range.
    """
    _thermostat_trait(
        raw_data, nest_hvac_pb2.HvacEquipmentCapabilitiesTrait
    ).hasStage1Cool = False
    _thermostat_trait(
        raw_data, nest_hvac_pb2.EcoModeStateTrait
    ).ecoMode = nest_hvac_pb2.EcoModeStateTrait.EcoMode.ECO_MODE_MANUAL_ECO
    eco = nest_hvac_pb2.EcoModeSettingsTrait()
    eco.ecoTemperatureHeat.value.value = 12.0
    eco.ecoTemperatureCool.value.value = 26.0
    raw_data[THERMOSTAT_KEY][
        nest_hvac_pb2.EcoModeSettingsTrait.DESCRIPTOR.full_name
    ] = eco

    thermostat = _by_serial(parser, raw_data)[THERMOSTAT_SERIAL]

    assert thermostat.is_eco_mode
    assert thermostat.hvac_mode is ThermostatHvacMode.HEAT
    # 12 C is 53.6 F, which the Fahrenheit thermostat snaps to 54 F.
    assert thermostat.target_temperature == pytest.approx(12.2222, abs=1e-4)


async def test_active_remote_sensor(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """The sensor the thermostat follows is flagged as active; see issue #20."""
    rcs = _thermostat_trait(raw_data, nest_hvac_pb2.RemoteComfortSensingSettingsTrait)
    rcs.activeRcsSelection.rcsSourceType = nest_hvac_pb2.RemoteComfortSensingSettingsTrait.RcsSourceType.RCS_SOURCE_TYPE_SINGLE_SENSOR
    rcs.activeRcsSelection.activeRcsSensor.resourceId = TEMP_SENSOR_KEY

    sensor = _by_serial(parser, raw_data)[TEMP_SENSOR_SERIAL]

    assert sensor.is_active_sensor
    assert sensor.associated_thermostat_object_key == THERMOSTAT_KEY


@pytest.mark.parametrize(
    ("heat_link_model", "expected"),
    [
        ("Amber-2.5", "Heat Link for Learning Thermostat (3rd gen, EU)"),
        ("Amber-1.6", "Heat Link for Learning Thermostat (2nd gen, EU)"),
        ("Agate-1.0", "Heat Link for Thermostat E (1st gen, EU)"),
        ("Something-9", "Heat Link (Something-9)"),
    ],
)
@pytest.mark.usefixtures("hot_water")
async def test_heat_link_model_names(
    parser: NestParser,
    raw_data: dict[str, Any],
    heat_link_model: str,
    expected: str,
) -> None:
    """Heat link hardware codes become names a user recognises; see issue #11."""
    _thermostat_trait(
        raw_data, nest_hvac_pb2.HeatLinkTrait
    ).heatLinkModel.value = heat_link_model

    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)
    assert heat_link.model == expected


@pytest.mark.usefixtures("hot_water")
async def test_heat_link_serial_number_provenance(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A heat link that reports no serial gets a derived one, not a hardware one."""
    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)
    assert heat_link.has_own_serial_number
    assert heat_link.hardware_serial_number == HEAT_LINK_SERIAL

    _thermostat_trait(raw_data, nest_hvac_pb2.HeatLinkTrait).ClearField(
        "heatLinkSerialNumber"
    )

    derived = _by_serial(parser, raw_data)[f"{THERMOSTAT_SERIAL}-hot-water"]

    assert isinstance(derived, NestHeatLink)
    assert not derived.has_own_serial_number
    assert derived.hardware_serial_number is None


async def test_no_heat_link_without_hot_water(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A thermostat with no hot water does not get a heat link."""
    devices = _by_serial(parser, raw_data)

    assert not [
        device for device in devices.values() if isinstance(device, NestHeatLink)
    ]


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


def _structures(parser: NestParser, raw_data: dict[str, Any]) -> list[NestStructure]:
    """Return the parsed structures."""
    return [
        device
        for device in parser.parse_all(raw_data).devices
        if isinstance(device, NestStructure)
    ]


def _add_home(raw_data: dict[str, Any], rest_id: str, resource_id: str) -> None:
    """Add a second home with both its REST bucket and protobuf resource."""
    raw_data[f"structure.{rest_id}"] = {
        **raw_data["structure.00000000-0000-0000-0000-000000000001"],
        "name": "Cabin",
        "away": True,
    }
    raw_data[resource_id] = {
        **raw_data[STRUCTURE_KEY],
        nest_structure_pb2.StructureInfoTrait.DESCRIPTOR.full_name: (
            nest_structure_pb2.StructureInfoTrait(
                name="Cabin", rtsStructureId=f"structure.{rest_id}"
            )
        ),
    }


async def test_rest_structure_is_not_duplicated(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A home with a REST bucket is parsed from it, not again from protobuf."""
    structures = _structures(parser, raw_data)

    assert len(structures) == 1
    assert not structures[0].is_protobuf
    assert structures[0].object_key == STRUCTURE_KEY


async def test_protobuf_structure(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """Without its REST bucket, the home is parsed from protobuf instead.

    It keeps the serial number of the REST structure, so switching the
    protobuf option does not replace the device.
    """
    del raw_data["structure.00000000-0000-0000-0000-000000000001"]

    structures = _structures(parser, raw_data)

    assert len(structures) == 1
    structure = structures[0]
    assert structure.is_protobuf
    assert structure.object_key == STRUCTURE_KEY
    assert structure.serial_number == "00000000-0000-0000-0000-000000000001"
    assert structure.name == "Test Home"
    assert structure.mode is StructureMode.HOME
    # The street address is not a room to suggest as the device's area.
    assert structure.location is None


async def test_each_home_gets_its_own_protobuf_key(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Each REST structure controls the protobuf resource that names it."""
    # Listed first, so picking the first protobuf structure would get it wrong.
    raw_data = {"STRUCTURE_00000000000000AA": {}, **raw_data}
    _add_home(raw_data, "cabin-id", "STRUCTURE_00000000000000AA")

    keys = {
        structure.serial_number: structure.object_key
        for structure in _structures(parser, raw_data)
    }

    assert keys == {
        "00000000-0000-0000-0000-000000000001": STRUCTURE_KEY,
        "cabin-id": "STRUCTURE_00000000000000AA",
    }


async def test_ambiguous_protobuf_structure_is_skipped(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """With several unidentified protobuf homes, none is guessed."""
    raw_data[STRUCTURE_KEY] = {}
    raw_data["STRUCTURE_00000000000000AA"] = {}

    assert not _structures(parser, raw_data)


async def test_lone_unidentified_protobuf_structure_is_used(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A single protobuf home without StructureInfoTrait is still matched."""
    raw_data[STRUCTURE_KEY] = {}

    structures = _structures(parser, raw_data)

    assert [structure.object_key for structure in structures] == [STRUCTURE_KEY]


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


@pytest.fixture
def hot_water(raw_data: dict[str, Any]) -> nest_hvac_pb2.HotWaterTrait:
    """Give the protobuf thermostat a Heat Link and return its hot water trait."""
    raw_data[THERMOSTAT_KEY].update(hot_water_traits())
    return raw_data[THERMOSTAT_KEY][nest_hvac_pb2.HotWaterTrait.DESCRIPTOR.full_name]


@pytest.mark.usefixtures("hot_water")
async def test_protobuf_heat_link(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """The Heat Link's own hardware and hot water state are parsed."""
    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)
    assert heat_link.is_protobuf
    assert heat_link.model == "Heat Link for Learning Thermostat (3rd gen, EU)"
    assert heat_link.software_version == "2.1"
    assert heat_link.hot_water_mode is HotWaterMode.SCHEDULE
    assert heat_link.hot_water_active
    assert heat_link.hot_water_control_active
    assert heat_link.current_temperature == 54.5


def _without_hot_water_flags(raw_data: dict[str, Any]) -> None:
    """Clear the hot water flags the capabilities trait may carry."""
    capabilities = raw_data[THERMOSTAT_KEY][
        nest_hvac_pb2.HvacEquipmentCapabilitiesTrait.DESCRIPTOR.full_name
    ]
    capabilities.hasHotWaterControl = False
    capabilities.hasHotWaterTemperature = False


@pytest.mark.usefixtures("hot_water")
async def test_protobuf_heat_link_without_capability_flags(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """The hot water traits alone prove the Heat Link is there.

    A Thermostat E reports heat stages on HvacEquipmentCapabilitiesTrait and
    leaves both hot water flags unset.
    """
    _without_hot_water_flags(raw_data)

    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)


@pytest.mark.usefixtures("hot_water")
async def test_no_protobuf_heat_link_for_central_heating_only(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A Heat Link wired for central heating only has no hot water.

    Its heat connection type is set like that of a Heat Link that also drives
    hot water, so it is not proof of hot water control.
    """
    _without_hot_water_flags(raw_data)
    traits = raw_data[THERMOSTAT_KEY]
    traits.pop(nest_hvac_pb2.HotWaterTrait.DESCRIPTOR.full_name)
    traits.pop(nest_hvac_pb2.HotWaterSettingsTrait.DESCRIPTOR.full_name)
    traits[nest_hvac_pb2.HeatLinkSettingsTrait.DESCRIPTOR.full_name] = (
        nest_hvac_pb2.HeatLinkSettingsTrait(
            heatConnectionType=_HEAT_LINK_CONNECTION.HEAT_LINK_CONNECTION_TYPE_ON_OFF,
            hotWaterConnectionType=_HEAT_LINK_CONNECTION.HEAT_LINK_CONNECTION_TYPE_NOT_CONNECTED,
        )
    )

    assert HEAT_LINK_SERIAL not in _by_serial(parser, raw_data)


@pytest.mark.usefixtures("hot_water")
async def test_no_protobuf_heat_link_from_empty_traits(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """An empty trait is not proof of a Heat Link.

    Traits are sometimes delivered with no fields set.
    """
    _without_hot_water_flags(raw_data)
    traits = raw_data[THERMOSTAT_KEY]
    traits[nest_hvac_pb2.HotWaterTrait.DESCRIPTOR.full_name] = (
        nest_hvac_pb2.HotWaterTrait()
    )
    traits[nest_hvac_pb2.HotWaterSettingsTrait.DESCRIPTOR.full_name] = (
        nest_hvac_pb2.HotWaterSettingsTrait()
    )

    assert HEAT_LINK_SERIAL not in _by_serial(parser, raw_data)


async def test_protobuf_hot_water_away_is_the_state_not_the_setting(
    parser: NestParser,
    raw_data: dict[str, Any],
    hot_water: nest_hvac_pb2.HotWaterTrait,
) -> None:
    """Away follows the trait, not the Home/Away Assist setting; see issue #68."""
    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)
    # The fixture follows the structure mode, but the structure is home.
    assert heat_link.hot_water_away_enabled
    assert not heat_link.hot_water_away_active

    hot_water.awayActive = True

    assert _by_serial(parser, raw_data)[HEAT_LINK_SERIAL].hot_water_away_active


async def test_protobuf_hot_water_temperature_needs_a_sensor(
    parser: NestParser,
    raw_data: dict[str, Any],
    hot_water: nest_hvac_pb2.HotWaterTrait,
) -> None:
    """An empty temperature message is not a 0 degree reading; see issue #69."""
    hot_water.ClearField("temperature")
    hot_water.temperature.SetInParent()

    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)
    assert hot_water.HasField("temperature")
    assert heat_link.current_temperature is None


async def test_protobuf_hot_water_next_transition_time(
    parser: NestParser,
    raw_data: dict[str, Any],
    hot_water: nest_hvac_pb2.HotWaterTrait,
) -> None:
    """The next hot water schedule change is parsed; see issue #70."""
    heat_link = _by_serial(parser, raw_data)[HEAT_LINK_SERIAL]

    assert isinstance(heat_link, NestHeatLink)
    assert heat_link.hot_water_next_transition_time == HOT_WATER_TRANSITION_SECONDS

    hot_water.ClearField("nextTransitionTime")

    assert (
        _by_serial(parser, raw_data)[HEAT_LINK_SERIAL].hot_water_next_transition_time
        == 0
    )


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


REST_PROTECT = "09AA00AA00AA0AA1"
_REST_PROTECT_KEY = f"topaz.{REST_PROTECT}"
_REST_STRUCTURE_ID = "00000000-0000-0000-0000-000000000001"


async def test_rest_protect_extras(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """The REST Protect reports hush, CO peak and the extra component tests."""
    raw_data[_REST_PROTECT_KEY].update(
        hushed_state=True,
        co_previous_peak=120,
        component_als_test_passed=False,
        component_temp_test_passed=True,
    )

    protect = _by_serial(parser, raw_data)[REST_PROTECT]

    assert protect.hushed_state is True
    assert protect.co_previous_peak == 120
    assert protect.component_als_test_passed is False
    assert protect.component_temp_test_passed is True


async def test_rest_protect_extras_missing(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Fields the Protect does not send stay None, so no entity is made."""
    for field in (
        "hushed_state",
        "component_als_test_passed",
        "component_temp_test_passed",
    ):
        raw_data[_REST_PROTECT_KEY].pop(field)
    raw_data[_REST_PROTECT_KEY]["co_previous_peak"] = "not a number"

    protect = _by_serial(parser, raw_data)[REST_PROTECT]

    assert protect.hushed_state is None
    assert protect.co_previous_peak is None
    assert protect.component_als_test_passed is None
    assert protect.component_temp_test_passed is None


async def test_protobuf_protect_has_no_rest_only_fields(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """The protobuf Protect has no source for the REST-only fields."""
    protect = _by_serial(parser, raw_data)[PROTECT_SERIAL]

    assert protect.hushed_state is None
    assert protect.co_previous_peak is None


async def test_structure_safety_summary(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A home with a Protect carries its safety summary counts."""
    raw_data[f"safety_summary.{_REST_STRUCTURE_ID}"] = {
        "total_critical_failures": 1,
        "total_warnings": 2,
    }

    (structure,) = _structures(parser, raw_data)

    assert structure.safety_critical_failures == 1
    assert structure.safety_warnings == 2


async def test_structure_safety_summary_needs_a_protect(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A home without a Protect does not expose the always-zero summary."""
    del raw_data[_REST_PROTECT_KEY]["structure_id"]

    (structure,) = _structures(parser, raw_data)

    assert structure.safety_critical_failures is None
    assert structure.safety_warnings is None


async def test_unnamed_camera(parser: NestParser, raw_data: dict[str, Any]) -> None:
    """A camera named only by its room gets the same name as over protobuf."""
    raw_data["quartz.18B430CCCCCC0002"]["description"] = ""

    camera = _by_serial(parser, raw_data)["18B430CCCCCC0002"]

    assert camera.name == "Camera"
    assert camera.location == "Front Door"


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


async def test_rest_camera_parses_string_booleans(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """REST camera and doorbell boolean properties reported as strings are parsed."""
    doorbell_data = raw_data["quartz.18B430CCCCCC0002"]
    doorbell_data["properties"] = {
        **doorbell_data.get("properties", {}),
        "doorbell.indoor_chime.enabled": "false",
        "doorbell.chime_assist.enabled": "false",
        "video.flipped": "false",
    }
    raw_data["quartz.18B430CCCCCC0009"] = {
        **doorbell_data,
        "serial_number": "18B430CCCCCC0009",
        "model": "Nest Cam Indoor",
        "properties": {"video.flipped": "false"},
    }

    devices = _by_serial(parser, raw_data)
    doorbell = devices["18B430CCCCCC0002"]
    camera = devices["18B430CCCCCC0009"]

    assert isinstance(doorbell, NestDoorbell)
    assert doorbell.indoor_chime_enabled is False
    assert doorbell.doorbell_chime_assist_enabled is False
    assert doorbell.video_flipped is False
    assert isinstance(camera, NestCamera)
    assert not isinstance(camera, NestDoorbell)
    assert camera.video_flipped is False


async def test_rest_thermostat_occupancy_from_protobuf_structure(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """A REST thermostat reads occupancy from the protobuf structure when REST structure is omitted."""
    del raw_data["structure.00000000-0000-0000-0000-000000000001"]
    raw_data["device.09AA00AA00AA0AAA"] = {
        "serial_number": "09AA00AA00AA0AAA",
        "current_schedule_mode": "HEAT",
        "temperature_scale": "C",
    }
    raw_data["shared.09AA00AA00AA0AAA"] = {
        "target_temperature": 21.0,
        "target_temperature_type": "heat",
        "current_temperature": 20.0,
        "can_heat": True,
        "can_cool": False,
    }
    raw_data["link.09AA00AA00AA0AAA"] = {
        "structure": "structure.00000000-0000-0000-0000-000000000001"
    }

    thermostat = _by_serial(parser, raw_data)["09AA00AA00AA0AAA"]
    assert isinstance(thermostat, NestThermostat)
    assert thermostat.occupancy is True


async def test_multi_home_protobuf_structure_traits(
    parser: NestParser, raw_data: dict[str, Any]
) -> None:
    """Protobuf devices match their own home's occupancy and safety summary."""
    cabin_structure = {
        nest_structure_pb2.StructureInfoTrait.DESCRIPTOR.full_name: (
            nest_structure_pb2.StructureInfoTrait(
                name="Cabin", rtsStructureId="structure.cabin-id"
            )
        ),
        nest_occupancy_pb2.StructureModeTrait.DESCRIPTOR.full_name: (
            nest_occupancy_pb2.StructureModeTrait(
                structureMode=nest_occupancy_pb2.StructureModeTrait.StructureMode.STRUCTURE_MODE_AWAY
            )
        ),
        nest_protect_pb2.SafetySummaryTrait.DESCRIPTOR.full_name: (
            nest_protect_pb2.SafetySummaryTrait()
        ),
    }
    # Place the away cabin structure before the main home in raw_data.
    raw_data = {"STRUCTURE_00000000000000AA": cabin_structure, **raw_data}
    raw_data[f"link.{THERMOSTAT_SERIAL}"] = {
        "structure": "structure.00000000-0000-0000-0000-000000000001"
    }
    summary = nest_protect_pb2.SafetySummaryTrait(
        criticalDevices=[
            nest_protect_pb2.SafetySummaryTrait.DeviceStatus(
                resourceId=weave_common_pb2.ResourceId(resourceId=PROTECT_KEY),
                failures=[
                    nest_protect_pb2.SafetySummaryTrait.FailureType.FAILURE_TYPE_SMOKE
                ],
            )
        ]
    )
    raw_data[STRUCTURE_KEY][
        nest_protect_pb2.SafetySummaryTrait.DESCRIPTOR.full_name
    ] = summary

    devices = _by_serial(parser, raw_data)

    assert devices[THERMOSTAT_SERIAL].occupancy is True
    assert devices[PROTECT_SERIAL].component_smoke_test_passed is False
