"""Fixtures that Home Assistant core provides to its own component tests.

pytest-homeassistant-custom-component ships tests/conftest.py but not
tests/components/conftest.py, so the handful of fixtures used here are
reproduced.
Keep in sync with
https://github.com/home-assistant/core/blob/dev/tests/components/conftest.py
"""

from collections.abc import Generator
from unittest.mock import patch

import pytest


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Test fixture that ensures all entities are enabled in the registry."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
    ):
        yield
