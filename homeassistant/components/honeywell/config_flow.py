"""Config flow to configure the honeywell integration."""
from __future__ import annotations

import asyncio

import AIOSomecomfort
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_COOL_AWAY_TEMPERATURE,
    CONF_HEAT_AWAY_TEMPERATURE,
    DEFAULT_COOL_AWAY_TEMPERATURE,
    DEFAULT_HEAT_AWAY_TEMPERATURE,
    DOMAIN,
)


class HoneywellConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a honeywell config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Create config entry. Show the setup form to the user."""
        errors = {}
        if user_input is not None:
            try:
                await self.is_valid(**user_input)
            except AIOSomecomfort.AuthError:
                errors["base"] = "invalid_auth"
            except (
                AIOSomecomfort.ConnectionError,
                AIOSomecomfort.ConnectionTimeout,
                asyncio.TimeoutError,
            ):
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=DOMAIN,
                    data=user_input,
                )

        data_schema = {
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=errors
        )

    async def is_valid(self, **kwargs) -> bool:
        """Check if login credentials are valid."""
        client = AIOSomecomfort.AIOSomeComfort(
            kwargs[CONF_USERNAME],
            kwargs[CONF_PASSWORD],
            session=async_get_clientsession(self.hass),
        )

        await client.login()
        return True

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HoneywellOptionsFlowHandler:
        """Options callback for Honeywell."""
        return HoneywellOptionsFlowHandler(config_entry)


class HoneywellOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options for Honeywell."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        """Initialize Honeywell options flow."""
        self.config_entry = entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title=DOMAIN, data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COOL_AWAY_TEMPERATURE,
                        default=self.config_entry.options.get(
                            CONF_COOL_AWAY_TEMPERATURE, DEFAULT_COOL_AWAY_TEMPERATURE
                        ),
                    ): int,
                    vol.Required(
                        CONF_HEAT_AWAY_TEMPERATURE,
                        default=self.config_entry.options.get(
                            CONF_HEAT_AWAY_TEMPERATURE, DEFAULT_HEAT_AWAY_TEMPERATURE
                        ),
                    ): int,
                }
            ),
        )
