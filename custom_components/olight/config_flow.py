"""Config flow: three fields, validated by actually talking to the station.

Everything the station exposes is local, so there is no account to sign into — but Tuya's LAN
protocol is encrypted with a per-device local key that only the vendor app knows. See the README
for how to read it out of the Olight app; there's nothing to reverse engineer, the Tuya SDK logs it.

Reconfigure exists because the key is not stable: re-pairing the station, or a factory reset, makes
Tuya issue a new one, and DHCP can move the IP. Without it the only fix was delete-and-re-add,
which orphans every entity's history.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_ID,
    CONF_ENABLE_CONTROLS,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    DEFAULT_MODEL,
    DOMAIN,
)
from .tuya_client import OstationClient, OstationError

_LOGGER = logging.getLogger(__name__)

# Tuya ids and keys are fixed-width; catching a mistyped key here beats a connection that fails
# with an opaque decrypt error much later.
_DEVICE_ID_LEN = 22
_LOCAL_KEY_LEN = 16

_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_DEVICE_ID): str,
    vol.Required(CONF_LOCAL_KEY): str,
    vol.Optional(CONF_NAME, default=DEFAULT_MODEL): str,
})

# The device id is the unique id; on reconfigure it is fixed and only host/key can change.
_RECONFIGURE_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_LOCAL_KEY): str,
})

_OPTIONS_SCHEMA = vol.Schema({
    vol.Required(CONF_ENABLE_CONTROLS, default=False): bool,
})


def _validate_fields(device_id: str, local_key: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len(device_id) != _DEVICE_ID_LEN:
        errors[CONF_DEVICE_ID] = "invalid_device_id"
    if len(local_key) != _LOCAL_KEY_LEN:
        errors[CONF_LOCAL_KEY] = "invalid_local_key"
    return errors


async def _try_connect(hass: HomeAssistant, device_id: str, host: str, local_key: str) -> bool:
    """One real status read; the only way to know the key and host are right."""
    client = OstationClient(device_id, host, local_key)
    try:
        await hass.async_add_executor_job(client.status)
        return True
    except OstationError as err:
        _LOGGER.debug("Ostation validation failed: %s", err)
        return False
    finally:
        await hass.async_add_executor_job(client.close)


class OlightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one Ostation, or fix its host/key later."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return OlightOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            device_id = user_input[CONF_DEVICE_ID].strip()
            local_key = user_input[CONF_LOCAL_KEY].strip()

            errors = _validate_fields(device_id, local_key)
            if not errors:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                if not await _try_connect(self.hass, device_id, host, local_key):
                    errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or DEFAULT_MODEL,
                    data={
                        CONF_HOST: host,
                        CONF_DEVICE_ID: device_id,
                        CONF_LOCAL_KEY: local_key,
                        CONF_MODEL: DEFAULT_MODEL,
                    },
                    options={CONF_ENABLE_CONTROLS: False},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the host and/or local key of an existing station in place."""
        entry = self._get_reconfigure_entry()
        device_id = entry.data[CONF_DEVICE_ID]
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            local_key = user_input[CONF_LOCAL_KEY].strip()
            errors = _validate_fields(device_id, local_key)
            if not errors and not await _try_connect(self.hass, device_id, host, local_key):
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_LOCAL_KEY: local_key},
                )

        suggested = user_input or {
            CONF_HOST: entry.data.get(CONF_HOST, ""),
            CONF_LOCAL_KEY: entry.data.get(CONF_LOCAL_KEY, ""),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_RECONFIGURE_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"device_id": device_id},
        )


class OlightOptionsFlow(OptionsFlow):
    """Keep write-capable entities opt-in after installation."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _OPTIONS_SCHEMA,
                {CONF_ENABLE_CONTROLS: self.config_entry.options.get(CONF_ENABLE_CONTROLS, False)},
            ),
        )
