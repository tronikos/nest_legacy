"""Climate platform for Nest thermostats and heat links."""

from datetime import timedelta
from typing import Any, override

from bidict import bidict

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_ON,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import NestConfigEntry, NestCoordinator
from .entity import NestEntity
from .pynest.enums import TemperatureScale, ThermostatHvacMode, ThermostatHvacState
from .pynest.models import NestThermostat

PARALLEL_UPDATES = 0

_THERMOSTAT_MIN_TEMPERATURE_C = 9
_THERMOSTAT_MAX_TEMPERATURE_C = 32

_HVAC_MODE_BIDICT: bidict[ThermostatHvacMode, HVACMode] = bidict(
    {
        ThermostatHvacMode.OFF: HVACMode.OFF,
        ThermostatHvacMode.COOL: HVACMode.COOL,
        ThermostatHvacMode.HEAT: HVACMode.HEAT,
        ThermostatHvacMode.RANGE: HVACMode.HEAT_COOL,
    }
)

_HVAC_ACTION_MAP = {
    ThermostatHvacState.OFF: HVACAction.IDLE,
    ThermostatHvacState.HEATING: HVACAction.HEATING,
    ThermostatHvacState.COOLING: HVACAction.COOLING,
    ThermostatHvacState.FAN: HVACAction.FAN,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_devices: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nest climate platform from a config entry."""
    coordinator = entry.runtime_data
    entities = [
        NestClimate(coordinator, device)
        for device in coordinator.data.values()
        if isinstance(device, NestThermostat)
    ]
    async_add_devices(entities)


class NestClimate(NestEntity[NestThermostat], ClimateEntity):
    """Representation of a Nest thermostat."""

    _attr_name = None  # Main feature of the device

    def __init__(
        self,
        coordinator: NestCoordinator,
        device: NestThermostat,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, device)

        is_fahrenheit = device.temperature_scale == TemperatureScale.FAHRENHEIT
        self._attr_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT if is_fahrenheit else UnitOfTemperature.CELSIUS
        )

        if is_fahrenheit:
            # Report native bounds in whole °F, matching the device's native precision.
            self._attr_min_temp = round(_THERMOSTAT_MIN_TEMPERATURE_C * 9 / 5 + 32)
            self._attr_max_temp = round(_THERMOSTAT_MAX_TEMPERATURE_C * 9 / 5 + 32)
        else:
            self._attr_min_temp = _THERMOSTAT_MIN_TEMPERATURE_C
            self._attr_max_temp = _THERMOSTAT_MAX_TEMPERATURE_C

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.PRESET_MODE
        )
        if device.has_dehumidifier or device.has_humidifier:
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        if device.has_fan:
            features |= ClimateEntityFeature.FAN_MODE

        self._attr_supported_features = features
        self._attr_preset_modes = [PRESET_NONE, PRESET_ECO]

    def _c_to_native(self, value: float | None) -> float | None:
        """Convert a Celsius value from the device into the entity's native unit.

        Fahrenheit thermostats only support whole-degree precision, so this
        rounds to the nearest integer °F. This also absorbs float32 noise
        from the Nest API (e.g. 26.1111107 instead of exactly 26.111111...),
        ensuring the frontend always receives a clean integer. Celsius values
        pass through unchanged, since the API already reports those at
        usable precision.
        """
        if value is None:
            return None
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return round(value * 9 / 5 + 32)
        return value

    def _native_to_c(self, value: float) -> float:
        """Convert a value in the entity's native unit back to Celsius for the API.

        Guards against callers sending unrounded numbers (automations, voice
        assistants) by enforcing native stepping constraints. Rounding is
        baked directly into this conversion so it can't be skipped by a
        future caller.
        """
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return (round(value) - 32) * 5 / 9
        return round(value * 2.0) / 2.0

    @override
    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return 1.0
        return PRECISION_HALVES

    @override
    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac operation."""
        return _HVAC_MODE_BIDICT.get(self.device.hvac_mode)

    @override
    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac operation modes."""
        modes = [HVACMode.OFF]
        if self.device.can_heat:
            modes.append(HVACMode.HEAT)
        if self.device.can_cool:
            modes.append(HVACMode.COOL)
        if self.device.can_heat and self.device.can_cool:
            modes.append(HVACMode.HEAT_COOL)
        return modes

    @override
    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return _HVAC_ACTION_MAP.get(self.device.hvac_state)

    @override
    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._c_to_native(self.device.current_temperature)

    @override
    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return None
        return self._c_to_native(self.device.target_temperature)

    @override
    @property
    def target_temperature_high(self) -> float | None:
        """Return the highbound temperature."""
        return self._c_to_native(
            self.device.target_temperature_high
            if self.hvac_mode == HVACMode.HEAT_COOL
            else None
        )

    @override
    @property
    def target_temperature_low(self) -> float | None:
        """Return the lowbound temperature."""
        return self._c_to_native(
            self.device.target_temperature_low
            if self.hvac_mode == HVACMode.HEAT_COOL
            else None
        )

    @override
    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        return self.device.current_humidity

    @override
    @property
    def target_humidity(self) -> float | None:
        """Return the target humidity."""
        return self.device.target_humidity

    @override
    @property
    def min_humidity(self) -> float:
        """Return the minimum humidity."""
        return 15

    @override
    @property
    def max_humidity(self) -> float:
        """Return the maximum humidity."""
        return 90

    @override
    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode, e.g., home, away, temp."""
        return PRESET_ECO if self.device.is_eco_mode else PRESET_NONE

    @override
    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        if not self.device.has_fan:
            return None
        if self.device.fan_timer_timeout > int(dt_util.utcnow().timestamp()):
            return FAN_ON
        return FAN_AUTO

    @override
    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes."""
        if self.device.has_fan:
            return [FAN_AUTO, FAN_ON]
        return None

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        payload: dict[str, Any] = {}

        if self.device.is_eco_mode:
            # Signal client layer to exit eco before applying setpoint
            payload["exit_eco"] = True

        if ATTR_TEMPERATURE in kwargs:
            payload["target_temperature"] = self._native_to_c(kwargs[ATTR_TEMPERATURE])
        if "target_temp_low" in kwargs:
            payload["target_temperature_low"] = self._native_to_c(
                kwargs["target_temp_low"]
            )
        if "target_temp_high" in kwargs:
            payload["target_temperature_high"] = self._native_to_c(
                kwargs["target_temp_high"]
            )

        if payload:
            await self._set_device_data(payload)

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        nest_mode = _HVAC_MODE_BIDICT.inverse.get(hvac_mode)
        if nest_mode:
            payload = {"hvac_mode": nest_mode.value}
            await self._set_device_data(payload)

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        nest_eco_mode = "manual-eco" if preset_mode == PRESET_ECO else "schedule"
        payload = {"eco": {"mode": nest_eco_mode}}
        await self._set_device_data(payload)

    @override
    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        await self._set_device_data({"target_humidity": humidity})

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        if not self.device.has_fan:
            return
        fan_on = fan_mode == FAN_ON
        timeout = (
            int(dt_util.utcnow().timestamp()) + self.device.fan_duration
            if fan_on
            else 0
        )
        payload: dict[str, Any] = {"fan_timer_timeout": timeout}
        await self._set_device_data(payload)

    async def async_set_fan_timer(self, duration: timedelta) -> None:
        """Set a short term fan timer."""
        if not self.device.has_fan:
            raise HomeAssistantError(f"Entity {self.entity_id} does not support fan")

        seconds = int(duration.total_seconds())
        if seconds <= 0:
            raise ValueError(
                f"Duration {seconds} for {self.entity_id} must be greater than 0 seconds"
            )

        timeout = int(dt_util.utcnow().timestamp()) + seconds
        payload: dict[str, Any] = {"fan_timer_timeout": timeout}
        await self._set_device_data(payload)
