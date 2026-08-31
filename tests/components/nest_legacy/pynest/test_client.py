"""Tests for the HTTP layer of the pynest client."""

from http import HTTPStatus
from typing import Any

from custom_components.nest_legacy.pynest.client import (
    NestClient,
    _is_transient_status,
    _transient_exception,
)
from custom_components.nest_legacy.pynest.exceptions import (
    BadCredentialsException,
    BadGatewayException,
    EmptyResponseException,
    GatewayTimeoutException,
    NestServiceException,
    NotAuthenticatedException,
    PynestException,
)
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

SESSION_URL = "https://home.nest.com/session"
CAMERA_LOGIN_URL = "https://webapi.camera.home.nest.com/api/v1/login.login_nest"

SESSION_RESPONSE: dict[str, Any] = {
    "access_token": "test-access-token",
    "email": "test@example.com",
    "expires_in": "Fri, 01-Jan-2100 00:00:00 GMT",
    "userid": "1234567890",
    "user": "1234567890",
    "is_superuser": False,
    "language": "en_US",
    "is_staff": False,
    "2fa_state": "not_enrolled",
    "2fa_enabled": False,
    "2fa_state_changed": "Thu, 01-Jan-2026 00:00:00 GMT",
    "urls": {
        "rubyapi_url": "https://home.nest.com/",
        "czfe_url": "https://czfe.example.com",
        "log_upload_url": "https://upload.example.com/upload",
        "transport_url": "https://transport.example.com",
        "weather_url": "https://apps-weather.example.com/weather/v1?query=",
        "support_url": "https://nest.secure.force.com/support/webapp?",
        "direct_transport_url": "https://transport.example.com:443",
    },
    "limits": {
        "thermostats_per_structure": 20,
        "structures": 5,
        "smoke_detectors_per_structure": 18,
        "smoke_detectors": 54,
        "thermostats": 60,
    },
}
CAMERA_LOGIN_RESPONSE = {"status": 0, "items": [{"session_token": "camera-token"}]}


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> NestClient:
    """Return a client wired to Home Assistant's mocked aiohttp session."""
    return NestClient(async_get_clientsession(hass))


def test_is_transient_status() -> None:
    """Only server-side and back-off statuses count as transient."""
    assert not _is_transient_status(HTTPStatus.BAD_REQUEST)
    assert not _is_transient_status(HTTPStatus.UNAUTHORIZED)
    assert not _is_transient_status(HTTPStatus.FORBIDDEN)
    assert not _is_transient_status(HTTPStatus.NOT_FOUND)
    assert _is_transient_status(HTTPStatus.REQUEST_TIMEOUT)
    assert _is_transient_status(HTTPStatus.TOO_MANY_REQUESTS)
    assert _is_transient_status(HTTPStatus.INTERNAL_SERVER_ERROR)
    assert _is_transient_status(HTTPStatus.SERVICE_UNAVAILABLE)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.GATEWAY_TIMEOUT, GatewayTimeoutException),
        (HTTPStatus.BAD_GATEWAY, BadGatewayException),
        (HTTPStatus.INTERNAL_SERVER_ERROR, NestServiceException),
        (HTTPStatus.TOO_MANY_REQUESTS, NestServiceException),
    ],
)
def test_transient_exception(status: int, expected: type[Exception]) -> None:
    """Each transient status maps onto its own exception type."""
    assert type(_transient_exception(status, "boom")) is expected


async def test_get_session(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A successful session response is parsed into a NestSession."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)

    session = await client._async_get_session("test-token")

    assert session.email == "test@example.com"
    assert session.userid == "1234567890"
    assert session.urls.transport_url == "https://transport.example.com"
    assert not client.is_expired()


@pytest.mark.parametrize(
    "status",
    [
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ],
)
async def test_get_session_rejects_credentials(
    client: NestClient, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """A 4xx means the credentials really are bad."""
    aioclient_mock.get(SESSION_URL, status=status, text="nope")

    with pytest.raises(BadCredentialsException):
        await client._async_get_session("test-token")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.REQUEST_TIMEOUT, NestServiceException),
        (HTTPStatus.TOO_MANY_REQUESTS, NestServiceException),
        (HTTPStatus.INTERNAL_SERVER_ERROR, NestServiceException),
        (HTTPStatus.BAD_GATEWAY, BadGatewayException),
        (HTTPStatus.SERVICE_UNAVAILABLE, NestServiceException),
        (HTTPStatus.GATEWAY_TIMEOUT, GatewayTimeoutException),
    ],
)
async def test_get_session_retries_transient_errors(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    expected: type[Exception],
) -> None:
    """A server-side failure is retryable, not an authentication failure."""
    aioclient_mock.get(SESSION_URL, status=status, text="upstream is unhappy")

    with pytest.raises(expected) as err:
        await client._async_get_session("test-token")

    assert not isinstance(err.value, BadCredentialsException)
    assert isinstance(err.value, PynestException)


async def test_get_camera_session_token(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The camera session token is taken from the first item of the response."""
    aioclient_mock.post(CAMERA_LOGIN_URL, json=CAMERA_LOGIN_RESPONSE)

    await client._async_get_camera_session_token("test-token")

    assert client._camera_session_token == "camera-token"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.INTERNAL_SERVER_ERROR, NestServiceException),
        (HTTPStatus.BAD_GATEWAY, BadGatewayException),
        (HTTPStatus.GATEWAY_TIMEOUT, GatewayTimeoutException),
        (HTTPStatus.TOO_MANY_REQUESTS, NestServiceException),
    ],
)
async def test_camera_session_token_retries_transient_errors(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    expected: type[Exception],
) -> None:
    """A server-side failure on the camera endpoint is retryable too."""
    aioclient_mock.post(CAMERA_LOGIN_URL, status=status, text="upstream is unhappy")

    with pytest.raises(expected):
        await client._async_get_camera_session_token("test-token")


async def test_camera_session_token_body_status(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 carrying a 403 in the body is a credential failure, not a success.

    This is the response legacy Nest accounts without cameras get; see issue #53.
    """
    aioclient_mock.post(
        CAMERA_LOGIN_URL,
        json={
            "status": 403,
            "items": [],
            "status_description": "unauthorized",
            "status_detail": "Unauthorized.",
        },
    )

    with pytest.raises(BadCredentialsException, match="unauthorized"):
        await client._async_get_camera_session_token("test-token")


async def test_camera_session_token_transient_body_status(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 carrying a 503 in the body is retried rather than failed outright."""
    aioclient_mock.post(
        CAMERA_LOGIN_URL,
        json={"status": 503, "items": [], "status_description": "unavailable"},
    )

    with pytest.raises(NestServiceException):
        await client._async_get_camera_session_token("test-token")


async def test_nest_token_auth_survives_camera_rejection(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An account with no cameras still authenticates; see issue #53."""
    aioclient_mock.post(
        CAMERA_LOGIN_URL,
        json={"status": 403, "items": [], "status_description": "unauthorized"},
    )
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)

    session = await client.async_authenticate_with_nest_token("test-token")

    assert session.email == "test@example.com"
    assert client._camera_session_token is None


async def test_nest_token_auth_reports_bad_token(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The session endpoint stays the authority on whether the token is valid."""
    aioclient_mock.post(
        CAMERA_LOGIN_URL,
        json={"status": 403, "items": [], "status_description": "unauthorized"},
    )
    aioclient_mock.get(SESSION_URL, status=HTTPStatus.UNAUTHORIZED, text="nope")

    with pytest.raises(BadCredentialsException):
        await client.async_authenticate_with_nest_token("test-token")


async def test_nest_token_auth_propagates_camera_outage(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transient camera failure is worth retrying, so it is not swallowed."""
    aioclient_mock.post(CAMERA_LOGIN_URL, status=HTTPStatus.SERVICE_UNAVAILABLE)
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)

    with pytest.raises(NestServiceException):
        await client.async_authenticate_with_nest_token("test-token")


async def test_get_first_data_requires_session(client: NestClient) -> None:
    """Fetching data before authenticating is a programming error."""
    with pytest.raises(NotAuthenticatedException):
        await client.async_get_first_data()


def _app_launch_buckets() -> dict[str, Any]:
    """Return an app launch body with one REST-only and one thermostat bucket."""
    return {
        "updated_buckets": [
            {
                "object_key": "device.09AA00AA00AA0AAA",
                "object_revision": 1,
                "object_timestamp": 2,
                "value": {"serial_number": "09AA00AA00AA0AAA"},
            },
            {
                "object_key": "topaz.09AA00AA00AA0AA1",
                "object_revision": 1,
                "object_timestamp": 2,
                "value": {"serial_number": "09AA00AA00AA0AA1"},
            },
        ]
    }


@pytest.mark.parametrize(
    ("enable_protobuf_thermostat", "expected_keys"),
    [
        # The protobuf stream owns the thermostat, so its REST buckets are dropped.
        (True, {"topaz.09AA00AA00AA0AA1"}),
        (False, {"device.09AA00AA00AA0AAA", "topaz.09AA00AA00AA0AA1"}),
    ],
)
async def test_get_first_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_protobuf_thermostat: bool,
    expected_keys: set[str],
) -> None:
    """App launch buckets are keyed by object key and filtered by the options."""
    client = NestClient(
        async_get_clientsession(hass),
        enable_protobuf_thermostat=enable_protobuf_thermostat,
    )
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    await client._async_get_session("test-token")

    aioclient_mock.post(
        "https://home.nest.com/api/0.1/user/1234567890/app_launch",
        json=_app_launch_buckets(),
    )

    data = await client.async_get_first_data()

    assert set(data) == expected_keys


async def test_get_first_data_error(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An error in the app launch body is surfaced."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    await client._async_get_session("test-token")

    aioclient_mock.post(
        "https://home.nest.com/api/0.1/user/1234567890/app_launch",
        json={"error": "unauthorized"},
    )

    with pytest.raises(PynestException, match="unauthorized"):
        await client.async_get_first_data()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.UNAUTHORIZED, NotAuthenticatedException),
        (HTTPStatus.GATEWAY_TIMEOUT, GatewayTimeoutException),
        (HTTPStatus.BAD_GATEWAY, BadGatewayException),
    ],
)
async def test_subscribe_errors(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    expected: type[Exception],
) -> None:
    """The long poll distinguishes an expired session from a server outage."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    await client._async_get_session("test-token")

    aioclient_mock.post(
        "https://transport.example.com/v6/subscribe", status=status, text=""
    )

    with pytest.raises(expected):
        await client.async_subscribe_for_updates()


async def test_subscribe_empty_response(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty 200 is treated as a dropped connection, not as data."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    await client._async_get_session("test-token")

    aioclient_mock.post("https://transport.example.com/v6/subscribe", text="")

    with pytest.raises(EmptyResponseException):
        await client.async_subscribe_for_updates()


async def test_subscribe_requires_session(client: NestClient) -> None:
    """Subscribing before authenticating is a programming error."""
    with pytest.raises(NotAuthenticatedException):
        await client.async_subscribe_for_updates()
