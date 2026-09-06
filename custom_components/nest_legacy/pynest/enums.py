"""Enums for Nest."""

from enum import StrEnum, unique
import logging
from typing import Any, override

_LOGGER = logging.getLogger(__name__)


@unique
class BucketType(StrEnum):
    """Bucket types."""

    BUCKETS = "buckets"
    DELAYED_TOPAZ = "delayed_topaz"
    DEMAND_RESPONSE = "demand_response"
    DEVICE = "device"
    DEVICE_ALERT_DIALOG = "device_alert_dialog"
    GEOFENCE_INFO = "geofence_info"
    KRYPTONITE = "kryptonite"  # Temperature Sensors
    LINK = "link"
    MESSAGE = "message"
    MESSAGE_CENTER = "message_center"
    METADATA = "metadata"
    OCCUPANCY = "occupancy"
    QUARTZ = "quartz"  # Cameras
    RCS_SETTINGS = "rcs_settings"
    SAFETY = "safety"
    SAFETY_SUMMARY = "safety_summary"
    SCHEDULE = "schedule"
    SHARED = "shared"
    STRUCTURE = "structure"  # General
    STRUCTURE_HISTORY = "structure_history"
    STRUCTURE_METADATA = "structure_metadata"
    TOPAZ = "topaz"  # Nest Protect
    TOPAZ_RESOURCE = "topaz_resource"
    TRACK = "track"
    TRIP = "trip"
    TUNEUPS = "tuneups"
    USER = "user"
    USER_ALERT_DIALOG = "user_alert_dialog"
    USER_SETTINGS = "user_settings"
    WIDGET_TRACK = "widget_track"
    WHERE = "where"  # Areas

    UNKNOWN = "unknown"

    @override
    @classmethod
    def _missing_(cls: type[BucketType], value: Any) -> BucketType:
        _LOGGER.warning("Unsupported value %s has been returned for %s", value, cls)
        return cls.UNKNOWN


@unique
class Environment(StrEnum):
    """Environment types."""

    FIELDTEST = "fieldtest"
    PRODUCTION = "production"


@unique
class TemperatureScale(StrEnum):
    """Temperature scales."""

    CELSIUS = "C"
    FAHRENHEIT = "F"


@unique
class ThermostatHvacState(StrEnum):
    """Nest Thermostat HVAC states."""

    OFF = "off"
    HEATING = "heating"
    COOLING = "cooling"
    FAN = "fan"


@unique
class ThermostatHvacStage(StrEnum):
    """Nest Thermostat HVAC stages."""

    OFF = "off"
    COOL_STAGE_1 = "cool_stage_1"
    COOL_STAGE_2 = "cool_stage_2"
    COOL_STAGE_3 = "cool_stage_3"
    HEAT_STAGE_1 = "heat_stage_1"
    HEAT_STAGE_2 = "heat_stage_2"
    HEAT_STAGE_3 = "heat_stage_3"
    ALTERNATE_HEAT_STAGE_1 = "alternate_heat_stage_1"
    ALTERNATE_HEAT_STAGE_2 = "alternate_heat_stage_2"
    AUXILIARY_HEAT = "auxiliary_heat"
    EMERGENCY_HEAT = "emergency_heat"


@unique
class ThermostatHvacMode(StrEnum):
    """Nest Thermostat HVAC modes."""

    OFF = "off"
    COOL = "cool"
    HEAT = "heat"
    RANGE = "range"  # heat-cool


@unique
class DualFuelBreakpointOverride(StrEnum):
    """Nest Thermostat dual fuel breakpoint overrides."""

    NONE = "none"
    ALWAYS_ALTERNATE_HEAT = "always_alternate_heat"
    NEVER_ALTERNATE_HEAT = "never_alternate_heat"


@unique
class HotWaterMode(StrEnum):
    """Nest Heat Link hot water modes."""

    OFF = "off"
    SCHEDULE = "schedule"


@unique
class LockBoltState(StrEnum):
    """Nest x Yale Lock bolt states."""

    LOCKED = "locked"
    UNLOCKED = "unlocked"
    LOCKING = "locking"
    UNLOCKING = "unlocking"
    JAMMED = "jammed"
    UNKNOWN = "unknown"


@unique
class LockBoltActor(StrEnum):
    """Actor that last changed the Nest x Yale Lock state."""

    PHYSICAL = "physical"
    KEYPAD = "keypad"
    REMOTE = "remote"
    IMPLICIT = "implicit"
    VOICE = "voice"
    UNKNOWN = "unknown"


@unique
class StructureMode(StrEnum):
    """Nest Structure modes."""

    HOME = "home"
    AWAY = "away"
    SLEEP = "sleep"
    VACATION = "vacation"
