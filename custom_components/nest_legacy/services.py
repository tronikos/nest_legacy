"""Services for the Nest Legacy integration."""

from typing import Any

from google.protobuf.json_format import MessageToDict
import probatio

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
from .pynest.models import NestDevice


def async_setup_services(hass: HomeAssistant) -> None:
    """Register services for Nest Legacy."""

    def _get_coordinator(call: ServiceCall) -> NestCoordinator:
        """Helper to get coordinator from config entry ID."""
        config_entry_id = call.data.get("config_entry_id")
        device_id = call.data.get("device_id")
        if not config_entry_id and device_id:
            _device_entry, config_entry = (
                dr.async_get_device_and_config_entry_for_domain(
                    hass, device_id, domain=DOMAIN
                )
            )
            if config_entry is not None:
                config_entry_id = config_entry.entry_id

        entry = None
        if not config_entry_id:
            entries = hass.config_entries.async_entries(DOMAIN)
            if not entries:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="no_config_entry"
                )
            if len(entries) > 1:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="multiple_config_entries",
                )
            entry = entries[0]
        else:
            entry = hass.config_entries.async_get_entry(config_entry_id)

        if not entry:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_found",
                translation_placeholders={"config_entry_id": str(config_entry_id)},
            )

        if not hasattr(entry, "runtime_data") or not entry.runtime_data:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="integration_not_ready"
            )
        return entry.runtime_data

    def _get_device(coordinator: NestCoordinator, ha_device_id: str) -> NestDevice:
        """Resolve HA device ID to a NestDevice in coordinator data."""
        device_entry = dr.async_get(hass).async_get(ha_device_id)
        if not device_entry:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": ha_device_id},
            )

        serial = next(
            (id_val for domain, id_val in device_entry.identifiers if domain == DOMAIN),
            None,
        )
        if serial is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_nest_legacy",
                translation_placeholders={"device_id": ha_device_id},
            )

        device = coordinator.data.get(serial)
        if device is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": ha_device_id},
            )
        return device

    async def async_list_guests(call: ServiceCall) -> ServiceResponse:
        """List guests from the structure."""
        coordinator = _get_coordinator(call)
        guests = coordinator.get_guests()
        return {"guests": guests}  # type: ignore[dict-item]

    async def async_get_user_schedule(call: ServiceCall) -> ServiceResponse:
        """Get a user's schedule."""
        coordinator = _get_coordinator(call)
        device = _get_device(coordinator, call.data["device_id"])

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
        device = _get_device(coordinator, call.data["device_id"])

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
        device = _get_device(coordinator, call.data["device_id"])

        user_id = call.data["user_id"]

        await coordinator.async_send_client_command(
            "async_delete_user_schedule", device, user_id
        )

    # Register services
    hass.services.async_register(
        DOMAIN,
        "list_guests",
        async_list_guests,
        schema=probatio.Schema(
            {
                probatio.Optional("config_entry_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "get_user_schedule",
        async_get_user_schedule,
        schema=probatio.Schema(
            {
                probatio.Optional("config_entry_id"): cv.string,
                probatio.Required("device_id"): cv.string,
                probatio.Required("user_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "set_user_schedule",
        async_set_user_schedule,
        schema=probatio.Schema(
            {
                probatio.Optional("config_entry_id"): cv.string,
                probatio.Required("device_id"): cv.string,
                probatio.Required("user_id"): cv.string,
                probatio.Optional("days_of_week"): probatio.All(
                    cv.ensure_list,
                    [
                        probatio.In(
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
                probatio.Optional("start_time"): cv.time,
                probatio.Optional("duration"): cv.time_period,
                probatio.Optional("start_timebox"): cv.datetime,
                probatio.Optional("end_timebox"): cv.datetime,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "delete_user_schedule",
        async_delete_user_schedule,
        schema=probatio.Schema(
            {
                probatio.Optional("config_entry_id"): cv.string,
                probatio.Required("device_id"): cv.string,
                probatio.Required("user_id"): cv.string,
            }
        ),
    )

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_fan_timer",
        entity_domain=CLIMATE_DOMAIN,
        schema={
            probatio.Required("duration"): cv.time_period,
        },
        func="async_set_fan_timer",
        required_features=[ClimateEntityFeature.FAN_MODE],
    )
