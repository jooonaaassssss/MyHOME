""" MyHOME integration. """

import yaml
from voluptuous import Invalid

from OWNd.message import OWNCommand, OWNGatewayCommand

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import device_registry as dr, entity_registry as er, config_validation as cv
from homeassistant.const import CONF_MAC

from .const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITIES,
    CONF_WORKER_COUNT,
    CONF_FILE_PATH,
    CONF_GENERATE_EVENTS,
    DOMAIN,
    LOGGER,
)
from .validate import config_schema, format_mac
from .gateway import MyHOMEGatewayHandler

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    """Set up the MyHOME component.

    CONFIG_SCHEMA already rejects a myhome: block in configuration.yaml, so
    there is nothing to check here beyond preparing the shared store.
    """
    hass.data.setdefault(DOMAIN, {})
    return True


def _async_prune_registry(hass, entry, gateway_device_entry) -> None:
    """Drop registry entries that the configuration file no longer describes."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    # Pruning lose entities and devices from the registry
    entity_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)

    entities_to_be_removed = []
    devices_to_be_removed = [
        device_entry.id
        for device_entry in dr.async_entries_for_config_entry(
            device_registry, entry.entry_id
        )
    ]

    configured_entities = []

    for _platform in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys():
        for _device in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][
            _platform
        ].keys():
            for _entity_name in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][
                _platform
            ][_device][CONF_ENTITIES]:
                if _entity_name != _platform:
                    configured_entities.append(
                        f"{entry.data[CONF_MAC]}-{_device}-{_entity_name}"
                    )  # extrapolating _attr_unique_id out of the entity's place in the config data structure
                else:
                    configured_entities.append(
                        f"{entry.data[CONF_MAC]}-{_device}"
                    )  # extrapolating _attr_unique_id out of the entity's place in the config data structure

    for entity_entry in entity_entries:
        if entity_entry.unique_id in configured_entities:
            if entity_entry.device_id in devices_to_be_removed:
                devices_to_be_removed.remove(entity_entry.device_id)
            continue
        entities_to_be_removed.append(entity_entry.entity_id)

    for enity_id in entities_to_be_removed:
        entity_registry.async_remove(enity_id)

    if gateway_device_entry.id in devices_to_be_removed:
        devices_to_be_removed.remove(gateway_device_entry.id)

    for device_id in devices_to_be_removed:
        if (
            len(
                er.async_entries_for_device(
                    entity_registry, device_id, include_disabled_entities=True
                )
            )
            == 0
        ):
            device_registry.async_remove_device(device_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    _config_file_path = (
        str(entry.options[CONF_FILE_PATH])
        if CONF_FILE_PATH in entry.options
        else "/config/myhome.yaml"
    )
    _generate_events = (
        entry.options[CONF_GENERATE_EVENTS]
        if CONF_GENERATE_EVENTS in entry.options
        else False
    )

    def _load_config():
        """Read and validate the configuration file off the event loop."""
        with open(_config_file_path, encoding="utf-8") as yaml_file:
            return config_schema(yaml.safe_load(yaml_file))

    try:
        _validated_config = await hass.async_add_executor_job(_load_config)
    except FileNotFoundError as err:
        raise ConfigEntryError(
            f"Configuration file '{_config_file_path}' is not present."
        ) from err
    except (Invalid, yaml.YAMLError) as err:
        raise ConfigEntryError(
            f"Configuration file '{_config_file_path}' is not valid: {err}"
        ) from err

    if entry.data[CONF_MAC] not in _validated_config:
        raise ConfigEntryError(
            f"Gateway {entry.data[CONF_MAC]} is not configured in "
            f"'{_config_file_path}'."
        )

    hass.data[DOMAIN][entry.data[CONF_MAC]] = _validated_config[entry.data[CONF_MAC]]

    # Migrating the config entry's unique_id if it was not formated to the recommended hass standard
    if entry.unique_id != dr.format_mac(entry.unique_id):
        hass.config_entries.async_update_entry(
            entry, unique_id=dr.format_mac(entry.unique_id)
        )
        LOGGER.warning("Migrating config entry unique_id to %s", entry.unique_id)

    gateway_handler = MyHOMEGatewayHandler(
        hass=hass, config_entry=entry, generate_events=_generate_events
    )
    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY] = gateway_handler

    try:
        tests_results = await gateway_handler.test()
    except OSError as ose:
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        raise ConfigEntryNotReady(
            f"Gateway cannot be reached at {gateway_handler.gateway.host}, "
            "make sure its address is correct."
        ) from ose

    if not tests_results["Success"]:
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        if tests_results["Message"] in ("password_error", "password_required"):
            raise ConfigEntryAuthFailed(
                f"Gateway {entry.data[CONF_MAC]} rejected the configured password."
            )
        raise ConfigEntryNotReady(
            f"Gateway {entry.data[CONF_MAC]} is not ready: {tests_results['Message']}"
        )

    _command_worker_count = (
        int(entry.options[CONF_WORKER_COUNT])
        if CONF_WORKER_COUNT in entry.options
        else 1
    )

    device_registry = dr.async_get(hass)

    gateway_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        identifiers={(DOMAIN, gateway_handler.unique_id)},
        manufacturer=gateway_handler.manufacturer,
        name=gateway_handler.name,
        model=gateway_handler.model,
        sw_version=gateway_handler.firmware,
    )
    gateway_handler.device_registry_id = gateway_device_entry.id

    _platforms = list(hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS])

    await hass.config_entries.async_forward_entry_setups(entry, _platforms)

    try:
        gateway_handler.listening_worker = hass.loop.create_task(
            gateway_handler.listening_loop()
        )
        for i in range(_command_worker_count):
            gateway_handler.sending_workers.append(
                hass.loop.create_task(gateway_handler.sending_loop(i))
            )

        _async_prune_registry(hass, entry, gateway_device_entry)
    except Exception:
        # The platforms are already forwarded at this point. Letting the failure
        # escape as-is would leave the entry half set up, and the retry would
        # abort with "Config entry has already been setup".
        await gateway_handler.close_listener()
        await hass.config_entries.async_unload_platforms(entry, _platforms)
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        raise

    # Defining the services

    # Restart the event listener when the gateway re-announces itself over SSDP.
    async def _handle_force_restart_event_listener(event):
        handler = hass.data[DOMAIN].get(entry.data[CONF_MAC], {}).get(CONF_ENTITY)
        if handler is None:
            LOGGER.warning(
                "No gateway handler found, cannot restart the event listener."
            )
            return

        LOGGER.debug("Restarting the event listener after an SSDP announcement.")
        await handler.close_listener_only()
        handler.listening_worker = entry.async_create_background_task(
            hass,
            handler.listening_loop(),
            name=f"{DOMAIN} {entry.data[CONF_MAC]} event listener",
        )

    # async_listen returns the unsubscribe callback; without registering it the
    # handler survives the unload and every reload stacks another one, so a
    # single SSDP announcement would restart the listener once per reload.
    entry.async_on_unload(
        hass.bus.async_listen(
            "myhome_force_restart_event_listener",
            _handle_force_restart_event_listener,
        )
    )


    async def handle_sync_time(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send time synchronisation message.",
                    gateway,
                )
                return False
            else:
                gateway = mac
        timezone = hass.config.as_dict()["time_zone"]
        if gateway in hass.data[DOMAIN]:
            await hass.data[DOMAIN][gateway][CONF_ENTITY].send(
                OWNGatewayCommand.set_datetime_to_now(timezone)
            )
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send time synchronisation message.",
                gateway,
            )
            return False

    hass.services.async_register(DOMAIN, "sync_time", handle_sync_time)

    async def handle_send_message(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        message = call.data.get(ATTR_MESSAGE, None)
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send message `%s`.",
                    gateway,
                    message,
                )
                return False
            else:
                gateway = mac
        LOGGER.debug("Handling message `%s` to be sent to `%s`", message, gateway)
        if gateway in hass.data[DOMAIN]:
            if message is not None:
                own_message = OWNCommand.parse(message)
                if own_message is not None:
                    if own_message.is_valid:
                        LOGGER.debug(
                            "%s Sending valid OpenWebNet Message: `%s`",
                            hass.data[DOMAIN][gateway][CONF_ENTITY].log_id,
                            own_message,
                        )
                        await hass.data[DOMAIN][gateway][CONF_ENTITY].send(own_message)
                else:
                    LOGGER.error(
                        "Could not parse message `%s`, not sending it.", message
                    )
                    return False
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send message `%s`.", gateway, message
            )
            return False

    hass.services.async_register(DOMAIN, "send_message", handle_send_message)

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    LOGGER.debug("Unloading MyHOME config entry.")

    if not await hass.config_entries.async_unload_platforms(
        entry, list(hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS])
    ):
        LOGGER.warning("Could not unload all MyHOME platforms, keeping the entry.")
        return False

    gateway_handler = hass.data[DOMAIN][entry.data[CONF_MAC]].pop(CONF_ENTITY)
    del hass.data[DOMAIN][entry.data[CONF_MAC]]

    # Every entry registers the services, so only the last gateway to leave may
    # take them down again.
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, "sync_time")
        hass.services.async_remove(DOMAIN, "send_message")

    return await gateway_handler.close_listener()
