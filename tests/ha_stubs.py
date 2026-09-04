"""Minimal Home Assistant stubs so ``validate.py`` can be imported standalone.

The regression test must run without a Home Assistant installation. ``validate.py``
only touches a handful of constants, two ``StrEnum`` device-class collections and
``device_registry.format_mac``, so those are reproduced here with the same values
Home Assistant uses. Nothing else of Home Assistant is emulated.
"""

from __future__ import annotations

import sys
import types
from enum import StrEnum


def _module(name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _package(name: str, **attrs: object) -> types.ModuleType:
    module = _module(name, **attrs)
    module.__path__ = []  # type: ignore[attr-defined]
    return module


class SwitchDeviceClass(StrEnum):
    OUTLET = "outlet"
    SWITCH = "switch"


class BinarySensorDeviceClass(StrEnum):
    BATTERY = "battery"
    BATTERY_CHARGING = "battery_charging"
    COLD = "cold"
    CONNECTIVITY = "connectivity"
    DOOR = "door"
    GARAGE_DOOR = "garage_door"
    GAS = "gas"
    HEAT = "heat"
    LIGHT = "light"
    LOCK = "lock"
    MOISTURE = "moisture"
    MOTION = "motion"
    MOVING = "moving"
    OCCUPANCY = "occupancy"
    OPENING = "opening"
    PLUG = "plug"
    POWER = "power"
    PRESENCE = "presence"
    PROBLEM = "problem"
    SAFETY = "safety"
    SMOKE = "smoke"
    SOUND = "sound"
    VIBRATION = "vibration"
    WINDOW = "window"


class SensorDeviceClass(StrEnum):
    ENERGY = "energy"
    ILLUMINANCE = "illuminance"
    POWER = "power"
    TEMPERATURE = "temperature"


def format_mac(mac: str) -> str:
    """Mirror ``homeassistant.helpers.device_registry.format_mac``."""
    to_test = mac
    if len(to_test) == 17 and to_test.count(":") == 5:
        return to_test.lower()
    if len(to_test) == 17 and to_test.count("-") == 5:
        to_test = to_test.replace("-", "")
    elif len(to_test) == 14 and to_test.count(".") == 2:
        to_test = to_test.replace(".", "")
    if len(to_test) == 12:
        return ":".join(to_test.lower()[i : i + 2] for i in range(0, 12, 2))
    return mac


def install() -> None:
    """Register the stub modules in ``sys.modules``."""
    _package("homeassistant")
    _package("homeassistant.helpers")
    _package("homeassistant.components")
    _module("homeassistant.const", CONF_NAME="name", CONF_MAC="mac")
    _module("homeassistant.helpers.device_registry", format_mac=format_mac)
    _module("homeassistant.components.light", DOMAIN="light")
    _module("homeassistant.components.button", DOMAIN="button")
    _module("homeassistant.components.cover", DOMAIN="cover")
    _module("homeassistant.components.climate", DOMAIN="climate")
    _module(
        "homeassistant.components.switch",
        DOMAIN="switch",
        SwitchDeviceClass=SwitchDeviceClass,
    )
    _module(
        "homeassistant.components.binary_sensor",
        DOMAIN="binary_sensor",
        BinarySensorDeviceClass=BinarySensorDeviceClass,
    )
    _module(
        "homeassistant.components.sensor",
        DOMAIN="sensor",
        SensorDeviceClass=SensorDeviceClass,
    )
