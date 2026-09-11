"""Constants for the Nest Legacy integration."""

import logging
from typing import Final

LOGGER: logging.Logger = logging.getLogger(__package__)

DOMAIN: Final = "nest_legacy"
ATTRIBUTION: Final = "Data provided by Google/Nest"

CONF_ACCOUNT_TYPE: Final = "account_type"
CONF_ISSUE_TOKEN: Final = "issue_token"
CONF_COOKIES: Final = "cookies"
CONF_FIELD_TEST: Final = "field_test"
CONF_EVENT_POLL_INTERVAL: Final = "event_poll_interval"

DEFAULT_EVENT_POLL_INTERVAL: Final = 5

# Protobuf enable options
CONF_ENABLE_PROTOBUF_LOCK: Final = "enable_protobuf_lock"
CONF_ENABLE_PROTOBUF_THERMOSTAT: Final = "enable_protobuf_thermostat"
CONF_ENABLE_PROTOBUF_STRUCTURE: Final = "enable_protobuf_structure"
CONF_ENABLE_PROTOBUF_PROTECT: Final = "enable_protobuf_protect"
CONF_ENABLE_PROTOBUF_CAMERA: Final = "enable_protobuf_camera"

# Whether to poll the camera cuepoint/observation API for events at all.
# The five options above only choose which CHANNEL a device type arrives on;
# none of them stops the camera event poll, which runs for every online,
# streaming camera on the account. Installs that use this integration for
# thermostats or temperature sensors pay for that poll - and for the repeated
# warnings it emits - with nothing to show for it.
CONF_ENABLE_CAMERA_EVENTS: Final = "enable_camera_events"
DEFAULT_ENABLE_CAMERA_EVENTS: Final = True
