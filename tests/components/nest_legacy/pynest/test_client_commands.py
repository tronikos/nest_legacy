"""Tests for the command paths of the pynest client."""

from http import HTTPStatus
import time
from typing import Any
from unittest.mock import AsyncMock, patch

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
    )


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
    """A camera that is still denied after a refresh is given up.

    See issues #61 and #81: Nest answers the same PERMISSION_DENIED
    "authentication failed" both for a stale session and for an account that
    may never read the camera_observation_history trait. The first failure is
    retried after refreshing the session; only a failure that survives that is
    permanent.
    """
    # PERMISSION_DENIED, the answer behind both issues.
    aioclient_mock.post(
        SEND_COMMAND_URL, content=_command_response(7, "authentication failed")
    )
    camera = _protobuf_camera()

    # First failure: the caller refreshes the session and retries.
    with pytest.raises(AuthenticationFailedException):
        await client.async_get_camera_events(camera)

    # The retry against the fresh session fails the same way: give it up.
    assert await client.async_get_camera_events(camera) == []
    # Now blacklisted, so it is not queried again.
    assert await client.async_get_camera_events(camera) == []

    # One session call plus the two commands it took to give the camera up.
    assert len(aioclient_mock.mock_calls) == 3
    assert caplog.text.count("Protobuf camera events are not available") == 1


async def test_protobuf_camera_events_stop_on_a_plain_permission_denial(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A denial that is not about authentication is given up straight away."""
    aioclient_mock.post(
        SEND_COMMAND_URL,
        content=_command_response(7, "The caller does not have permission"),
    )
    camera = _protobuf_camera()

    assert await client.async_get_camera_events(camera) == []

    assert camera.object_key in client._protobuf_events_unauthorized
    # One session call plus the single command that was allowed through.
    assert len(aioclient_mock.mock_calls) == 2
    assert caplog.text.count("Protobuf camera events are not available") == 1


async def test_protobuf_camera_events_survive_an_expired_session(
    client: NestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poll that succeeds after a refresh resets the failure count.

    The camera session token is short-lived, so the event poll runs into an
    expired session regularly. Recovering from it keeps the camera in the poll
    instead of dropping it, and the count starts again after every success.
    See issue #81.
    """
    expired = AuthenticationFailedException(
        "Command failed with code 7: authentication failed"
    )
    response = v1_pb2.SendCommandResponse()
    response.ParseFromString(_camera_history_response(_EventType.EVENT_MOTION))
    camera = _protobuf_camera()

    with patch.object(
        client,
        "_async_send_command",
        new=AsyncMock(side_effect=[expired, response, expired]),
    ):
        with pytest.raises(AuthenticationFailedException):
            await client.async_get_camera_events(camera)

        events = await client.async_get_camera_events(camera)

        # The success reset the count, so this is a first failure again.
        with pytest.raises(AuthenticationFailedException):
            await client.async_get_camera_events(camera)

    assert [event["types"] for event in events] == [["motion"]]
    assert camera.object_key not in client._protobuf_events_unauthorized
    assert "no longer polling it for events" not in caplog.text


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
