"""Tests for the command paths of the pynest client."""

from http import HTTPStatus
import time
from typing import Any
from unittest.mock import AsyncMock, patch

from custom_components.nest_legacy.pynest import client as client_module
from custom_components.nest_legacy.pynest.client import NestClient
from custom_components.nest_legacy.pynest.enums import ThermostatHvacMode
from custom_components.nest_legacy.pynest.exceptions import (
    AuthenticationFailedException,
    NonRetryablePynestException,
    NotAuthenticatedException,
    PynestException,
)
from custom_components.nest_legacy.pynest.models import (
    NestCamera,
    NestHeatLink,
    NestLock,
    NestProtect,
    NestTempSensor,
    NestThermostat,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    history_pb2 as nest_history_pb2,
    hvac_pb2 as nest_hvac_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nestlabs.gateway import v1_pb2
from google.protobuf import any_pb2
from google.protobuf.message import Message
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import TEMP_SENSOR_KEY, THERMOSTAT_KEY
from .test_client import SESSION_RESPONSE, SESSION_URL

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

PUT_URL = "https://transport.example.com/v6/put"
SEND_COMMAND_URL = (
    "https://grpc-web.production.nest.com/nestlabs.gateway.v1.ResourceApi/SendCommand"
)
BATCH_UPDATE_URL = (
    "https://grpc-web.production.nest.com"
    "/nestlabs.gateway.v1.TraitBatchApi/BatchUpdateState"
)

_RCS = nest_hvac_pb2.RemoteComfortSensingSettingsTrait
_SET_POINT_TYPE = nest_hvac_pb2.SetPointScheduleSettingsTrait.SetPointType


@pytest.fixture(autouse=True)
def no_retry_delay() -> Any:
    """Run the client's retry loops without waiting."""
    with patch("custom_components.nest_legacy.pynest.client.asyncio.sleep"):
        yield


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> NestClient:
    """Return an authenticated client."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    client = NestClient(async_get_clientsession(hass))
    await client._async_get_session("test-token")
    return client


def _thermostat(*, is_eco_mode: bool = False) -> NestThermostat:
    """Return a protobuf thermostat heating to 20 C."""
    return NestThermostat(
        object_key=THERMOSTAT_KEY,
        serial_number="18B430DDDDDD0002",
        name="Thermostat",
        hvac_mode=ThermostatHvacMode.HEAT,
        target_temperature=20.0,
        is_eco_mode=is_eco_mode,
        is_protobuf=True,
    )


def _temp_sensor() -> NestTempSensor:
    """Return a protobuf temperature sensor associated with the thermostat."""
    return NestTempSensor(
        object_key=TEMP_SENSOR_KEY,
        serial_number="18B430DDDDDD0005",
        name="Sensor",
        associated_thermostat_object_key=THERMOSTAT_KEY,
        is_protobuf=True,
    )


def _heat_link() -> NestHeatLink:
    """Return the Heat Link driven by the thermostat."""
    return NestHeatLink(
        object_key="heatlink.18B430DDDDDD0006",
        serial_number="18B430DDDDDD0006",
        name="Heat Link",
        associated_thermostat_object_key=THERMOSTAT_KEY,
        is_protobuf=True,
    )


def _protect() -> NestProtect:
    """Return a REST Protect."""
    return NestProtect(
        object_key="topaz.09AA00AA00AA0AA1",
        serial_number="09AA00AA00AA0AA1",
        name="Protect",
    )


def _protobuf_lock() -> NestLock:
    """Return a protobuf lock."""
    return NestLock(
        object_key="DEVICE_0000000000000002",
        serial_number="18B430DDDDDD0001",
        name="Lock",
        is_protobuf=True,
    )


def _protobuf_camera() -> NestCamera:
    """Return a protobuf camera."""
    return NestCamera(
        object_key="DEVICE_0000000000000003",
        serial_number="18B430DDDDDD0002",
        name="Camera",
        location="Living Room",
        is_protobuf=True,
        nexus_api_http_server_url="https://camera.example.com",
    )


async def test_camera_snapshot_uses_the_url_the_upload_publishes(
    client: NestClient,
) -> None:
    """The snapshot reads the URL the camera publishes for this upload.

    upload_live_image is asynchronous: the device keeps the previous image URL
    until the trait stream carries the new one, so the snapshot has to wait for
    that update instead of reading the URL it already had.
    """
    camera = _protobuf_camera()

    async def send(device, command):
        client._publish_live_image_url(device.object_key, "https://cam/new")

    fetch = AsyncMock(return_value=b"frame")
    with (
        patch.object(client, "_async_send_command", new=AsyncMock(side_effect=send)),
        patch.object(client, "_async_fetch_camera_snapshot", new=fetch),
    ):
        assert await client.async_get_camera_snapshot(camera) == b"frame"

    fetch.assert_awaited_once_with("https://cam/new")


async def test_camera_snapshot_uses_the_last_known_url_when_nothing_arrives(
    client: NestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A camera that does not publish a URL in time is read from the last one."""
    monkeypatch.setattr(client_module, "_SNAPSHOT_URL_TIMEOUT", 0.05)
    camera = _protobuf_camera()
    client._live_image_urls[camera.object_key] = "https://cam/previous"

    fetch = AsyncMock(return_value=b"frame")
    with (
        patch.object(client, "_async_send_command", new=AsyncMock(return_value=None)),
        patch.object(client, "_async_fetch_camera_snapshot", new=fetch),
    ):
        assert await client.async_get_camera_snapshot(camera) == b"frame"

    fetch.assert_awaited_once_with("https://cam/previous")


async def test_camera_snapshot_retries_with_a_new_upload_on_400(
    client: NestClient,
) -> None:
    """A failed download asks the camera for a fresh upload and tries again."""
    camera = _protobuf_camera()
    published = []

    async def send(device, command):
        published.append(f"https://cam/{len(published) + 1}")
        client._publish_live_image_url(device.object_key, published[-1])

    fetch = AsyncMock(side_effect=[None, b"frame"])
    with (
        patch.object(client, "_async_send_command", new=AsyncMock(side_effect=send)),
        patch.object(client, "_async_fetch_camera_snapshot", new=fetch),
        patch.object(client_module.asyncio, "sleep", new=AsyncMock(return_value=None)),
    ):
        assert await client.async_get_camera_snapshot(camera) == b"frame"

    assert [call.args[0] for call in fetch.await_args_list] == [
        "https://cam/1",
        "https://cam/2",
    ]


async def test_camera_snapshot_gives_up_after_the_last_attempt(
    client: NestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Three failed downloads end the request with one warning."""

    async def send(device, command):
        client._publish_live_image_url(device.object_key, "https://cam/new")

    fetch = AsyncMock(return_value=None)
    with (
        patch.object(client, "_async_send_command", new=AsyncMock(side_effect=send)),
        patch.object(client, "_async_fetch_camera_snapshot", new=fetch),
        patch.object(client_module.asyncio, "sleep", new=AsyncMock(return_value=None)),
    ):
        assert await client.async_get_camera_snapshot(_protobuf_camera()) is None

    assert fetch.await_count == client_module._SNAPSHOT_ATTEMPTS
    assert caplog.text.count("giving up for this request") == 1


async def test_camera_snapshot_downloads_the_image(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The URL is downloaded with the camera headers."""
    camera = _protobuf_camera()

    async def send(device, command):
        client._publish_live_image_url(
            device.object_key, "https://camera.example.com/snapshot"
        )

    aioclient_mock.get("https://camera.example.com/snapshot", content=b"frame")
    aioclient_mock.mock_calls.clear()

    with patch.object(client, "_async_send_command", new=AsyncMock(side_effect=send)):
        assert await client.async_get_camera_snapshot(camera) == b"frame"

    gets = [call for call in aioclient_mock.mock_calls if call[0] == "GET"]
    assert len(gets) == 1


def _mock_trait_updates(aioclient_mock: AiohttpClientMocker) -> None:
    """Accept every trait update."""
    aioclient_mock.post(
        BATCH_UPDATE_URL,
        content=v1_pb2.BatchUpdateStateResponse().SerializeToString(),
    )


def _trait_updates(
    aioclient_mock: AiohttpClientMocker,
) -> list[v1_pb2.TraitUpdateStateRequest]:
    """Return every trait update sent, in order."""
    return [
        update
        for _, url, data, _ in aioclient_mock.mock_calls
        if str(url) == BATCH_UPDATE_URL
        for update in v1_pb2.BatchUpdateStateRequest.FromString(
            data
        ).batchUpdateStateRequest
    ]


def _unpack[T: Message](state: any_pb2.Any, trait_class: type[T]) -> T:
    """Return the trait packed into a trait update."""
    trait = trait_class()
    assert state.Unpack(trait)
    return trait


async def test_thermostat_setpoint_and_lock(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each thermostat setting is written to its own trait on the thermostat."""
    _mock_trait_updates(aioclient_mock)

    await client.async_set_device_data(
        _thermostat(), {"target_temperature": 21.5, "temperature_lock": True}
    )

    target_update, lock_update = _trait_updates(aioclient_mock)
    assert target_update.traitRequest.resourceId == THERMOSTAT_KEY
    assert target_update.traitRequest.traitLabel == "target_temperature_settings"
    target = _unpack(target_update.state, nest_hvac_pb2.TargetTemperatureSettingsTrait)
    assert target.targetTemperature.setpointType == _SET_POINT_TYPE.SET_POINT_TYPE_HEAT
    assert target.targetTemperature.heatingTarget.value == 21.5
    assert lock_update.traitRequest.resourceId == THERMOSTAT_KEY
    assert lock_update.traitRequest.traitLabel == "temperature_lock_settings"
    assert _unpack(
        lock_update.state, nest_hvac_pb2.TemperatureLockSettingsTrait
    ).enabled


async def test_hvac_mode_sets_the_setpoint_type(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The HVAC mode is the setpoint type of the target temperature."""
    _mock_trait_updates(aioclient_mock)

    await client.async_set_device_data(_thermostat(), {"hvac_mode": "range"})

    (update,) = _trait_updates(aioclient_mock)
    target = _unpack(update.state, nest_hvac_pb2.TargetTemperatureSettingsTrait)
    assert target.enabled.value
    assert target.targetTemperature.setpointType == _SET_POINT_TYPE.SET_POINT_TYPE_RANGE


async def test_exit_eco_is_sent_before_the_setpoint(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Eco is left before the new setpoint is applied; see issue #33."""
    _mock_trait_updates(aioclient_mock)

    await client.async_set_device_data(
        _thermostat(is_eco_mode=True), {"exit_eco": True, "target_temperature": 21.0}
    )

    eco_update, target_update = _trait_updates(aioclient_mock)
    assert eco_update.traitRequest.traitLabel == "eco_mode_state"
    assert (
        _unpack(eco_update.state, nest_hvac_pb2.EcoModeStateTrait).ecoMode
        == nest_hvac_pb2.EcoModeStateTrait.EcoMode.ECO_MODE_INACTIVE
    )
    assert target_update.traitRequest.traitLabel == "target_temperature_settings"
    target = _unpack(target_update.state, nest_hvac_pb2.TargetTemperatureSettingsTrait)
    assert target.targetTemperature.heatingTarget.value == 21.0


async def test_activating_a_sensor_keeps_the_other_rcs_settings(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The sensor is selected on the thermostat without losing its other settings."""
    _mock_trait_updates(aioclient_mock)
    rcs = _RCS(rcsControlMode=_RCS.RcsControlMode.RCS_CONTROL_MODE_SCHEDULE)
    rcs.associatedRcsSensors.add().deviceId.resourceId = TEMP_SENSOR_KEY
    rcs.activeRcsSelection.rcsSourceType = _RCS.RcsSourceType.RCS_SOURCE_TYPE_BACKPLATE

    await client.async_set_device_data(
        _temp_sensor(),
        {"is_active_sensor": True},
        current_traits={_RCS.DESCRIPTOR.full_name: rcs},
    )

    (update,) = _trait_updates(aioclient_mock)
    assert update.traitRequest.resourceId == THERMOSTAT_KEY
    assert update.traitRequest.traitLabel == "remote_comfort_sensing_settings"
    sent = _unpack(update.state, _RCS)
    assert (
        sent.activeRcsSelection.rcsSourceType
        == _RCS.RcsSourceType.RCS_SOURCE_TYPE_SINGLE_SENSOR
    )
    assert sent.activeRcsSelection.activeRcsSensor.resourceId == TEMP_SENSOR_KEY
    assert sent.rcsControlMode == _RCS.RcsControlMode.RCS_CONTROL_MODE_SCHEDULE
    assert [s.deviceId.resourceId for s in sent.associatedRcsSensors] == [
        TEMP_SENSOR_KEY
    ]


async def test_deactivating_an_inactive_sensor_sends_nothing(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Turning off a sensor the thermostat isn't using changes nothing."""
    _mock_trait_updates(aioclient_mock)
    rcs = _RCS()
    rcs.activeRcsSelection.rcsSourceType = _RCS.RcsSourceType.RCS_SOURCE_TYPE_BACKPLATE

    await client.async_set_device_data(
        _temp_sensor(),
        {"is_active_sensor": False},
        current_traits={_RCS.DESCRIPTOR.full_name: rcs},
    )

    assert not _trait_updates(aioclient_mock)


async def test_heat_link_boost(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A boost sets the hot water settings on the thermostat it hangs off."""
    _mock_trait_updates(aioclient_mock)

    await client.async_set_device_data(
        _heat_link(),
        {
            "hot_water_mode": "schedule",
            "hot_water_boost": True,
            "hot_water_boost_duration": 3600,
        },
    )

    (update,) = _trait_updates(aioclient_mock)
    assert update.traitRequest.resourceId == THERMOSTAT_KEY
    assert update.traitRequest.traitLabel == "hot_water_settings"
    settings = _unpack(update.state, nest_hvac_pb2.HotWaterSettingsTrait)
    assert (
        settings.mode
        == nest_hvac_pb2.HotWaterSettingsTrait.HotWaterMode.HOT_WATER_MODE_SCHEDULE
    )
    assert settings.boostTimerEnd.seconds == pytest.approx(time.time() + 3600, abs=5)


async def test_generic_device_write(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Protect setting is merged into its own bucket."""
    aioclient_mock.post(PUT_URL, json={})

    await client.async_set_device_data(_protect(), {"night_light_enable": True})

    objects = aioclient_mock.mock_calls[-1][2]["objects"]
    assert objects == [
        {
            "object_key": "topaz.09AA00AA00AA0AA1",
            "op": "MERGE",
            "value": {"night_light_enable": True},
        }
    ]


@pytest.mark.parametrize("status", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN])
async def test_write_reports_an_expired_session(
    client: NestClient, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """A rejected write asks the caller to reauthenticate and retry."""
    aioclient_mock.post(PUT_URL, status=status, text="nope")

    with pytest.raises(NotAuthenticatedException):
        await client.async_set_device_data(_protect(), {"night_light_enable": True})

    assert len(aioclient_mock.mock_calls) == 2


async def test_write_does_not_retry_a_bad_request(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected payload is not worth sending again."""
    aioclient_mock.post(PUT_URL, status=HTTPStatus.BAD_REQUEST, text="nope")

    with pytest.raises(PynestException, match="BAD_REQUEST"):
        await client.async_set_device_data(_protect(), {"night_light_enable": True})

    assert len(aioclient_mock.mock_calls) == 2


async def test_write_retries_a_server_error(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 5xx is retried a few times before giving up."""
    aioclient_mock.post(PUT_URL, status=HTTPStatus.SERVICE_UNAVAILABLE, text="down")

    with pytest.raises(PynestException):
        await client.async_set_device_data(_protect(), {"night_light_enable": True})

    # One session call plus three attempts.
    assert len(aioclient_mock.mock_calls) == 4


def _command_response(code: int = 0, message: str = "") -> bytes:
    """Return a serialized SendCommandResponse carrying the given status."""
    response = v1_pb2.SendCommandResponse()
    response.status.code = code
    response.status.message = message
    return response.SerializeToString()


async def test_protobuf_command(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A successful protobuf command is sent once."""
    aioclient_mock.post(SEND_COMMAND_URL, content=_command_response())

    await client.async_set_device_data(_protobuf_lock(), {"bolt_locked": True})

    assert len(aioclient_mock.mock_calls) == 2


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        # INVALID_ARGUMENT, the failure behind issue #33.
        (3, "nope", NonRetryablePynestException),
        (5, "nope", NonRetryablePynestException),
        (7, "nope", NonRetryablePynestException),
        # PERMISSION_DENIED for a stale session, the failure behind issue #81.
        (7, "authentication failed", AuthenticationFailedException),
        (9, "nope", NonRetryablePynestException),
        (16, "nope", NotAuthenticatedException),
    ],
)
async def test_protobuf_command_is_not_retried_when_it_cannot_succeed(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    code: int,
    message: str,
    expected: type[Exception],
) -> None:
    """A rejected command fails immediately rather than being hammered."""
    aioclient_mock.post(SEND_COMMAND_URL, content=_command_response(code, message))

    with pytest.raises(expected):
        await client.async_set_device_data(_protobuf_lock(), {"bolt_locked": True})

    assert len(aioclient_mock.mock_calls) == 2


async def test_protobuf_command_retries_a_transient_status(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An UNAVAILABLE command is retried before it is reported."""
    aioclient_mock.post(SEND_COMMAND_URL, content=_command_response(14, "unavailable"))

    with pytest.raises(PynestException):
        await client.async_set_device_data(_protobuf_lock(), {"bolt_locked": True})

    assert len(aioclient_mock.mock_calls) == 4


@pytest.mark.parametrize("status", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN])
async def test_protobuf_command_reports_an_expired_session(
    client: NestClient, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """A rejected protobuf command asks the caller to reauthenticate."""
    aioclient_mock.post(SEND_COMMAND_URL, status=status, text="nope")

    with pytest.raises(NotAuthenticatedException):
        await client.async_set_device_data(_protobuf_lock(), {"bolt_locked": True})


async def test_protobuf_camera_events_stop_after_permission_denied(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A PERMISSION_DENIED camera is warned about once and then left alone.

    See issue #61: Google accounts that are not authorized for the
    camera_observation_history trait used to warn on every poll.
    """
    # PERMISSION_DENIED, the failure behind issue #61.
    aioclient_mock.post(
        SEND_COMMAND_URL, content=_command_response(7, "authentication failed")
    )
    camera = _protobuf_camera()

    assert await client.async_get_camera_events(camera) == []
    assert await client.async_get_camera_events(camera) == []

    # One session call plus the single command that was allowed through.
    assert len(aioclient_mock.mock_calls) == 2
    assert caplog.text.count("Protobuf camera events are not available") == 1


async def test_protobuf_camera_events_keep_polling_after_a_transient_error(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An UNAVAILABLE answer does not disable the camera."""
    aioclient_mock.post(SEND_COMMAND_URL, content=_command_response(14, "unavailable"))
    camera = _protobuf_camera()

    assert await client.async_get_camera_events(camera) == []
    assert await client.async_get_camera_events(camera) == []

    # One session call plus three attempts for each of the two polls.
    assert len(aioclient_mock.mock_calls) == 7


_EventType = nest_history_pb2.CameraObservationHistoryTrait.CameraObservationHistoryResponse.CameraEventTimeWindow.EventType


def _camera_history_response(*event_types: int) -> bytes:
    """Return a serialized camera history answer with one event of these types."""
    history = nest_history_pb2.CameraObservationHistoryTrait.CameraObservationHistoryResponse()
    event = history.cameraEventWindow.cameraEvent.add(eventId="event-1")
    event.eventType.extend(event_types)
    event.startTime.seconds = 1790000000
    event.endTime.seconds = 1790000010
    response = v1_pb2.SendCommandResponse()
    operation = response.sendCommandResponse.add().traitOperations.add()
    operation.event.event.Pack(history, type_url_prefix="type.nestlabs.com/")
    return response.SerializeToString()


async def test_protobuf_camera_events_use_the_rest_type_names(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Protobuf event types are named like the REST cuepoint API names them."""
    aioclient_mock.post(
        SEND_COMMAND_URL,
        content=_camera_history_response(
            _EventType.EVENT_MOTION,
            _EventType.EVENT_PERSON_TALKING,
            _EventType.EVENT_DOG_BARKING,
            _EventType.EVENT_UNFAMILIAR_FACE,
            _EventType.EVENT_PACKAGE_DELIVERED,
            _EventType.EVENT_ANIMAL_DOG,
        ),
    )
    events = await client.async_get_camera_events(_protobuf_camera())

    assert [event["types"] for event in events] == [
        [
            "motion",
            "person-talking",
            "dog-barking",
            "unfamiliar-face",
            "package-delivered",
            "animal-dog",
        ]
    ]
