"""Support for Xiaomi Yeelight WiFi color bulb."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, ValuesView
import contextlib
from datetime import datetime
from ipaddress import IPv4Address
import logging
from urllib.parse import urlparse

import async_timeout
from async_upnp_client.search import SsdpSearchListener
from async_upnp_client.utils import CaseInsensitiveDict

from homeassistant import config_entries
from homeassistant.components import network, ssdp
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    DISCOVERY_ATTEMPTS,
    DISCOVERY_INTERVAL,
    DISCOVERY_SEARCH_INTERVAL,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    SSDP_ST,
    SSDP_TARGET,
)

_LOGGER = logging.getLogger(__name__)


class YeelightScanner:
    """Scan for Yeelight devices."""

    _scanner: YeelightScanner | None = None

    @classmethod
    @callback
    def async_get(cls, hass: HomeAssistant) -> YeelightScanner:
        """Get scanner instance."""
        if cls._scanner is None:
            cls._scanner = cls(hass)
        return cls._scanner

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize class."""
        self._hass = hass
        self._host_discovered_events: dict[str, list[asyncio.Event]] = {}
        self._unique_id_capabilities: dict[str, CaseInsensitiveDict] = {}
        self._host_capabilities: dict[str, CaseInsensitiveDict] = {}
        self._track_interval: CALLBACK_TYPE | None = None
        self._listeners: list[SsdpSearchListener] = []
        self._connected_events: list[asyncio.Event] = []

    async def async_setup(self) -> None:
        """Set up the scanner."""
        if self._connected_events:
            await self._async_wait_connected()
            return

        for idx, source_ip in enumerate(await self._async_build_source_set()):
            self._connected_events.append(asyncio.Event())

            def _wrap_async_connected_idx(idx) -> Callable[[], None]:
                """Create a function to capture the idx cell variable."""

                @callback
                def _async_connected() -> None:
                    self._connected_events[idx].set()

                return _async_connected

            source = (str(source_ip), 0)
            self._listeners.append(
                SsdpSearchListener(
                    callback=self._async_process_entry,
                    search_target=SSDP_ST,
                    target=SSDP_TARGET,
                    source=source,
                    connect_callback=_wrap_async_connected_idx(idx),
                )
            )

        results = await asyncio.gather(
            *(listener.async_start() for listener in self._listeners),
            return_exceptions=True,
        )
        failed_listeners = []
        for idx, result in enumerate(results):
            if not isinstance(result, Exception):
                continue
            _LOGGER.warning(
                "Failed to setup listener for %s: %s",
                self._listeners[idx].source,
                result,
            )
            failed_listeners.append(self._listeners[idx])
            self._connected_events[idx].set()

        for listener in failed_listeners:
            self._listeners.remove(listener)

        await self._async_wait_connected()
        self._track_interval = async_track_time_interval(
            self._hass, self.async_scan, DISCOVERY_INTERVAL
        )
        self.async_scan()

    async def _async_wait_connected(self):
        """Wait for the listeners to be up and connected."""
        await asyncio.gather(*(event.wait() for event in self._connected_events))

    async def _async_build_source_set(self) -> set[IPv4Address]:
        """Build the list of ssdp sources."""
        adapters = await network.async_get_adapters(self._hass)
        sources: set[IPv4Address] = set()
        if network.async_only_default_interface_enabled(adapters):
            sources.add(IPv4Address("0.0.0.0"))
            return sources

        return {
            source_ip
            for source_ip in await network.async_get_enabled_source_ips(self._hass)
            if isinstance(source_ip, IPv4Address) and not source_ip.is_loopback
        }

    async def async_discover(self) -> ValuesView[CaseInsensitiveDict]:
        """Discover bulbs."""
        _LOGGER.debug("Yeelight discover with interval %s", DISCOVERY_SEARCH_INTERVAL)
        await self.async_setup()
        for _ in range(DISCOVERY_ATTEMPTS):
            self.async_scan()
            await asyncio.sleep(DISCOVERY_SEARCH_INTERVAL.total_seconds())
        return self._unique_id_capabilities.values()

    @callback
    def async_scan(self, _: datetime | None = None) -> None:
        """Send discovery packets."""
        _LOGGER.debug("Yeelight scanning")
        for listener in self._listeners:
            listener.async_search()

    async def async_get_capabilities(self, host: str) -> CaseInsensitiveDict | None:
        """Get capabilities via SSDP."""
        if host in self._host_capabilities:
            return self._host_capabilities[host]

        host_event = asyncio.Event()
        self._host_discovered_events.setdefault(host, []).append(host_event)
        await self.async_setup()

        for listener in self._listeners:
            listener.async_search((host, SSDP_TARGET[1]))

        with contextlib.suppress(asyncio.TimeoutError):
            async with async_timeout.timeout(DISCOVERY_TIMEOUT):
                await host_event.wait()

        self._host_discovered_events[host].remove(host_event)
        return self._host_capabilities.get(host)

    def _async_discovered_by_ssdp(self, response: CaseInsensitiveDict) -> None:
        @callback
        def _async_start_flow(*_) -> None:
            discovery_flow.async_create_flow(
                self._hass,
                DOMAIN,
                context={"source": config_entries.SOURCE_SSDP},
                data=ssdp.SsdpServiceInfo(
                    ssdp_usn="",
                    ssdp_st=SSDP_ST,
                    ssdp_headers=response,
                    upnp={},
                ),
            )

        # Delay starting the flow in case the discovery is the result
        # of another discovery
        async_call_later(self._hass, 1, _async_start_flow)

    @callback
    def _async_process_entry(self, headers: CaseInsensitiveDict) -> None:
        """Process a discovery."""
        _LOGGER.debug("Discovered via SSDP: %s", headers)
        unique_id = headers["id"]
        host = urlparse(headers["location"]).hostname
        assert host
        current_entry = self._unique_id_capabilities.get(unique_id)
        # Make sure we handle ip changes
        if not current_entry or host != urlparse(current_entry["location"]).hostname:
            _LOGGER.debug("Yeelight discovered with %s", headers)
            self._async_discovered_by_ssdp(headers)
        self._host_capabilities[host] = headers
        self._unique_id_capabilities[unique_id] = headers
        for event in self._host_discovered_events.get(host, []):
            event.set()
