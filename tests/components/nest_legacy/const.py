"""Protobuf fixtures for the Nest Legacy integration tests.

The observe stream hands the coordinator protobuf messages rather than JSON, so
these are built with the generated classes instead of being loaded from disk.
"""

from typing import Any

from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
    located_pb2 as nest_located_pb2,
    occupancy_pb2 as nest_occupancy_pb2,
    safety_pb2 as nest_safety_pb2,
    security_pb2 as nest_security_pb2,
    sensor_pb2 as nest_sensor_pb2,
    structure_pb2 as nest_structure_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait.product import (
    camera_pb2 as nest_camera_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.weave import (
    common_pb2 as weave_common_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.weave.trait import (
    description_pb2 as weave_description_pb2,
    heartbeat_pb2 as weave_heartbeat_pb2,
    power_pb2 as weave_power_pb2,
    security_pb2 as weave_security_pb2,
)

STRUCTURE_KEY = "STRUCTURE_0000000000000001"
LOCK_KEY = "DEVICE_0000000000000002"
THERMOSTAT_KEY = "DEVICE_0000000000000003"
PROTECT_KEY = "DEVICE_0000000000000004"
CAMERA_KEY = "DEVICE_0000000000000005"
TEMP_SENSOR_KEY = "DEVICE_0000000000000006"

LOCK_SERIAL = "18B430DDDDDD0001"
THERMOSTAT_SERIAL = "18B430DDDDDD0002"
PROTECT_SERIAL = "18B430DDDDDD0003"
CAMERA_SERIAL = "18B430DDDDDD0004"
TEMP_SENSOR_SERIAL = "18B430DDDDDD0005"

_WHERE_FRONT_DOOR = "where-front-door"
_WHERE_HALLWAY = "where-hallway"


def _trait_key(message_class: type[Any]) -> str:
    """Return the key the parser looks a trait up under."""
    return str(message_class.DESCRIPTOR.full_name)


def _structure_traits() -> dict[str, Any]:
    """Return the traits of a protobuf structure."""
    annotations = nest_located_pb2.LocatedAnnotationsTrait()
    for index, (where_id, label) in enumerate(
        ((_WHERE_FRONT_DOOR, "Front Door"), (_WHERE_HALLWAY, "Hallway"))
    ):
        entry = annotations.predefinedWheres[index]
        entry.whereId.resourceId = where_id
        entry.label.literal = label

    info = nest_structure_pb2.StructureInfoTrait(name="Test Home")
    location = nest_structure_pb2.StructureLocationTrait(
        addressLines=["1 Test Street"],
    )
    mode = nest_occupancy_pb2.StructureModeTrait(
        structureMode=nest_occupancy_pb2.StructureModeTrait.StructureMode.STRUCTURE_MODE_HOME
    )
    identity = weave_description_pb2.DeviceIdentityTrait(
        serialNumber="00000000-0000-0000-0000-000000000001"
    )
    return {
        _trait_key(nest_located_pb2.LocatedAnnotationsTrait): annotations,
        _trait_key(nest_structure_pb2.StructureInfoTrait): info,
        _trait_key(nest_structure_pb2.StructureLocationTrait): location,
        _trait_key(nest_occupancy_pb2.StructureModeTrait): mode,
        _trait_key(weave_description_pb2.DeviceIdentityTrait): identity,
    }


def _lock_traits() -> dict[str, Any]:
    """Return the traits of a Nest x Yale lock."""
    bolt_lock = weave_security_pb2.BoltLockTrait(
        lockedState=weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_LOCKED,
        actuatorState=weave_security_pb2.BoltLockTrait.BoltActuatorState.BOLT_ACTUATOR_STATE_OK,
        boltLockActor=weave_security_pb2.BoltLockTrait.BoltLockActorStruct(
            method=weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_KEYPAD_PIN
        ),
    )
    settings = nest_security_pb2.EnhancedBoltLockSettingsTrait(autoRelockOn=True)
    settings.autoRelockDuration.seconds = 120
    capabilities = weave_security_pb2.BoltLockCapabilitiesTrait()
    capabilities.maxAutoRelockDuration.seconds = 600
    tamper = weave_security_pb2.TamperTrait(
        tamperState=weave_security_pb2.TamperTrait.TamperState.TAMPER_STATE_CLEAR
    )
    battery = weave_power_pb2.BatteryPowerSourceTrait()
    battery.remaining.remainingPercent.value = 0.85
    battery.assessedVoltage.value = 5.8
    identity = weave_description_pb2.DeviceIdentityTrait(
        serialNumber=LOCK_SERIAL, softwareVersion="1.0.9"
    )
    label = weave_description_pb2.LabelSettingsTrait(label="Front Door")
    liveness = weave_heartbeat_pb2.LivenessTrait(
        status=weave_heartbeat_pb2.LivenessTrait.LIVENESS_DEVICE_STATUS_ONLINE
    )
    located = nest_located_pb2.DeviceLocatedSettingsTrait()
    located.whereAnnotationRid.resourceId = _WHERE_FRONT_DOOR
    return {
        _trait_key(weave_security_pb2.BoltLockTrait): bolt_lock,
        _trait_key(nest_security_pb2.EnhancedBoltLockSettingsTrait): settings,
        _trait_key(weave_security_pb2.BoltLockCapabilitiesTrait): capabilities,
        _trait_key(weave_security_pb2.TamperTrait): tamper,
        _trait_key(weave_power_pb2.BatteryPowerSourceTrait): battery,
        _trait_key(weave_description_pb2.DeviceIdentityTrait): identity,
        _trait_key(weave_description_pb2.LabelSettingsTrait): label,
        _trait_key(weave_heartbeat_pb2.LivenessTrait): liveness,
        _trait_key(nest_located_pb2.DeviceLocatedSettingsTrait): located,
    }


def dual_fuel_trait(
    *,
    breakpoint_celsius: float | None = -2.187271,
    override: (
        nest_hvac_pb2.EquipmentSettingsTrait.DualFuelOverride.ValueType
    ) = nest_hvac_pb2.EquipmentSettingsTrait.DualFuelOverride.DUAL_FUEL_OVERRIDE_NONE,
    dual_fuel: bool = True,
) -> nest_hvac_pb2.EquipmentSettingsTrait:
    """Return an EquipmentSettingsTrait describing a dual fuel system.

    Mirrors the payloads reported in issue #60.
    """
    selection = nest_hvac_pb2.EquipmentSettingsTrait.DualFuelSelection
    trait = nest_hvac_pb2.EquipmentSettingsTrait(
        dualFuelSelected=(
            selection.DUAL_FUEL_SELECTION_DUAL_FUEL
            if dual_fuel
            else selection.DUAL_FUEL_SELECTION_SINGLE_FUEL
        ),
        dualFuelBreakpointOverride=override,
    )
    if breakpoint_celsius is not None:
        trait.dualFuelBreakpoint.value = breakpoint_celsius
    return trait


def _thermostat_traits() -> dict[str, Any]:
    """Return the traits of a protobuf thermostat with a dual fuel system."""
    hvac = nest_hvac_pb2.HvacControlTrait()
    display = nest_hvac_pb2.DisplaySettingsTrait(
        temperatureScale=nest_hvac_pb2.DisplaySettingsTrait.TemperatureScale.TEMPERATURE_SCALE_F
    )
    identity = weave_description_pb2.DeviceIdentityTrait(
        serialNumber=THERMOSTAT_SERIAL, softwareVersion="6.2-24"
    )
    label = weave_description_pb2.LabelSettingsTrait(label="Upstairs")
    liveness = weave_heartbeat_pb2.LivenessTrait(
        status=weave_heartbeat_pb2.LivenessTrait.LIVENESS_DEVICE_STATUS_ONLINE
    )
    located = nest_located_pb2.DeviceLocatedSettingsTrait()
    located.whereAnnotationRid.resourceId = _WHERE_HALLWAY
    return {
        _trait_key(nest_hvac_pb2.HvacControlTrait): hvac,
        _trait_key(nest_hvac_pb2.DisplaySettingsTrait): display,
        _trait_key(nest_hvac_pb2.EquipmentSettingsTrait): dual_fuel_trait(),
        _trait_key(weave_description_pb2.DeviceIdentityTrait): identity,
        _trait_key(weave_description_pb2.LabelSettingsTrait): label,
        _trait_key(weave_heartbeat_pb2.LivenessTrait): liveness,
        _trait_key(nest_located_pb2.DeviceLocatedSettingsTrait): located,
    }


def _protect_traits() -> dict[str, Any]:
    """Return the traits of a protobuf Nest Protect."""
    alarm = nest_safety_pb2.SafetyAlarmTrait.AlarmState
    identity = weave_description_pb2.DeviceIdentityTrait(
        serialNumber=PROTECT_SERIAL, softwareVersion="3.4.2"
    )
    label = weave_description_pb2.LabelSettingsTrait(label="Landing")
    liveness = weave_heartbeat_pb2.LivenessTrait(
        status=weave_heartbeat_pb2.LivenessTrait.LIVENESS_DEVICE_STATUS_ONLINE
    )
    located = nest_located_pb2.DeviceLocatedSettingsTrait()
    located.whereAnnotationRid.resourceId = _WHERE_HALLWAY
    return {
        _trait_key(nest_sensor_pb2.SmokeTrait): nest_sensor_pb2.SmokeTrait(),
        _trait_key(
            nest_sensor_pb2.CarbonMonoxideTrait
        ): nest_sensor_pb2.CarbonMonoxideTrait(),
        _trait_key(nest_safety_pb2.SafetyAlarmSmokeTrait): (
            nest_safety_pb2.SafetyAlarmSmokeTrait(alarmState=alarm.ALARM_STATE_IDLE)
        ),
        _trait_key(nest_safety_pb2.SafetyAlarmCOTrait): (
            nest_safety_pb2.SafetyAlarmCOTrait(alarmState=alarm.ALARM_STATE_IDLE)
        ),
        _trait_key(weave_description_pb2.DeviceIdentityTrait): identity,
        _trait_key(weave_description_pb2.LabelSettingsTrait): label,
        _trait_key(weave_heartbeat_pb2.LivenessTrait): liveness,
        _trait_key(nest_located_pb2.DeviceLocatedSettingsTrait): located,
    }


def _camera_traits() -> dict[str, Any]:
    """Return the traits of a protobuf Nest camera."""
    identity = weave_description_pb2.DeviceIdentityTrait(
        serialNumber=CAMERA_SERIAL, softwareVersion="1.2.3"
    )
    label = weave_description_pb2.LabelSettingsTrait(label="Driveway")
    liveness = weave_heartbeat_pb2.LivenessTrait(
        status=weave_heartbeat_pb2.LivenessTrait.LIVENESS_DEVICE_STATUS_ONLINE
    )
    recording = nest_camera_pb2.RecordingToggleTrait(
        currentCameraState=nest_camera_pb2.CameraState.CAMERA_ON
    )
    located = nest_located_pb2.DeviceLocatedSettingsTrait()
    located.whereAnnotationRid.resourceId = _WHERE_FRONT_DOOR
    return {
        _trait_key(
            nest_camera_pb2.StreamingProtocolTrait
        ): nest_camera_pb2.StreamingProtocolTrait(),
        _trait_key(nest_camera_pb2.RecordingToggleTrait): recording,
        _trait_key(weave_description_pb2.DeviceIdentityTrait): identity,
        _trait_key(weave_description_pb2.LabelSettingsTrait): label,
        _trait_key(weave_heartbeat_pb2.LivenessTrait): liveness,
        _trait_key(nest_located_pb2.DeviceLocatedSettingsTrait): located,
    }


def _temp_sensor_traits() -> dict[str, Any]:
    """Return the traits of a protobuf Nest Temperature Sensor."""
    temperature = nest_sensor_pb2.TemperatureTrait()
    temperature.temperatureValue.temperature.value = 19.5
    identity = weave_description_pb2.DeviceIdentityTrait(
        serialNumber=TEMP_SENSOR_SERIAL, softwareVersion="1.0.0"
    )
    label = weave_description_pb2.LabelSettingsTrait(label="Study Sensor")
    liveness = weave_heartbeat_pb2.LivenessTrait(
        status=weave_heartbeat_pb2.LivenessTrait.LIVENESS_DEVICE_STATUS_ONLINE
    )
    battery = weave_power_pb2.BatteryPowerSourceTrait()
    battery.remaining.remainingPercent.value = 0.71
    located = nest_located_pb2.DeviceLocatedSettingsTrait()
    located.whereAnnotationRid.resourceId = _WHERE_HALLWAY
    return {
        _trait_key(nest_sensor_pb2.TemperatureTrait): temperature,
        _trait_key(weave_description_pb2.DeviceIdentityTrait): identity,
        _trait_key(weave_description_pb2.LabelSettingsTrait): label,
        _trait_key(weave_heartbeat_pb2.LivenessTrait): liveness,
        _trait_key(weave_power_pb2.BatteryPowerSourceTrait): battery,
        _trait_key(nest_located_pb2.DeviceLocatedSettingsTrait): located,
    }


def protobuf_updates() -> dict[str, dict[str, Any]]:
    """Return one observe stream payload covering every protobuf device type."""
    return {
        STRUCTURE_KEY: _structure_traits(),
        LOCK_KEY: _lock_traits(),
        THERMOSTAT_KEY: _thermostat_traits(),
        PROTECT_KEY: _protect_traits(),
        CAMERA_KEY: _camera_traits(),
        TEMP_SENSOR_KEY: _temp_sensor_traits(),
    }


__all__ = [
    "CAMERA_KEY",
    "CAMERA_SERIAL",
    "LOCK_KEY",
    "LOCK_SERIAL",
    "PROTECT_KEY",
    "PROTECT_SERIAL",
    "STRUCTURE_KEY",
    "TEMP_SENSOR_KEY",
    "TEMP_SENSOR_SERIAL",
    "THERMOSTAT_KEY",
    "THERMOSTAT_SERIAL",
    "dual_fuel_trait",
    "protobuf_updates",
    "weave_common_pb2",
]
