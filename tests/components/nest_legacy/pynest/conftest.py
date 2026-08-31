"""Fixtures for the pynest client tests."""

from collections.abc import Generator
from http import HTTPStatus
from unittest.mock import patch

import pytest

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)


@pytest.fixture(autouse=True)
def mock_response_ok() -> Generator[None]:
    """Add the ClientResponse attributes the aiohttp mocker does not implement.

    The client branches on ClientResponse.ok and ClientResponse.content_length,
    neither of which AiohttpClientMockResponse provides.
    """
    with (
        patch.object(
            AiohttpClientMockResponse,
            "ok",
            property(lambda self: self.status < HTTPStatus.BAD_REQUEST),
            create=True,
        ),
        patch.object(
            AiohttpClientMockResponse,
            "content_length",
            property(lambda self: len(self.response)),
            create=True,
        ),
    ):
        yield
