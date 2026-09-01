"""Fixtures for the pynest client tests."""

from collections.abc import Generator
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
    mock_stream,
)


def _content(response: AiohttpClientMockResponse) -> Any:
    """Return the body as a stream that keeps its position between reads."""
    if not hasattr(response, "_cached_content"):
        response._cached_content = mock_stream(response.response)
    return response._cached_content


@pytest.fixture(autouse=True)
def mock_response_ok() -> Generator[None]:
    """Line the aiohttp mock responses up with real ClientResponse objects.

    The client branches on ClientResponse.ok and ClientResponse.content_length,
    which the mocker does not provide, and reads ClientResponse.content in a
    loop, which the mocker rewinds on every access.
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
        patch.object(AiohttpClientMockResponse, "content", property(_content)),
    ):
        yield
