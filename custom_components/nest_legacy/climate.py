"""Climate platform for Nest thermostats and heat links."""

from __future__ import annotations

from typing import Any

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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import NestConfigEntry, NestCoordinator
from .entity import NestEntity
from .pynest.enums import TemperatureScale, ThermostatHvacMode, ThermostatHvacState
from .pynest.models import NestThermostat

PARALLEL_UPDATES = 0

_THERMOSTAT_MIN_TEMPERATURE = 9
_THERMOSTAT_MAX_TEMPERATURE = 32

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
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = _THERMOSTAT_MIN_TEMPERATURE
        self._attr_max_temp = _THERMOSTAT_MAX_TEMPERATURE

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

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature.

        The Nest API only accepts whole-number integers in Fahrenheit. Since
        the entity always reports in Celsius, 5/9 °C is the exact Celsius
        equivalent of 1 °F — keeping UI steps aligned with what the API accepts.
        """
        if self.device.temperature_scale == TemperatureScale.FAHRENHEIT:
            return 5 / 9
        return PRECISION_HALVES

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac operation."""
        return _HVAC_MODE_BIDICT.get(self.device.hvac_mode)

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

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return _HVAC_ACTION_MAP.get(self.device.hvac_state)

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self.device.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return None
        return self.device.target_temperature

    @property
    def target_temperature_high(self) -> float | None:
        """Return the highbound temperature."""
        return (
            self.device.target_temperature_high
            if self.hvac_mode == HVACMode.HEAT_COOL
            else None
        )

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lowbound temperature."""
        return (
            self.device.target_temperature_low
            if self.hvac_mode == HVACMode.HEAT_COOL
            else None
        )

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        return self.device.current_humidity

    @property
    def target_humidity(self) -> float | None:
        """Return the target humidity."""
        return self.device.target_humidity

    @property
    def min_humidity(self) -> float:
        """Return the minimum humidity."""
        return 15

    @property
    def max_humidity(self) -> float:
        """Return the maximum humidity."""
        return 90

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode, e.g., home, away, temp."""
        return PRESET_ECO if self.device.is_eco_mode else PRESET_NONE

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        if not self.device.has_fan:
            return None
        if self.device.fan_timer_timeout > int(dt_util.utcnow().timestamp()):
            return FAN_ON
        return FAN_AUTO

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes."""
        if self.device.has_fan:
            return [FAN_AUTO, FAN_ON]
        return None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        payload: dict[str, Any] = {}

        if self.device.is_eco_mode:
            # Signal client layer to exit eco before applying setpoint
            payload["exit_eco"] = True

        def _round_temp(temp: float) -> float:
            """Round temp to the nearest value the Nest API will accept.

            Fahrenheit thermostats only accept integer °F values. Since HA
            always passes °C internally, we convert to °F, round to the
            nearest integer, then convert back. This guards against callers
            (automations, voice assistants) that bypass the step constraint.
            """
            if self.device.temperature_scale == TemperatureScale.FAHRENHEIT:
                return (round(temp * 1.8 + 32.0) - 32.0) / 1.8
            return round(temp * 2.0) / 2.0

        if ATTR_TEMPERATURE in kwargs:
            payload["target_temperature"] = _round_temp(kwargs[ATTR_TEMPERATURE])
        if "target_temp_low" in kwargs:
            payload["target_temperature_low"] = _round_temp(kwargs["target_temp_low"])
        if "target_temp_high" in kwargs:
            payload["target_temperature_high"] = _round_temp(kwargs["target_temp_high"])

        if payload:
            await self._set_device_data(payload)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        nest_mode = _HVAC_MODE_BIDICT.inverse.get(hvac_mode)
        if nest_mode:
            payload = {"hvac_mode": nest_mode.value}
            await self._set_device_data(payload)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        nest_eco_mode = "manual-eco" if preset_mode == PRESET_ECO else "schedule"
        payload = {"eco": {"mode": nest_eco_mode}}
        await self._set_device_data(payload)

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        await self._set_device_data({"target_humidity": humidity})

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
