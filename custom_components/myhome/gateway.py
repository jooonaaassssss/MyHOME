"""Code to handle a MyHome Gateway."""
import asyncio
import contextlib
import random
from typing import Dict, List

from homeassistant.const import (
    CONF_ENTITIES,
    CONF_HOST,
    CONF_PORT,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_MAC,
    CONF_FRIENDLY_NAME,
)
from homeassistant.components.light import DOMAIN as LIGHT
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.sensor import (
    DOMAIN as SENSOR,
)

from OWNd.connection import OWNSession, OWNEventSession, OWNCommandSession, OWNGateway
from OWNd.message import (
    OWNMessage,
    OWNLightingEvent,
    OWNLightingCommand,
    OWNEnergyEvent,
    OWNAutomationEvent,
    OWNDryContactEvent,
    OWNAuxEvent,
    OWNHeatingEvent,
    OWNHeatingCommand,
    OWNCENPlusEvent,
    OWNCENEvent,
    OWNGatewayEvent,
    OWNGatewayCommand,
    OWNCommand,
)

from .const import (
    CONF_PLATFORMS,
    CONF_FIRMWARE,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_DEVICE_TYPE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_UDN,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    CONF_LONG_PRESS,
    CONF_LONG_RELEASE,
    DOMAIN,
    LOGGER,
)
from .myhome_device import MyHOMEEntity
from .button import (
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
)


# Delay before each reconnection attempt, in seconds. The last value repeats.
RECONNECT_DELAYS = (5, 15, 30, 60, 120, 300)


def reconnect_delay(failures: int) -> float:
    """Return the backoff for the given number of consecutive failures.

    A little jitter is added so several gateways, or several workers on one
    gateway, do not all retry in the same instant.
    """
    base = RECONNECT_DELAYS[min(failures, len(RECONNECT_DELAYS)) - 1]
    return base + random.uniform(0, base * 0.1)


class MyHOMEGatewayHandler:
    """Manages a single MyHOME Gateway."""

    def __init__(self, hass, config_entry, generate_events=False):
        build_info = {
            "address": config_entry.data[CONF_HOST],
            "port": config_entry.data[CONF_PORT],
            "password": config_entry.data[CONF_PASSWORD],
            "ssdp_location": config_entry.data[CONF_SSDP_LOCATION],
            "ssdp_st": config_entry.data[CONF_SSDP_ST],
            "deviceType": config_entry.data[CONF_DEVICE_TYPE],
            "friendlyName": config_entry.data[CONF_FRIENDLY_NAME],
            "manufacturer": config_entry.data[CONF_MANUFACTURER],
            "manufacturerURL": config_entry.data[CONF_MANUFACTURER_URL],
            "modelName": config_entry.data[CONF_NAME],
            "modelNumber": config_entry.data[CONF_FIRMWARE],
            "serialNumber": config_entry.data[CONF_MAC],
            "UDN": config_entry.data[CONF_UDN],
        }
        self.hass = hass
        self.config_entry = config_entry
        self.generate_events = generate_events
        self.gateway = OWNGateway(build_info)
        self._terminate_listener = False
        self._terminate_sender = False
        self.is_connected = False
        self.listening_worker: asyncio.tasks.Task = None
        # Registry id of the gateway's own device, filled in by async_setup_entry
        # once the device exists. Entities link to it via via_device_id.
        self.device_registry_id: str = None
        self.sending_workers: List[asyncio.tasks.Task] = []
        self.send_buffer = asyncio.Queue()

    @property
    def mac(self) -> str:
        return self.gateway.serial

    @property
    def unique_id(self) -> str:
        return self.mac

    @property
    def log_id(self) -> str:
        return self.gateway.log_id

    @property
    def manufacturer(self) -> str:
        return self.gateway.manufacturer

    @property
    def name(self) -> str:
        return f"{self.gateway.model_name} Gateway"

    @property
    def model(self) -> str:
        return self.gateway.model_name

    @property
    def firmware(self) -> str:
        return self.gateway.firmware

    async def test(self) -> Dict:
        """Probe the gateway. A successful probe is evidence that it is reachable."""
        results = await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()
        self._set_connected(bool(results.get("Success")))
        return results

    def _set_connected(self, connected: bool) -> None:
        """Record the connection state and refresh the entities that show it."""
        if self.is_connected == connected:
            return
        self.is_connected = connected
        for _platform in self.hass.data[DOMAIN].get(self.mac, {}).get(CONF_PLATFORMS, {}).values():
            for _device in _platform.values():
                for _entity in _device.get(CONF_ENTITIES, {}).values():
                    if isinstance(_entity, MyHOMEEntity) and _entity.entity_id:
                        _entity.async_write_ha_state()

    async def _close_session(self, session) -> None:
        """Close a session without letting that failure mask the original one."""
        try:
            await session.close()
        except Exception:
            LOGGER.debug(
                "%s Session did not close cleanly.", self.log_id, exc_info=True
            )

    async def listening_loop(self):
        """Keep an event session running for as long as the listener is wanted.

        ``_listen`` returns only when termination was requested; every other way
        out is an exception. That exception used to end the task for good, and
        because nothing awaits the task it was not even reported: commands kept
        working over the separate command session while no state update ever
        arrived again until Home Assistant was restarted. Reconnect instead.
        """
        self._terminate_listener = False
        failures = 0

        while not self._terminate_listener:
            _event_session = OWNEventSession(gateway=self.gateway, logger=LOGGER)
            try:
                await self._listen(_event_session)
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # Only a real failure makes the gateway unavailable. A cancelled
                # worker is a deliberate restart or unload, and flipping several
                # hundred entities to unavailable for the second that takes would
                # be noise, not information.
                self._set_connected(False)
                failures += 1
                delay = reconnect_delay(failures)
                if failures == 1:
                    LOGGER.warning(
                        "%s Event listener stopped: %s. Reconnecting in %.0f seconds.",
                        self.log_id,
                        err,
                        delay,
                    )
                else:
                    LOGGER.debug(
                        "%s Event listener still down after %s attempts: %s. "
                        "Retrying in %.0f seconds.",
                        self.log_id,
                        failures,
                        err,
                        delay,
                    )
            finally:
                await self._close_session(_event_session)

            if self._terminate_listener:
                break
            await asyncio.sleep(delay)

        LOGGER.debug("%s Listening worker stopped.", self.log_id)

    async def _listen(self, _event_session: OWNEventSession):
        LOGGER.debug("%s Creating listening worker.", self.log_id)

        await _event_session.connect()
        LOGGER.info("%s Event listener connected.", self.log_id)
        self._set_connected(True)

        while not self._terminate_listener:
            message = await _event_session.get_next()
            LOGGER.debug("%s Message received: `%s`", self.log_id, message)

            if self.generate_events:
                if isinstance(message, OWNMessage):
                    _event_content = {"gateway": str(self.gateway.host)}
                    _event_content.update(message.event_content)
                    self.hass.bus.async_fire("myhome_message_event", _event_content)
                else:
                    self.hass.bus.async_fire("myhome_message_event", {"gateway": str(self.gateway.host), "message": str(message)})

            if not isinstance(message, OWNMessage):
                LOGGER.warning(
                    "%s Data received is not a message: `%s`",
                    self.log_id,
                    message,
                )
            elif isinstance(message, OWNEnergyEvent):
                if SENSOR in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS] and message.entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR]:
                    for _entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR][message.entity][CONF_ENTITIES]:
                        if isinstance(
                            self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR][message.entity][CONF_ENTITIES][_entity],
                            MyHOMEEntity,
                        ):
                            self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR][message.entity][CONF_ENTITIES][_entity].handle_event(message)
                else:
                    continue
            elif (
                isinstance(message, OWNLightingEvent)
                or isinstance(message, OWNAutomationEvent)
                or isinstance(message, OWNDryContactEvent)
                or isinstance(message, OWNAuxEvent)
                or isinstance(message, OWNHeatingEvent)
            ):
                if not message.is_translation:
                    is_event = False
                    if isinstance(message, OWNLightingEvent):
                        if message.is_general:
                            is_event = True
                            event = "on" if message.is_on else "off"
                            self.hass.bus.async_fire(
                                "myhome_general_light_event",
                                {"message": str(message), "event": event},
                            )
                            await asyncio.sleep(0.1)
                            await self.send_status_request(OWNLightingCommand.status("0"))
                        elif message.is_area:
                            is_event = True
                            event = "on" if message.is_on else "off"
                            self.hass.bus.async_fire(
                                "myhome_area_light_event",
                                {
                                    "message": str(message),
                                    "area": message.area,
                                    "event": event,
                                },
                            )
                            await asyncio.sleep(0.1)
                            await self.send_status_request(OWNLightingCommand.status(message.area))
                        elif message.is_group:
                            is_event = True
                            event = "on" if message.is_on else "off"
                            self.hass.bus.async_fire(
                                "myhome_group_light_event",
                                {
                                    "message": str(message),
                                    "group": message.group,
                                    "event": event,
                                },
                            )
                    elif isinstance(message, OWNAutomationEvent):
                        if message.is_general:
                            is_event = True
                            if message.is_opening and not message.is_closing:
                                event = "open"
                            elif message.is_closing and not message.is_opening:
                                event = "close"
                            else:
                                event = "stop"
                            self.hass.bus.async_fire(
                                "myhome_general_automation_event",
                                {"message": str(message), "event": event},
                            )
                        elif message.is_area:
                            is_event = True
                            if message.is_opening and not message.is_closing:
                                event = "open"
                            elif message.is_closing and not message.is_opening:
                                event = "close"
                            else:
                                event = "stop"
                            self.hass.bus.async_fire(
                                "myhome_area_automation_event",
                                {
                                    "message": str(message),
                                    "area": message.area,
                                    "event": event,
                                },
                            )
                        elif message.is_group:
                            is_event = True
                            if message.is_opening and not message.is_closing:
                                event = "open"
                            elif message.is_closing and not message.is_opening:
                                event = "close"
                            else:
                                event = "stop"
                            self.hass.bus.async_fire(
                                "myhome_group_automation_event",
                                {
                                    "message": str(message),
                                    "group": message.group,
                                    "event": event,
                                },
                            )
                    if not is_event:
                        if isinstance(message, OWNLightingEvent) and message.brightness_preset:
                            if isinstance(
                                self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][LIGHT][message.entity][CONF_ENTITIES][LIGHT],
                                MyHOMEEntity,
                            ):
                                await self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][LIGHT][message.entity][CONF_ENTITIES][LIGHT].async_update()
                        else:
                            for _platform in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS]:
                                if _platform != BUTTON and message.entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform]:
                                    for _entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES]:
                                        if (
                                            isinstance(
                                                self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity],
                                                MyHOMEEntity,
                                            )
                                            and not isinstance(
                                                self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity],
                                                DisableCommandButtonEntity,
                                            )
                                            and not isinstance(
                                                self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity],
                                                EnableCommandButtonEntity,
                                            )
                                        ):
                                            self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity].handle_event(message)

                else:
                    LOGGER.debug(
                        "%s Ignoring translation message `%s`",
                        self.log_id,
                        message,
                    )
            elif isinstance(message, OWNHeatingCommand) and message.dimension is not None and message.dimension == 14:
                where = message.where[1:] if message.where.startswith("#") else message.where
                LOGGER.debug(
                    "%s Received heating command, sending query to zone %s",
                    self.log_id,
                    where,
                )
                await self.send_status_request(OWNHeatingCommand.status(where))
            elif isinstance(message, OWNCENPlusEvent):
                event = None
                if message.is_short_pressed:
                    event = CONF_SHORT_PRESS
                elif message.is_held or message.is_still_held:
                    event = CONF_LONG_PRESS
                elif message.is_released:
                    event = CONF_LONG_RELEASE
                else:
                    event = None
                self.hass.bus.async_fire(
                    "myhome_cenplus_event",
                    {
                        "object": int(message.object),
                        "pushbutton": int(message.push_button),
                        "event": event,
                    },
                )
                LOGGER.info(
                    "%s %s",
                    self.log_id,
                    message.human_readable_log,
                )
            elif isinstance(message, OWNCENEvent):
                event = None
                if message.is_pressed:
                    event = CONF_SHORT_PRESS
                elif message.is_released_after_short_press:
                    event = CONF_SHORT_RELEASE
                elif message.is_held:
                    event = CONF_LONG_PRESS
                elif message.is_released_after_long_press:
                    event = CONF_LONG_RELEASE
                else:
                    event = None
                self.hass.bus.async_fire(
                    "myhome_cen_event",
                    {
                        "object": int(message.object),
                        "pushbutton": int(message.push_button),
                        "event": event,
                    },
                )
                LOGGER.info(
                    "%s %s",
                    self.log_id,
                    message.human_readable_log,
                )
            elif isinstance(message, OWNGatewayEvent) or isinstance(message, OWNGatewayCommand):
                LOGGER.info(
                    "%s %s",
                    self.log_id,
                    message.human_readable_log,
                )
            else:
                LOGGER.info(
                    "%s Unsupported message type: `%s`",
                    self.log_id,
                    message,
                )

    async def sending_loop(self, worker_id: int):
        """Keep a command worker running for as long as it is wanted.

        Mirrors ``listening_loop``. A command worker that died left the
        integration unable to send anything at all, which is worse than losing
        the status feed, so it reconnects on the same schedule.
        """
        self._terminate_sender = False
        failures = 0

        while not self._terminate_sender:
            _command_session = OWNCommandSession(gateway=self.gateway, logger=LOGGER)
            try:
                await self._send_commands(worker_id, _command_session)
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:
                failures += 1
                delay = reconnect_delay(failures)
                if failures == 1:
                    LOGGER.warning(
                        "%s Command worker %s stopped: %s. Reconnecting in %.0f seconds.",
                        self.log_id,
                        worker_id,
                        err,
                        delay,
                    )
                else:
                    LOGGER.debug(
                        "%s Command worker %s still down after %s attempts: %s. "
                        "Retrying in %.0f seconds.",
                        self.log_id,
                        worker_id,
                        failures,
                        err,
                        delay,
                    )
            finally:
                await self._close_session(_command_session)

            if self._terminate_sender:
                break
            await asyncio.sleep(delay)

        LOGGER.debug("%s Sending worker %s stopped.", self.log_id, worker_id)

    async def _send_commands(self, worker_id: int, _command_session: OWNCommandSession):
        LOGGER.debug(
            "%s Creating sending worker %s",
            self.log_id,
            worker_id,
        )

        await _command_session.connect()

        while not self._terminate_sender:
            task = await self.send_buffer.get()
            LOGGER.debug(
                "%s Message `%s` was successfully unqueued by worker %s.",
                self.log_id,
                task["message"],
                worker_id,
            )
            await _command_session.send(message=task["message"], is_status_request=task["is_status_request"])
            self.send_buffer.task_done()

    async def close_listener(self) -> bool:
        """Stop the event listener and the command workers, and wait for them."""
        LOGGER.debug("%s Closing event listener and command workers", self.log_id)
        self._terminate_sender = True
        self._terminate_listener = True

        await self._stop_worker(self.listening_worker)
        self.listening_worker = None
        for worker in self.sending_workers:
            await self._stop_worker(worker)
        self.sending_workers.clear()
        self._set_connected(False)

        return True

    async def close_listener_only(self) -> bool:
        """Stop the event listener and wait until its worker is really gone.

        Raising the flag on its own is not enough: the loop is parked in
        ``get_next()`` and only re-reads the flag after the next bus message, so a
        restart could start a second listener on the same connection while the old
        one was still running.
        """
        LOGGER.debug("%s Closing event listener only", self.log_id)
        self._terminate_listener = True
        await self._stop_worker(self.listening_worker)
        self.listening_worker = None
        return True

    async def _stop_worker(self, worker: asyncio.Task | None) -> None:
        """Cancel a worker task and wait for it to finish."""
        if worker is None or worker.done():
            return
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    async def send(self, message: OWNCommand):
        await self.send_buffer.put({"message": message, "is_status_request": False})
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )

    async def send_status_request(self, message: OWNCommand):
        await self.send_buffer.put({"message": message, "is_status_request": True})
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )
