"""Adds config flow for Nest."""

from collections.abc import Mapping
import re
from typing import Any, override

from aiohttp import ClientError
import probatio

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    CONF_ACCOUNT_TYPE,
    CONF_COOKIES,
    CONF_ENABLE_PROTOBUF_CAMERA,
    CONF_ENABLE_PROTOBUF_PROTECT,
    CONF_ENABLE_PROTOBUF_STRUCTURE,
    CONF_EVENT_POLL_INTERVAL,
    CONF_FIELD_TEST,
    CONF_ISSUE_TOKEN,
    CONF_SECTION_PROTOBUF,
    DEFAULT_EVENT_POLL_INTERVAL,
    DOMAIN,
    LOGGER,
)
from .coordinator import NestConfigEntry
from .pynest.client import NestClient
from .pynest.exceptions import BadCredentialsException, NestServiceException

_DOCS_URL = "https://github.com/tronikos/nest_legacy#configuration"
_ISSUE_TOKEN_PREFIX = "https://accounts.google.com/o/oauth2/iframerpc"
# At least one of these carries the Google sign-in session. A cookie string
# without any of them was copied from the wrong request or got truncated.
_GOOGLE_SESSION_COOKIES = ("SID", "__Secure-1PSID", "__Secure-3PSID")


def _unwrap(value: str) -> str:
    """Undo line wrapping that pasting a long value can introduce."""
    return "".join(line.strip() for line in value.splitlines())


def _normalize_google_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Clean up pasted Google credentials."""
    cookies = _unwrap(user_input[CONF_COOKIES])
    # Drop the header name in case the whole "Cookie: ..." line was copied.
    cookies = re.sub(r"^cookie:\s*", "", cookies, flags=re.IGNORECASE)
    return {
        **user_input,
        CONF_ISSUE_TOKEN: _unwrap(user_input[CONF_ISSUE_TOKEN]),
        CONF_COOKIES: cookies,
    }


def _google_input_errors(user_input: dict[str, Any]) -> dict[str, str]:
    """Catch Google credentials that were copied from the wrong place."""
    errors: dict[str, str] = {}
    issue_token: str = user_input[CONF_ISSUE_TOKEN]
    if (
        not issue_token.startswith(_ISSUE_TOKEN_PREFIX)
        or "action=issueToken" not in issue_token
    ):
        errors[CONF_ISSUE_TOKEN] = "invalid_issue_token"
    cookie_names = {
        pair.partition("=")[0].strip() for pair in user_input[CONF_COOKIES].split(";")
    }
    if cookie_names.isdisjoint(_GOOGLE_SESSION_COOKIES):
        errors[CONF_COOKIES] = "invalid_cookies"
    return errors


class NestConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Nest."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._options: dict[str, Any] = {}

    async def _validate_input(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Validate user credentials."""
        account_type = self._options[CONF_ACCOUNT_TYPE]
        field_test = self._options.get(CONF_FIELD_TEST, False)

        client = NestClient(async_create_clientsession(self.hass), field_test)

        nest_session = None
        data: dict[str, Any] = {}
        if account_type == "google":
            issue_token = user_input[CONF_ISSUE_TOKEN]
            cookies = user_input[CONF_COOKIES]
            nest_session = await client.async_authenticate_with_google_credentials(
                issue_token, cookies
            )
            # Validating already made Google rotate some cookies, and the
            # pasted values stop working after a rotation or two.
            data[CONF_COOKIES] = client.google_cookies or cookies
        elif account_type == "nest":
            access_token = user_input[CONF_ACCESS_TOKEN]
            nest_session = await client.async_authenticate_with_nest_token(access_token)
        else:
            raise ValueError("Invalid account type")

        await self.async_set_unique_id(nest_session.user)
        return {
            "title": f"Nest{' FT' if field_test else ''} ({nest_session.email})",
            "data": data,
        }

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self._options = user_input
            account_type = user_input[CONF_ACCOUNT_TYPE]
            if account_type == "google":
                return await self.async_step_google_account()
            if account_type == "nest":
                return await self.async_step_nest_account()

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ACCOUNT_TYPE, default="google"): probatio.In(
                        {"google": "Google Account", "nest": "Nest Account"}
                    ),
                    probatio.Optional(CONF_FIELD_TEST, default=False): bool,
                }
            ),
        )

    async def _show_form_and_handle_errors(
        self,
        step_id: str,
        schema: probatio.Schema,
        user_input: dict[str, Any] | None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show form, validate input, and handle exceptions."""
        errors = errors or {}
        if user_input and not errors:
            try:
                info = await self._validate_input(user_input)
            except TimeoutError, ClientError, NestServiceException:
                errors["base"] = "cannot_connect"
            except BadCredentialsException:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unknown error during validation")
                errors["base"] = "unknown"
            else:
                data = {**self._options, **user_input, **info["data"]}
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data=data
                    )
                if self.source == SOURCE_RECONFIGURE:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data=data
                    )

                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=data)

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={
                "docs_url": _DOCS_URL,
                "issue_token_prefix": _ISSUE_TOKEN_PREFIX,
            },
        )

    async def async_step_google_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Google Account authentication."""
        schema = probatio.Schema(
            {
                probatio.Required(CONF_ISSUE_TOKEN): str,
                probatio.Required(CONF_COOKIES): str,
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _normalize_google_input(user_input)
            errors = _google_input_errors(user_input)
        return await self._show_form_and_handle_errors(
            "google_account", schema, user_input, errors
        )

    async def async_step_nest_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Nest Account authentication."""
        schema = probatio.Schema({probatio.Required(CONF_ACCESS_TOKEN): str})
        return await self._show_form_and_handle_errors(
            "nest_account", schema, user_input
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        reauth_entry = self._get_reauth_entry()
        self._options = dict(reauth_entry.data)
        return await self.async_step_user(self._options)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        reconfigure_entry = self._get_reconfigure_entry()
        self._options = dict(reconfigure_entry.data)
        return await self.async_step_user(self._options)

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: NestConfigEntry) -> OptionsFlowWithReload:
        """Create the options flow."""
        return NestOptionsFlowHandler()


class NestOptionsFlowHandler(OptionsFlowWithReload):
    """Handle options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input is not None:
            # Flatten the section back out so the stored options stay a flat
            # dict, which is what the coordinator and the client read.
            protobuf_input = user_input.pop(CONF_SECTION_PROTOBUF, {})
            return self.async_create_entry(
                title="", data={**user_input, **protobuf_input}
            )

        options = {
            probatio.Optional(
                CONF_EVENT_POLL_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_EVENT_POLL_INTERVAL, DEFAULT_EVENT_POLL_INTERVAL
                ),
            ): probatio.All(probatio.Coerce(int), probatio.Range(min=0)),
            probatio.Required(CONF_SECTION_PROTOBUF): section(
                probatio.Schema(
                    {
                        probatio.Optional(
                            CONF_ENABLE_PROTOBUF_STRUCTURE,
                            default=self.config_entry.options.get(
                                CONF_ENABLE_PROTOBUF_STRUCTURE, False
                            ),
                        ): bool,
                        probatio.Optional(
                            CONF_ENABLE_PROTOBUF_PROTECT,
                            default=self.config_entry.options.get(
                                CONF_ENABLE_PROTOBUF_PROTECT, False
                            ),
                        ): bool,
                        probatio.Optional(
                            CONF_ENABLE_PROTOBUF_CAMERA,
                            default=self.config_entry.options.get(
                                CONF_ENABLE_PROTOBUF_CAMERA,
                                self.config_entry.data.get(CONF_ACCOUNT_TYPE)
                                == "google",
                            ),
                        ): bool,
                    }
                ),
                options={"collapsed": True},
            ),
        }

        return self.async_show_form(
            step_id="user", data_schema=probatio.Schema(options)
        )
