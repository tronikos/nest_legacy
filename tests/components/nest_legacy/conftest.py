"""Fixtures for the Nest Legacy integration tests."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from custom_components.nest_legacy.const import (
    CONF_ACCOUNT_TYPE,
    CONF_COOKIES,
    CONF_ISSUE_TOKEN,
    DOMAIN,
)
from custom_components.nest_legacy.pynest.models import (
    NestLimits,
    NestSession,
    NestUrls,
)
import pytest

from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant

from . import async_load_fixture_json, setup_integration
from .const import protobuf_updates

from pytest_homeassistant_custom_component.common import MockConfigEntry

USER_ID = "1234567890"
EMAIL = "test@example.com"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components rather than from core."""


@pytest.fixture
def nest_session() -> NestSession:
    """Return a Nest session that is not close to expiring."""
    return NestSession(
        access_token="test-access-token",
        email=EMAIL,
        expires_in="Fri, 01-Jan-2100 00:00:00",
        user=USER_ID,
        userid=USER_ID,
        limits=NestLimits(
            thermostats_per_structure=20,
            structures=5,
            smoke_detectors_per_structure=18,
            smoke_detectors=54,
            thermostats=60,
        ),
        urls=NestUrls(
            rubyapi_url="https://home.nest.com/",
            czfe_url="https://czfe.example.com",
            log_upload_url="https://upload.example.com/upload",
            transport_url="https://transport.example.com",
            weather_url="https://apps-weather.example.com/weather/v1?query=",
            support_url="https://nest.secure.force.com/support/webapp?",
            direct_transport_url="https://transport.example.com:443",
        ),
    )


@pytest.fixture
async def app_launch_data(hass: HomeAssistant) -> dict[str, Any]:
    """Return the REST buckets returned by the app launch endpoint."""
    return await async_load_fixture_json(hass, "app_launch.json")


@pytest.fixture
def observe_data() -> dict[str, dict[str, Any]]:
    """Return the protobuf traits returned by the observe stream."""
    return protobuf_updates()


@pytest.fixture
def mock_nest_client(
    nest_session: NestSession,
    app_launch_data: dict[str, Any],
    observe_data: dict[str, dict[str, Any]],
) -> Generator[AsyncMock]:
    """Mock the Nest client the coordinator talks to."""
    with patch(
        "custom_components.nest_legacy.coordinator.NestClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.async_authenticate_with_google_credentials.return_value = nest_session
        client.async_authenticate_with_nest_token.return_value = nest_session
        client.is_expired.return_value = False
        client.async_get_first_data.return_value = app_launch_data
        client.async_get_camera_events.return_value = []
        client.async_get_camera_properties.return_value = {}

        async def _observe() -> AsyncGenerator[dict[str, dict[str, Any]]]:
            """Yield one batch of updates, then stay connected."""
            yield observe_data
            await asyncio.Event().wait()

        client.async_observe_for_updates = Mock(side_effect=_observe)

        async def _subscribe() -> dict[str, dict[str, Any]]:
            """Return no REST updates, then stay connected."""
            await asyncio.Event().wait()
            return {}

        client.async_subscribe_for_updates = AsyncMock(side_effect=_subscribe)
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for a Google account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Nest ({EMAIL})",
        unique_id=USER_ID,
        data={
            CONF_ACCOUNT_TYPE: "google",
            CONF_ISSUE_TOKEN: "https://accounts.google.com/o/oauth2/iframerpc?test",
            CONF_COOKIES: "OCAK=test; SID=test",
        },
    )


@pytest.fixture
def mock_nest_account_config_entry() -> MockConfigEntry:
    """Return a config entry for a legacy Nest account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Nest ({EMAIL})",
        unique_id=USER_ID,
        data={
            CONF_ACCOUNT_TYPE: "nest",
            CONF_ACCESS_TOKEN: "test-legacy-token",
        },
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Mock setting up a config entry."""
    with patch(
        "custom_components.nest_legacy.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
) -> MockConfigEntry:
    """Set up the integration and return its config entry."""
    await setup_integration(hass, mock_config_entry)
    return mock_config_entry
