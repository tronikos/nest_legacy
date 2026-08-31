"""Tests for the Nest Legacy integration."""

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.json import json_loads_object

from pytest_homeassistant_custom_component.common import MockConfigEntry

_FIXTURES = Path(__file__).parent / "fixtures"


async def async_load_fixture_json(hass: HomeAssistant, name: str) -> dict[str, Any]:
    """Load a JSON fixture stored next to these tests.

    The fixture helpers in tests.common locate files relative to the caller,
    which does not survive being called from a fixture, so read them directly.
    """
    return await hass.async_add_executor_job(
        lambda: json_loads_object((_FIXTURES / name).read_text(encoding="utf-8"))
    )


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the config entry to Home Assistant and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
