"""Tests for the protobuf observe stream and camera event history."""

import asyncio
import logging
from typing import Any

from custom_components.nest_legacy.pynest.client import NestClient
from custom_components.nest_legacy.pynest.exceptions import PynestException
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait import (
    hvac_pb2 as nest_hvac_pb2,
    security_pb2 as nest_security_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nest.trait.product import (
    camera_pb2 as nest_camera_pb2,
)
from custom_components.nest_legacy.pynest.protobuf_gen.nestlabs.gateway import v2_pb2
from custom_components.nest_legacy.pynest.protobuf_gen.weave.trait import (
    security_pb2 as weave_security_pb2,
)
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .test_client import SESSION_RESPONSE, SESSION_URL

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

OBSERVE_URL = (
    "https://grpc-web.production.nest.com/nestlabs.gateway.v2.GatewayService/Observe"
)
RESOURCE_ID = "DEVICE_0000000000000002"
_NESTLABS_TYPE_URL_PREFIX = "type.nestlabs.com/"


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> NestClient:
    """Return an authenticated client."""
    aioclient_mock.get(SESSION_URL, json=SESSION_RESPONSE)
    client = NestClient(async_get_clientsession(hass))
    await client._async_get_session("test-token")
    return client


def _trait_state(
    message: Any,
    *,
    trait_label: str = "bolt_lock",
    accepted: bool = True,
    type_url: str | None = None,
) -> dict[str, Any]:
    """Describe one trait state to put on the wire."""
    return {
        "message": message,
        "trait_label": trait_label,
        "accepted": accepted,
        "type_url": type_url,
    }


def _stream(*trait_states: dict[str, Any]) -> bytes:
    """Return one length delimited observe frame carrying the trait states."""
    outer = v2_pb2.ObserveResponse()
    inner = outer.observeResponse.add()
    for spec in trait_states:
        state = inner.traitStates.add()
        state.traitId.resourceId = RESOURCE_ID
        state.traitId.traitLabel = spec["trait_label"]
        if spec["type_url"] is not None:
            state.patch.values.type_url = spec["type_url"]
        else:
            state.patch.values.Pack(
                spec["message"], type_url_prefix=_NESTLABS_TYPE_URL_PREFIX
            )
        state.stateTypes.append(
            v2_pb2.ACCEPTED if spec["accepted"] else v2_pb2.CONFIRMED
        )

    # The stream frames each outer ObserveResponse the same way protobuf
    # serializes it: field 1, wire type 2, length, payload.
    return outer.SerializeToString()


async def _collect(client: NestClient) -> list[dict[str, Any]]:
    """Drain the observe stream."""
    return [updates async for updates in client.async_observe_for_updates()]


async def test_observe_yields_a_connected_marker_then_updates(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The stream signals it is connected before any data arrives."""
    bolt_lock = weave_security_pb2.BoltLockTrait(
        lockedState=weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_LOCKED
    )
    aioclient_mock.post(OBSERVE_URL, content=_stream(_trait_state(bolt_lock)))

    results = await _collect(client)

    assert results[0] == {}
    traits = results[1][RESOURCE_ID]
    assert weave_security_pb2.BoltLockTrait.DESCRIPTOR.full_name in traits


async def test_observe_skips_unknown_traits(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A trait type the integration does not model is ignored."""
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(
                None,
                trait_label="mystery",
                type_url="type.nestlabs.com/nest.trait.NotAThing",
            )
        ),
    )

    assert await _collect(client) == [{}]


async def test_observe_skips_bucketized_traits(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The bucketized copies of a trait are history, not current state."""
    bolt_lock = weave_security_pb2.BoltLockTrait()
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(_trait_state(bolt_lock, trait_label="bolt_lock_bucketized")),
    )

    assert await _collect(client) == [{}]


async def test_observe_skips_unpaired_traits(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The unpaired copies of a trait are settings for unpaired mode."""
    display = nest_hvac_pb2.DisplaySettingsTrait(wakeOnRemoteTempChange=True)
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(_trait_state(display, trait_label="unpaired_display_settings")),
    )

    assert await _collect(client) == [{}]


async def test_observe_keeps_the_paired_trait_over_its_unpaired_copy(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A paired label and its unpaired copy neither collide nor swap places.

    The paired label is the live one, so it is what survives under the trait
    type; see issue #90.
    """
    paired = nest_hvac_pb2.DisplaySettingsTrait(wakeOnRemoteTempChange=True)
    unpaired = nest_hvac_pb2.DisplaySettingsTrait(wakeOnRemoteTempChange=False)
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(paired, trait_label="display_settings"),
            _trait_state(unpaired, trait_label="unpaired_display_settings"),
        ),
    )

    results = await _collect(client)

    trait = results[1][RESOURCE_ID][
        nest_hvac_pb2.DisplaySettingsTrait.DESCRIPTOR.full_name
    ]
    assert trait.wakeOnRemoteTempChange is True
    assert "only one will be used" not in caplog.text


async def test_observe_prefers_accepted_state(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A confirmed state does not overwrite the accepted one in the same batch."""
    accepted = weave_security_pb2.BoltLockTrait(
        lockedState=weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_LOCKED
    )
    confirmed = weave_security_pb2.BoltLockTrait(
        lockedState=weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_UNLOCKED
    )
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(accepted),
            _trait_state(confirmed, accepted=False),
        ),
    )

    results = await _collect(client)

    trait = results[1][RESOURCE_ID][
        weave_security_pb2.BoltLockTrait.DESCRIPTOR.full_name
    ]
    assert (
        trait.lockedState
        == weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_LOCKED
    )


async def test_observe_publishes_the_live_image_url(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The URL a snapshot needs arrives on the stream, not in the reply.

    upload_live_image is asynchronous: the command is acknowledged right away
    and the camera publishes the URL later as an UploadLiveImageTrait.
    """
    url = "https://nexus.example.com/live/1"
    waiter = asyncio.Event()
    client._live_image_waiters[RESOURCE_ID] = waiter
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(nest_camera_pb2.UploadLiveImageTrait(liveImageUrl=url))
        ),
    )

    await _collect(client)

    assert client._live_image_urls[RESOURCE_ID] == url
    assert waiter.is_set()
    # Publishing consumes the waiter so a later snapshot does not reuse it.
    assert RESOURCE_ID not in client._live_image_waiters


async def test_observe_ignores_the_older_live_image_url(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An ACCEPTED upload answer wins over a CONFIRMED one in the same batch."""
    fresh = "https://nexus.example.com/live/fresh"
    stale = "https://nexus.example.com/live/stale"
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(nest_camera_pb2.UploadLiveImageTrait(liveImageUrl=fresh)),
            _trait_state(
                nest_camera_pb2.UploadLiveImageTrait(liveImageUrl=stale),
                accepted=False,
            ),
        ),
    )

    await _collect(client)

    assert client._live_image_urls[RESOURCE_ID] == fresh


async def test_observe_warns_about_colliding_labels(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two labels for one trait type are reported; see the logs in issue #51.

    Only one of them survives, so the warning is the signal to add the label
    to _LABEL_SPECIFIC_TRAITS.
    """
    equipment = nest_hvac_pb2.EquipmentSettingsTrait()
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(equipment, trait_label="equipment_settings"),
            _trait_state(equipment, trait_label="alt_equipment_settings"),
        ),
    )

    await _collect(client)

    assert "only one will be used" in caplog.text


async def test_observe_warns_once_about_a_repeated_collision(
    client: NestClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A collision that repeats on every poll is only reported the first time.

    The full values behind it are logged at DEBUG level, once, so a collision
    does not dominate the log; see issue #90.
    """
    equipment = nest_hvac_pb2.EquipmentSettingsTrait()
    frame = _stream(
        _trait_state(equipment, trait_label="equipment_settings"),
        _trait_state(equipment, trait_label="alt_equipment_settings"),
    )
    aioclient_mock.post(OBSERVE_URL, content=frame + frame + frame)

    caplog.set_level(logging.DEBUG)
    await _collect(client)

    assert caplog.text.count("only one will be used") == 1
    assert caplog.text.count("Colliding traits of type") == 1


async def test_observe_keeps_label_specific_traits_apart(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Traits that carry meaning in their label are stored under it too."""
    enhanced = nest_security_pb2.EnhancedBoltLockSettingsTrait(autoRelockOn=True)
    aioclient_mock.post(
        OBSERVE_URL,
        content=_stream(
            _trait_state(enhanced, trait_label="enhanced_bolt_lock_settings")
        ),
    )

    results = await _collect(client)

    assert (
        nest_security_pb2.EnhancedBoltLockSettingsTrait.DESCRIPTOR.full_name
        in results[1][RESOURCE_ID]
    )


async def test_observe_reports_a_failed_connection(
    client: NestClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected stream is reported so the coordinator can back off."""
    aioclient_mock.post(OBSERVE_URL, status=500, text="down")

    with pytest.raises(PynestException):
        await _collect(client)
