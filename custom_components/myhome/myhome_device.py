"""Support for common values for MyHome devices."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

from homeassistant.helpers.entity import Entity


from .const import DOMAIN, CONF_PLATFORMS, CONF_ENTITIES


class MyHOMEEntity(Entity):
    def __init__(
        self,
        hass,
        name: str,
        platform: str,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        self._hass = hass
        self._platform = platform
        self._who = who
        self._where = where
        self._device_id = device_id
        self._attr_unique_id = f"{gateway.mac}-{self._device_id}"
        self._manufacturer = manufacturer or "BTicino S.p.A."
        self._model = model
        self._gateway_handler = gateway
        self._attr_has_entity_name = True
        self._attr_name = None
        self._attr_entity_registry_enabled_default = True
        self._attr_should_poll = False

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{gateway.mac}-{self._device_id}")},
            "name": name,
            "manufacturer": self._manufacturer,
            "model": self._model,
        }
        if self._gateway_handler.device_registry_id is not None:
            # via_device_id takes the registry id of the gateway device, which
            # async_setup_entry creates before it forwards the platforms.
            self._attr_device_info["via_device_id"] = (
                self._gateway_handler.device_registry_id
            )

    @property
    def available(self) -> bool:
        """Whether the gateway this entity lives behind can be reached.

        Without this every entity kept reporting its last known state for as
        long as the gateway stayed away, so a shutter that had not been heard
        from in days was indistinguishable from one that is genuinely closed.
        """
        return self._gateway_handler.is_connected

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id][CONF_ENTITIES][self._platform] = self
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        if self._platform in self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id][CONF_ENTITIES]:
            del self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id][CONF_ENTITIES][self._platform]
