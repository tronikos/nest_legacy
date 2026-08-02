"""Services for the Nest Legacy integration."""

from typing import Any

from google.protobuf.json_format import MessageToDict
import voluptuous as vol

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    ClimateEntityFeature,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    service,
)

from .const import DOMAIN
from .coordinator import NestCoordinator


def async_setup_services(hass: HomeAssistant) -> None:
    """Register services for Nest Legacy."""

    def _get_coordinator(call: ServiceCall) -> NestCoordinator:
        """Helper to get coordinator from config entry ID."""
        config_entry_id = call.data.get("config_entry_id")
        device_id = call.data.get("device_id")
        if not config_entry_id and device_id:
            device_registry = dr.async_get(hass)
            device_entry = device_registry.async_get(device_id)
            if device_entry and device_entry.config_entries:
                config_entry_id = next(iter(device_entry.config_entries), None)

        entry = None
        if not config_entry_id:
            entries = hass.config_entries.async_entries(DOMAIN)
            if not entries:
                raise ServiceValidationError("No Nest Legacy integration found")
            if len(entries) > 1:
                raise ServiceValidationError(
                    "Multiple Nest Legacy integrations found, please specify config_entry_id"
                )
            entry = entries[0]
        else:
            entry = hass.config_entries.async_get_entry(config_entry_id)

        if not entry:
            raise ServiceValidationError(f"Config entry '{config_entry_id}' not found")

        if not hasattr(entry, "runtime_data") or not entry.runtime_data:
            raise ServiceValidationError("Nest Legacy integration not ready")
        return entry.runtime_data

    def _get_serial_from_ha_device(ha_device_id: str) -> str:
        """Resolve HA device ID to Nest serial number."""
        device_registry = dr.async_get(hass)
        device_entry = device_registry.async_get(ha_device_id)
        if not device_entry:
            raise ServiceValidationError(
                f"Device ID '{ha_device_id}' not found in registry"
            )

        for domain, identifier in device_entry.identifiers:
            if domain == DOMAIN:
                return identifier

        raise ServiceValidationError(
            f"Device '{ha_device_id}' is not a Nest Legacy device"
        )

    async def async_list_guests(call: ServiceCall) -> ServiceResponse:
        """List guests from the structure."""
        coordinator = _get_coordinator(call)
        guests = coordinator.get_guests()
        return {"guests": guests}  # type: ignore[dict-item]

    async def async_get_user_schedule(call: ServiceCall) -> ServiceResponse:
        """Get a user's schedule."""
        coordinator = _get_coordinator(call)
        ha_device_id = call.data["device_id"]
        serial = _get_serial_from_ha_device(ha_device_id)
        device = coordinator.data[serial]

        user_id = call.data["user_id"]

        resp = await coordinator.async_send_client_command(
            "async_get_user_schedule", device, user_id
        )
        resp_dict = MessageToDict(resp)
        try:
            return resp_dict["sendCommandResponse"][0]["traitOperations"][0]["event"][
                "event"
            ]
        except KeyError, IndexError, TypeError:
            return resp_dict

    async def async_set_user_schedule(call: ServiceCall) -> None:
        """Set a user's schedule."""
        coordinator = _get_coordinator(call)
        ha_device_id = call.data["device_id"]
        serial = _get_serial_from_ha_device(ha_device_id)
        device = coordinator.data[serial]

        user_id = call.data["user_id"]

        daily_schedules = []
        if "days_of_week" in call.data:
            start_time = call.data.get("start_time")
            duration = call.data.get("duration")
            day_map = {
                "sunday": 1,
                "monday": 2,
                "tuesday": 4,
                "wednesday": 8,
                "thursday": 16,
                "friday": 32,
                "saturday": 64,
            }
            ds: dict[str, Any] = {
                "days_of_week": [day_map[d] for d in call.data["days_of_week"]]
            }
            if start_time:
                ds["start_time"] = {
                    "hour": start_time.hour,
                    "minute": start_time.minute,
                    "second": start_time.second,
                }
            if duration:
                ds["duration_seconds"] = int(duration.total_seconds())
            daily_schedules.append(ds)

        timebox_schedules = []
        if "start_timebox" in call.data or "end_timebox" in call.data:
            ts: dict[str, Any] = {}
            start_timebox = call.data.get("start_timebox")
            end_timebox = call.data.get("end_timebox")
            if start_timebox:
                ts["start_time"] = int(start_timebox.timestamp())
            if end_timebox:
                ts["end_time"] = int(end_timebox.timestamp())
            timebox_schedules.append(ts)

        await coordinator.async_send_client_command(
            "async_set_user_schedule",
            device,
            user_id,
            daily_schedules or None,
            timebox_schedules or None,
        )

    async def async_delete_user_schedule(call: ServiceCall) -> None:
        """Delete a user's schedule."""
        coordinator = _get_coordinator(call)
        ha_device_id = call.data["device_id"]
        serial = _get_serial_from_ha_device(ha_device_id)
        device = coordinator.data[serial]

        user_id = call.data["user_id"]

        await coordinator.async_send_client_command(
            "async_delete_user_schedule", device, user_id
        )

    # Register services
    hass.services.async_register(
        DOMAIN,
        "list_guests",
        async_list_guests,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "get_user_schedule",
        async_get_user_schedule,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): cv.string,
                vol.Required("device_id"): cv.string,
                vol.Required("user_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "set_user_schedule",
        async_set_user_schedule,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): cv.string,
                vol.Required("device_id"): cv.string,
                vol.Required("user_id"): cv.string,
                vol.Optional("days_of_week"): vol.All(
                    cv.ensure_list,
                    [
                        vol.In(
                            [
                                "monday",
                                "tuesday",
                                "wednesday",
                                "thursday",
                                "friday",
                                "saturday",
                                "sunday",
                            ]
                        )
                    ],
                ),
                vol.Optional("start_time"): cv.time,
                vol.Optional("duration"): cv.time_period,
                vol.Optional("start_timebox"): cv.datetime,
                vol.Optional("end_timebox"): cv.datetime,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "delete_user_schedule",
        async_delete_user_schedule,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): cv.string,
                vol.Required("device_id"): cv.string,
                vol.Required("user_id"): cv.string,
            }
        ),
    )

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_fan_timer",
        entity_domain=CLIMATE_DOMAIN,
        schema={
            vol.Required("duration"): cv.time_period,
        },
        func="async_set_fan_timer",
        required_features=[ClimateEntityFeature.FAN_MODE],
    )
