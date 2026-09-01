"""Tests for the Nest Legacy lock platform."""

from typing import Any
from unittest.mock import AsyncMock

from custom_components.nest_legacy.pynest.protobuf_gen.weave.trait import (
    security_pb2 as weave_security_pb2,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.lock import (
    DOMAIN as LOCK_DOMAIN,
    SERVICE_LOCK,
    SERVICE_UNLOCK,
    LockState,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .const import LOCK_KEY

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)

ENTITY_ID = "lock.front_door_front_door"
_BOLT_LOCK_KEY = weave_security_pb2.BoltLockTrait.DESCRIPTOR.full_name


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only this platform."""
    return [Platform.LOCK]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """All lock entities are created as expected."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("service", "expected"),
    [(SERVICE_LOCK, True), (SERVICE_UNLOCK, False)],
)
async def test_lock_and_unlock(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_nest_client: AsyncMock,
    service: str,
    expected: bool,
) -> None:
    """Locking and unlocking reach the client; see issue #50."""
    await hass.services.async_call(
        LOCK_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    device, data = mock_nest_client.async_set_device_data.call_args[0]
    assert device.object_key == LOCK_KEY
    assert data == {"bolt_locked": expected}


@pytest.mark.parametrize(
    ("actuator_state", "locked_state", "expected"),
    [
        (
            "BOLT_ACTUATOR_STATE_OK",
            "BOLT_LOCKED_STATE_UNLOCKED",
            LockState.UNLOCKED,
        ),
        (
            "BOLT_ACTUATOR_STATE_LOCKING",
            "BOLT_LOCKED_STATE_UNLOCKED",
            LockState.LOCKING,
        ),
        (
            "BOLT_ACTUATOR_STATE_UNLOCKING",
            "BOLT_LOCKED_STATE_LOCKED",
            LockState.UNLOCKING,
        ),
        (
            "BOLT_ACTUATOR_STATE_JAMMED_LOCKING",
            "BOLT_LOCKED_STATE_UNLOCKED",
            LockState.JAMMED,
        ),
    ],
)
async def test_bolt_states(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_nest_client: AsyncMock,
    observe_data: dict[str, dict[str, Any]],
    actuator_state: str,
    locked_state: str,
    expected: str,
) -> None:
    """The actuator state takes priority over the resting bolt state."""
    trait = observe_data[LOCK_KEY][_BOLT_LOCK_KEY]
    trait.actuatorState = getattr(
        weave_security_pb2.BoltLockTrait.BoltActuatorState, actuator_state
    )
    trait.lockedState = getattr(
        weave_security_pb2.BoltLockTrait.BoltLockedState, locked_state
    )
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == expected
