"""The component."""
import logging
import asyncio
import datetime
import requests
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
            await acc.async_disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, unload)
    )
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    if entry.entry_id not in hass.data[DOMAIN].get(CONF_ACCOUNTS, {}):
        return False
    client = await get_client_from_config(hass, entry)
    await client.async_disconnect()
    return True


async def async_reload_integration_config(hass, config):
    for client in hass.data[DOMAIN].get(CONF_ACCOUNTS, {}).values():
        await client.async_reconnect()
    return config


async def get_client_from_config(hass, config, renew=False):
    init_integration_data(hass)
    if isinstance(config, ConfigEntry):
        cfg = {
            **config.data,
            **config.options,
            'entry': config,
            CONF_ENTITY_ID: config.entry_id,
        }
    else:
        cfg = {
            **config,
            CONF_ENTITY_ID: None,
        }

    if eid := cfg.get(CONF_ENTITY_ID):
        client = hass.data[DOMAIN][CONF_ACCOUNTS].get(eid)
        if renew:
            await client.async_disconnect()
        elif client:
            return client

    client = PoeClient(hass, cfg)
    await client.async_init()
    if eid:
        hass.data[DOMAIN][CONF_ACCOUNTS][eid] = client

    return client


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
                vol.Optional('conversation_id'): cv.string,
                vol.Optional('extra'): cv.match_all,
                vol.Optional('throw', default=False): cv.boolean,
                vol.Optional('throw_chunk', default=False): cv.boolean,
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
        acc = None
        for acc in self.hass.data[DOMAIN][CONF_ACCOUNTS].values():
            if nam in [acc.name, acc.entry_id]:
                break
        if not isinstance(acc, PoeClient):
            _LOGGER.warning('Account %s not found in %s.', nam, self.hass.data[DOMAIN].get(CONF_ACCOUNTS))
            return False
        reply = await acc.async_send(**dat)
        if reply:
            if dat.get('throw', False):
                persistent_notification.async_create(
                    self.hass, f'{reply}', f'Poe chat reply', f'{DOMAIN}-reply',
                )
        return reply


class PoeClient(poe.Client):
    gql_headers = None
    next_data = None
    channel = None
    formkey = None
    bots = None
    bot_names = None
    ws_domain = None
    ws_connected = None
    throw_split = '\n\n------\n\n'

    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self.name = config.get(CONF_NAME)
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
            "User-Agent": config.get("user_agent") or USER_AGENT,
            "Referrer": "https://poe.com/",
            "Origin": "https://poe.com",
        }
        self.session.headers.update(self.headers)

    async def async_init(self):
        return await self.hass.async_add_executor_job(partial(self.init))

    def init(self):
        try:
            self.setup_connection()
            self.connect_ws()
            _LOGGER.info('Init client: %s', [
                self.session.cookies, self.get_websocket_url(), self.channel, self.bot_names,
            ])
        except RuntimeError as exc:
            _LOGGER.error('Init error: %s', [
                exc, self.session.cookies, self.get_websocket_url(), self.channel, self.gql_headers, self.bot_names,
            ])
            raise exc

    async def async_send(self, **kwargs):
        return await self.hass.async_add_executor_job(partial(self.send, **kwargs))

    def send(self, **kwargs):
        bot = kwargs.get(CONF_BOT) or self.config.get(CONF_BOT) or 'capybara'
        msg = kwargs.get('message')
        ext = kwargs.get('extra') or {}
        if not msg:
            return None

        reply = None
        throw_chunk = kwargs.get('throw_chunk', False)
        throw = kwargs.get('throw', throw_chunk)
        try:
            txt = ''
            siz = int(ext.get('chunk_size', 2) or 1)
            for chunk in self.send_message(bot, msg):
                eof = True
                new = chunk.get('text_new', '')
                txt += new
                if eof and siz:
                    eof = len(txt) >= siz
                if eof and ext.get('chunk_line', False):
                    eof = txt.endswith('\n')
                if eof and ext.get('chunk_code', False) and '```' in txt:
                    eof = txt.endswith('```\n') and txt != new
                reply = {
                    **kwargs,
                    **chunk,
                    'text_new': txt,
                }
                if eof:
                    txt = ''
                    self.reply_chunk(msg, reply, throw_chunk=throw_chunk)
            if txt and reply:
                self.reply_chunk(msg, reply, throw_chunk=throw_chunk)
            if reply:
                self.hass.bus.async_fire(f'{DOMAIN}.reply', reply)
                if throw:
                    persistent_notification.async_create(
                        self.hass, f'{msg}{self.throw_split}{reply.get("linkifiedText")}',
                        f'Poe chat reply', f'{DOMAIN}-reply',
                    )
        except Exception as exc:
            _LOGGER.error('Error sending message: %s', [kwargs, type(exc), exc])
            self.hass.bus.async_fire(f'{DOMAIN}.reply_error', {
                **kwargs,
                'error': str(exc) or type(exc),
            })
            if throw:
                persistent_notification.async_create(
                    self.hass, f'{msg}{self.throw_split}{exc}',
                    f'Poe chat error', f'{DOMAIN}-reply',
                )
        if not reply:
            self.reconnect()
        return reply

    def reply_chunk(self, msg, reply, throw_chunk=False):
        self.hass.bus.async_fire(f'{DOMAIN}.reply_chunk', reply)
        if throw_chunk:
            persistent_notification.async_create(
                self.hass, f'{msg}{self.throw_split}{reply.get("linkifiedText")}',
                f'Poe chat reply', f'{DOMAIN}-reply',
            )

    async def async_reconnect(self):
        await self.async_disconnect()
        await self.async_init()

    def reconnect(self):
        if self.ws_connected:
            self.disconnect_ws()
        self.init()

    async def async_disconnect(self):
        if self.ws_connected:
            await self.hass.async_add_executor_job(self.disconnect_ws)

    def get_bot(self, display_name):
        url = f'{self.home_url.rstrip("/")}/_next/data/{self.next_data["buildId"]}/{display_name}.json'
        r = poe.request_with_retries(self.session.get, url)
        chat_data = r.json()["pageProps"]["payload"]["chatOfBotDisplayName"]
        return chat_data
