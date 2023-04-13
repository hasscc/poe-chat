import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import *

from . import DOMAIN, get_client_from_config, init_integration_data, CONF_BOT


def get_flow_schema(defaults: dict):
    return {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, '')): str,
        vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, '')): str,
        # vol.Optional(CONF_BOT, default=defaults.get(CONF_BOT, '')): str,
        # vol.Optional(CONF_BASE, default=defaults.get(CONF_BASE, '')): str,
        vol.Optional('proxy', default=defaults.get('proxy', '')): str,
    }


class PoeChatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(entry)

    async def async_step_user(self, user_input=None):
        init_integration_data(self.hass)
        errors = {}
        if user_input is None:
            user_input = {}
        if name := user_input.get(CONF_NAME):
            await self.async_set_unique_id(name)
            self._abort_if_unique_id_configured()
            if acc := await get_client_from_config(self.hass, user_input, renew=True):
                if not acc.bots:
                    self.context['last_error'] = 'None bot found'
                else:
                    return self.async_create_entry(
                        title=name,
                        data=user_input,
                    )
            errors['base'] = 'cannot_access'
        return self.async_show_form(
            step_id='user',
            data_schema=vol.Schema({
                **get_flow_schema(user_input),
            }),
            errors=errors,
            description_placeholders={'tip': self.context.pop('last_error', '')},
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}
        schema = {}
        if user_input is None:
            user_input = {}
        prev_input = {
            **self.config_entry.data,
            **self.config_entry.options,
            **user_input,
        }
        acc = await get_client_from_config(self.hass, self.config_entry)
        if acc and acc.bot_names:
            bots = {
                k: f'{n} ({k})'
                for k, n in acc.bot_names.items()
            }
            schema[vol.Optional(CONF_BOT, default=prev_input.get(CONF_BOT, ''))] = vol.In(bots)
        if name := user_input.get(CONF_NAME):
            errors['base'] = 'cannot_access'
            if acc := await get_client_from_config(self.hass, user_input, renew=True):
                if not acc.bot_names:
                    self.context['last_error'] = 'None bot found'
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=user_input
                    )
                    return self.async_create_entry(title='', data={})
        return self.async_show_form(
            step_id='init',
            data_schema=vol.Schema({
                **get_flow_schema(prev_input),
                **schema,
            }),
            errors=errors,
            description_placeholders={'tip': self.context.pop('last_error', '')},
        )
