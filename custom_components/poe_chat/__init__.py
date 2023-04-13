"""The component."""
import logging
import asyncio
import datetime
import requests
import random
import voluptuous as vol
from functools import partial

from homeassistant.core import HomeAssistant
from homeassistant.const import *
from homeassistant.config_entries import ConfigEntry
from homeassistant.components import persistent_notification
import homeassistant.helpers.config_validation as cv

import poe

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'poe_chat'
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' \
             'AppleWebKit/537.36 (KHTML, like Gecko) ' \
             'Chrome/109.0.0.0 Safari/537.36'

SCAN_INTERVAL = datetime.timedelta(seconds=86400)
CONF_ACCOUNTS = 'accounts'
CONF_BOT = 'bot'

CONFIG_SCHEMA = vol.Schema(
    {},
    extra=vol.ALLOW_EXTRA,
)


def init_integration_data(hass):
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CONF_ACCOUNTS, {})


async def async_setup(hass: HomeAssistant, hass_config: dict):
    init_integration_data(hass)
    ComponentServices(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    init_integration_data(hass)

    client = await get_client_from_config(hass, entry)

    async def unload(*args):
        acc = hass.data[DOMAIN][CONF_ACCOUNTS].pop(entry.entry_id, None)
        if acc:
            acc.disconnect_ws()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, unload)
    )
    return True


async def get_client_from_config(hass, config, renew=False):
    init_integration_data(hass)
    if isinstance(config, ConfigEntry):
        cfg = {
            **config.data,
            **config.options,
            'entry': config,
            CONF_ENTITY_ID: config.data.get(CONF_NAME) or config.entry_id,
        }
    else:
        cfg = {
            **config,
            CONF_ENTITY_ID: None,
        }

    if eid := cfg.get(CONF_ENTITY_ID):
        client = hass.data[DOMAIN][CONF_ACCOUNTS].get(eid)
        if client and not renew:
            return client

    client = PoeClient(hass, cfg)
    await hass.async_add_executor_job(client.init)
    if eid:
        hass.data[DOMAIN][CONF_ACCOUNTS][eid] = client

    return client


async def async_reload_integration_config(hass, config):
    hass.data[DOMAIN]['config'] = config
    hass.data[DOMAIN].setdefault(CONF_ACCOUNTS, {})
    return config


class ComponentServices:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass

        hass.helpers.service.async_register_admin_service(
            DOMAIN, SERVICE_RELOAD, self.handle_reload_config,
        )

        hass.services.async_register(
            DOMAIN, 'chat', self.async_chat,
            schema=vol.Schema({
                vol.Required('name'): cv.string,
                vol.Optional('bot'): cv.string,
                vol.Required('message'): cv.string,
                vol.Optional('extra'): cv.match_all,
                vol.Optional('throw', default=False): cv.boolean,
            }),
        )

    async def handle_reload_config(self, call):
        current_entries = self.hass.config_entries.async_entries(DOMAIN)
        reload_tasks = [
            self.hass.config_entries.async_reload(entry.entry_id)
            for entry in current_entries
        ]
        await asyncio.gather(*reload_tasks)

    async def async_chat(self, call):
        dat = call.data or {}
        nam = dat.get(CONF_NAME)
        acc = self.hass.data[DOMAIN][CONF_ACCOUNTS].get(nam) if nam else None
        if not isinstance(acc, PoeClient):
            _LOGGER.warning('Account %s not found in %s.', nam, self.hass.data[DOMAIN].get(CONF_ACCOUNTS))
            return False
        reply = await self.hass.async_add_executor_job(partial(acc.send, **dat))
        if reply:
            if dat.get('throw', False):
                persistent_notification.async_create(
                    self.hass, f'{reply}', f'Poe chat reply', f'{DOMAIN}-reply',
                )
            self.hass.bus.async_fire(f'{DOMAIN}.reply', reply)
        return reply


class PoeClient(poe.Client):
    gql_headers = None
    next_data = None
    channel = None
    formkey = None
    bots = None
    bot_names = None
    ws_connected = None

    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self.entry_id = config.get(CONF_ENTITY_ID)
        if base := config.get(CONF_BASE):
            poe_home = 'https://poe.com/'
            self.home_url = base.rstrip('/') + '/'
            self.gql_url = self.gql_url.replace(poe_home, self.home_url)
            self.gql_recv_url = self.gql_recv_url.replace(poe_home, self.home_url)
            self.settings_url = self.settings_url.replace(poe_home, self.home_url)

        self.session = requests.Session()
        self.token = config.get(CONF_TOKEN)
        self.proxy = config.get('proxy')
        if self.proxy:
            self.session.proxies = {
                "http": self.proxy,
                "https": self.proxy,
            }
            _LOGGER.info(f"Proxy enabled: {self.proxy}")

        self.active_messages = {}
        self.message_queues = {}

        self.session.cookies.set("p-b", self.token)
        self.headers = {
            "User-Agent": config.get('user_agent') or USER_AGENT,
            "Referrer": "https://poe.com/",
            "Origin": "https://poe.com",
        }
        self.session.headers.update(self.headers)
        self.ws_domain = f"tch{random.randint(1, int(1e6))}"

    def init(self):
        self.next_data = self.get_next_data(overwrite_vars=True)
        self.channel = self.get_channel_data()
        self.gql_headers = {
            **self.headers,
            "poe-formkey": self.formkey,
            "poe-tchannel": self.channel["channel"],
        }
        self.connect_ws()
        self.bots = self.get_bots(download_next_data=False)
        self.bot_names = self.get_bot_names()
        self.subscribe()
        _LOGGER.warning('Init client: %s', [
            self.session.cookies, self.get_websocket_url(), self.channel, self.bot_names,
        ])

    def send(self, **kwargs):
        bot = kwargs.get(CONF_BOT) or self.config.get(CONF_BOT) or 'capybara'
        msg = kwargs.get('message')
        if not msg:
            return None

        reply = None
        try:
            for chunk in self.send_message(bot, msg):
                reply = {
                    **kwargs,
                    **chunk,
                }
                self.hass.bus.async_fire(f'{DOMAIN}.reply_chunk', reply)
        except Exception as exc:
            _LOGGER.error('Error sending message: %s', [kwargs, type(exc), exc])
        if not reply:
            self.disconnect_ws()
            self.connect_ws()
        return reply

    def get_bot(self, display_name):
        url = f'{self.home_url.rstrip("/")}/_next/data/{self.next_data["buildId"]}/{display_name}.json'
        r = poe.request_with_retries(self.session.get, url)
        chat_data = r.json()["pageProps"]["payload"]["chatOfBotDisplayName"]
        return chat_data
