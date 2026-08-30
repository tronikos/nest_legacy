"""Number platform for Nest."""

from typing import override

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from .coordinator import NestConfigEntry, NestCoordinator
from .entity import NestEntity
from .pynest.enums import DualFuelBreakpointOverride
from .pynest.models import NestLock, NestThermostat

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_devices: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nest number platform from a config entry."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    entities.extend(
        NestLockAutoRelockDuration(coordinator, device)
        for device in coordinator.data.values()
        if isinstance(device, NestLock)
    )
    entities.extend(
        NestThermostatDualFuelBreakpoint(coordinator, device)
        for device in coordinator.data.values()
        if isinstance(device, NestThermostat) and device.has_dual_fuel
    )
    async_add_devices(entities)


class NestLockAutoRelockDuration(NestEntity[NestLock], NumberEntity):
    """Representation of a Nest Lock Auto-Relock Duration."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "auto_relock_duration"
    _attr_icon = "mdi:timer-lock"

    def __init__(self, coordinator: NestCoordinator, device: NestLock) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.serial_number}-auto_relock_duration"
        self._attr_native_min_value = 0
        self._attr_native_max_value = device.max_auto_relock_duration

    @override
    @property
    def native_value(self) -> float | None:
        """Return the entity state."""
        return self.device.auto_relock_duration

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        await self._set_device_data({"auto_relock_duration": int(value)})


class NestThermostatDualFuelBreakpoint(NestEntity[NestThermostat], NumberEntity):
    """Representation of a Nest Thermostat dual fuel breakpoint.

    The outdoor temperature below which the alternate heat source is used
    instead of the heat pump. Matches the slider in the Nest app, which runs
    from -25F to 50F in 1F steps.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:thermometer-off"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = -25
    _attr_native_max_value = 50
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_translation_key = "dual_fuel_breakpoint"

    def __init__(self, coordinator: NestCoordinator, device: NestThermostat) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.serial_number}-dual_fuel_breakpoint"

    @override
    @property
    def native_value(self) -> float | None:
        """Return the entity state."""
        if (breakpoint_celsius := self.device.dual_fuel_breakpoint) is None:
            return None
        return TemperatureConverter.convert(
            breakpoint_celsius, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
        )

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        # Picking a temperature in the Nest app cancels any always/never
        # alternate heat override, so clear it here as well.
        await self._set_device_data(
            {
                "dual_fuel_breakpoint": TemperatureConverter.convert(
                    value, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS
                ),
                "dual_fuel_breakpoint_override": DualFuelBreakpointOverride.NONE,
            }
        )
