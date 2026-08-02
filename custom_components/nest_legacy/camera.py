"""Camera platform for Nest."""

from typing import override

from aiohttp import ClientError

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestConfigEntry, NestCoordinator
from .entity import NestEntity
from .pynest.exceptions import PynestException
from .pynest.models import NestCamera as NestCameraModel

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_devices: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nest camera platform from a config entry."""
    coordinator = entry.runtime_data
    entities = [
        NestCameraEntity(coordinator, device)
        for device in coordinator.data.values()
        if isinstance(device, NestCameraModel)
    ]
    async_add_devices(entities)


class NestCameraEntity(NestEntity[NestCameraModel], Camera):
    """Representation of a Nest camera."""

    _attr_supported_features = CameraEntityFeature.ON_OFF
    _attr_name = None  # The camera is the main feature of the device

    def __init__(self, coordinator: NestCoordinator, device: NestCameraModel) -> None:
        """Initialize the camera."""
        super().__init__(coordinator, device)
        Camera.__init__(self)

    @override
    @property
    def is_streaming(self) -> bool:
        """Return true if the camera is streaming."""
        return self.device.is_streaming

    @override
    @property
    def is_on(self) -> bool:
        """Return true if on."""
        return self.device.streaming_enabled

    @override
    async def async_turn_on(self) -> None:
        """Turn on camera."""
        await self._set_device_data({"streaming_enabled": True})

    @override
    async def async_turn_off(self) -> None:
        """Turn off camera."""
        await self._set_device_data({"streaming_enabled": False})

    @override
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        if not self.is_on:
            return None
        try:
            return await self.coordinator.client.async_get_camera_snapshot(self.device)
        except (ClientError, TimeoutError, PynestException) as err:
            raise HomeAssistantError(
                f"Error fetching snapshot for camera {self.device.location} "
                f"{self.device.name}: {err}"
            ) from err
