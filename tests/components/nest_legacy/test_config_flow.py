"""Tests for the Nest Legacy config flow."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError
from custom_components.nest_legacy.const import (
    CONF_ACCOUNT_TYPE,
    CONF_COOKIES,
    CONF_ENABLE_PROTOBUF_CAMERA,
    CONF_ENABLE_PROTOBUF_LOCK,
    CONF_ENABLE_PROTOBUF_PROTECT,
    CONF_ENABLE_PROTOBUF_STRUCTURE,
    CONF_ENABLE_PROTOBUF_THERMOSTAT,
    CONF_EVENT_POLL_INTERVAL,
    CONF_FIELD_TEST,
    CONF_ISSUE_TOKEN,
    DOMAIN,
)
from custom_components.nest_legacy.pynest.exceptions import (
    BadCredentialsException,
    BadGatewayException,
    GatewayTimeoutException,
    NestServiceException,
)
from custom_components.nest_legacy.pynest.models import NestSession
import pytest

from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .conftest import EMAIL, USER_ID

from pytest_homeassistant_custom_component.common import MockConfigEntry

GOOGLE_INPUT = {
    CONF_ISSUE_TOKEN: "https://accounts.google.com/o/oauth2/iframerpc?test",
    CONF_COOKIES: "OCAK=test; SID=test",
}
NEST_INPUT = {CONF_ACCESS_TOKEN: "test-legacy-token"}


@pytest.fixture
def mock_config_flow_client(nest_session: NestSession) -> Generator[AsyncMock]:
    """Mock the client the config flow creates."""
    with patch(
        "custom_components.nest_legacy.config_flow.NestClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.async_authenticate_with_google_credentials.return_value = nest_session
        client.async_authenticate_with_nest_token.return_value = nest_session
        yield client


async def _start_account_flow(hass: HomeAssistant, account_type: str) -> dict[str, Any]:
    """Walk the flow up to the credentials form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ACCOUNT_TYPE: account_type, CONF_FIELD_TEST: False},
    )


@pytest.mark.parametrize(
    ("account_type", "step_id", "user_input"),
    [
        ("google", "google_account", GOOGLE_INPUT),
        ("nest", "nest_account", NEST_INPUT),
    ],
)
async def test_full_flow(
    hass: HomeAssistant,
    mock_config_flow_client: AsyncMock,
    mock_setup_entry: AsyncMock,
    account_type: str,
    step_id: str,
    user_input: dict[str, Any],
) -> None:
    """Both account types create an entry."""
    result = await _start_account_flow(hass, account_type)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step_id

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Nest ({EMAIL})"
    assert result["data"] == {
        CONF_ACCOUNT_TYPE: account_type,
        CONF_FIELD_TEST: False,
        **user_input,
    }
    assert result["result"].unique_id == USER_ID


async def test_field_test_title(
    hass: HomeAssistant,
    mock_config_flow_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """The field test environment is called out in the entry title."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ACCOUNT_TYPE: "google", CONF_FIELD_TEST: True},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], GOOGLE_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Nest FT ({EMAIL})"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BadCredentialsException("No active session found."), "invalid_auth"),
        (ClientError("boom"), "cannot_connect"),
        (TimeoutError, "cannot_connect"),
        # Raised for 5xx/408/429 responses, which are server-side failures and
        # must not be presented as rejected credentials.
        (NestServiceException("503"), "cannot_connect"),
        (BadGatewayException("502"), "cannot_connect"),
        (GatewayTimeoutException("504"), "cannot_connect"),
        (RuntimeError("kaboom"), "unknown"),
    ],
)
async def test_errors_then_recovery(
    hass: HomeAssistant,
    mock_config_flow_client: AsyncMock,
    mock_setup_entry: AsyncMock,
    error: Exception,
    expected: str,
) -> None:
    """Every failure mode shows its own message and the form stays usable."""
    mock_config_flow_client.async_authenticate_with_google_credentials.side_effect = (
        error
    )

    result = await _start_account_flow(hass, "google")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], GOOGLE_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    mock_config_flow_client.async_authenticate_with_google_credentials.side_effect = (
        None
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], GOOGLE_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_account_aborts(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_config_flow_client: AsyncMock,
) -> None:
    """The same Nest user cannot be added twice."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_account_flow(hass, "google")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], GOOGLE_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_dhcp_discovery_starts_user_flow(hass: HomeAssistant) -> None:
    """DHCP discovery falls through to the normal account setup flow.

    nest_legacy supports multiple config entries (multiple Nest accounts),
    so there is no custom async_step_dhcp — the inherited default
    (_async_step_discovery_without_unique_id) handles this correctly, and
    duplicate-account detection happens later via the account's own
    unique_id once credentials are entered.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(
            ip="192.168.1.5",
            hostname="nest",
            macaddress="18b430aabbcc",
        ),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_dhcp_discovery_aborts_if_already_in_progress(
    hass: HomeAssistant,
) -> None:
    """A second concurrent DHCP discovery aborts instead of starting another flow."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(
            ip="192.168.1.5",
            hostname="nest",
            macaddress="18b430aabbcc",
        ),
    )
    assert first["type"] is FlowResultType.FORM

    second = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(
            ip="192.168.1.6",
            hostname="nest",
            macaddress="641666aabbcc",
        ),
    )

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"


async def test_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_config_flow_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Reauthentication updates the stored credentials in place."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "google_account"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ISSUE_TOKEN: "https://accounts.google.com/new", CONF_COOKIES: "SID=new"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_COOKIES] == "SID=new"


async def test_reauth_wrong_account(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_config_flow_client: AsyncMock,
    nest_session: NestSession,
) -> None:
    """Signing in as a different Nest user is rejected."""
    mock_config_entry.add_to_hass(hass)
    mock_config_flow_client.async_authenticate_with_google_credentials.return_value = (
        NestSession(**{**vars(nest_session), "user": "9999999999"})
    )

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], GOOGLE_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_config_flow_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Reconfiguring rewrites the credentials of the existing entry."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "google_account"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ISSUE_TOKEN: "https://accounts.google.com/reconfigured",
            CONF_COOKIES: "SID=reconfigured",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_COOKIES] == "SID=reconfigured"


async def test_options_flow(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """The options flow round trips every option."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    options = {
        CONF_EVENT_POLL_INTERVAL: 30,
        CONF_ENABLE_PROTOBUF_LOCK: False,
        CONF_ENABLE_PROTOBUF_THERMOSTAT: False,
        CONF_ENABLE_PROTOBUF_STRUCTURE: True,
        CONF_ENABLE_PROTOBUF_PROTECT: True,
        CONF_ENABLE_PROTOBUF_CAMERA: False,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == options
