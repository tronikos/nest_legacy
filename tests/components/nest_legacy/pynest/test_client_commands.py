"""Tests for the command paths of the pynest client."""

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

from custom_components.nest_legacy.pynest.client import NestClient
from custom_components.nest_legacy.pynest.exceptions import (
    NonRetryablePynestException,
    NotAuthenticatedException,
    PynestException,
)
from custom_components.nest_legacy.pynest.models import (
    NestCamera,
    NestLock,
    NestProtect,
    NestThermostat,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nestlabs.gateway import v1_pb2
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .test_client import SESSION_RESPONSE, SESSION_URL

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

PUT_URL = "https://transport.example.com/v6/put"
SEND_COMMAND_URL = (
    "https://grpc-web.production.nest.com/nestlabs.gateway.v1.ResourceApi/SendCommand"
)


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


def _thermostat() -> NestThermostat:
    """Return a REST thermostat."""
    return NestThermostat(
        object_key="device.09AA00AA00AA0AAA",
        serial_number="09AA00AA00AA0AAA",
        name="Thermostat",
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


async def test_thermostat_write_splits_shared_and_device_buckets(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Setpoints go to the shared bucket and everything else to the device."""
    aioclient_mock.post(PUT_URL, json={})

    await client.async_set_device_data(
        _thermostat(), {"target_temperature": 21.5, "temperature_lock": True}
    )

    objects = aioclient_mock.mock_calls[-1][2]["objects"]
    assert objects == [
        {
            "object_key": "shared.09AA00AA00AA0AAA",
            "op": "MERGE",
            "value": {"target_temperature": 21.5, "target_change_pending": True},
        },
        {
            "object_key": "device.09AA00AA00AA0AAA",
            "op": "MERGE",
            "value": {"temperature_lock": True},
        },
    ]


async def test_hvac_mode_is_renamed_for_the_rest_api(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The generic hvac_mode key becomes the legacy target_temperature_type."""
    aioclient_mock.post(PUT_URL, json={})

    await client.async_set_device_data(_thermostat(), {"hvac_mode": "heat"})

    objects = aioclient_mock.mock_calls[-1][2]["objects"]
    assert objects[0]["value"]["target_temperature_type"] == "heat"


async def test_exit_eco_is_sent_before_the_setpoint(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Leaving eco is expressed as the schedule eco mode; see issue #33."""
    aioclient_mock.post(PUT_URL, json={})

    await client.async_set_device_data(
        _thermostat(), {"exit_eco": True, "target_temperature": 21.0}
    )

    objects = aioclient_mock.mock_calls[-1][2]["objects"]
    assert {"eco": {"mode": "schedule"}}.items() <= objects[1]["value"].items()


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
    ("code", "expected"),
    [
        # INVALID_ARGUMENT, the failure behind issue #33.
        (3, NonRetryablePynestException),
        (5, NonRetryablePynestException),
        (9, NonRetryablePynestException),
        (16, NotAuthenticatedException),
    ],
)
async def test_protobuf_command_is_not_retried_when_it_cannot_succeed(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    code: int,
    expected: type[Exception],
) -> None:
    """A rejected command fails immediately rather than being hammered."""
    aioclient_mock.post(SEND_COMMAND_URL, content=_command_response(code, "nope"))

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
    aioclient_mock.post(SEND_COMMAND_URL, content=_command_response(7, "nope"))
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
