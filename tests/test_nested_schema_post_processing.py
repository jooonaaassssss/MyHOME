"""Regression test for the nested-schema post-processing in ``validate.py``.

``MyHomeDeviceSchema`` and ``MyHomeSensorSchema`` override ``Schema.__call__`` to
rekey devices to ``who-where`` and to inject the ``entities``, ``icon``, ``icon_on``,
``entity_name`` and ``model`` defaults that the platforms read unguarded. Those
schemas are nested inside ``gateway_schema``, and a validation engine is free to
compile a nested schema from its declaration instead of calling it -- which is what
Home Assistant Core 2026.9 does after replacing voluptuous with probatio.

The integration must behave identically under both engines, so every assertion here
runs twice: once against real voluptuous, once against probatio's compatibility shim.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .schema_probe import ENGINES

PROBE = Path(__file__).resolve().parent / "schema_probe.py"

EXPECTED_DEVICES = {
    "light": ["1-11"],
    "switch": ["1-12"],
    "cover": ["2-21"],
    "binary_sensor": ["25-31"],
    "sensor": ["18-51"],
    "climate": ["4-1"],
    "button": ["1-11", "1-12", "2-21"],
}

# Keys the platforms index without a membership check; a missing one is a KeyError
# at setup time rather than a graceful degradation.
REQUIRED_DEVICE_KEYS = {"entities", "icon", "icon_on", "entity_name", "model"}
REQUIRED_SENSOR_KEYS = {"entities", "model", "who"}


@pytest.fixture(scope="module", params=ENGINES)
def report(request: pytest.FixtureRequest) -> dict:
    """Validate the fixture in a subprocess pinned to one engine."""
    completed = subprocess.run(
        [sys.executable, str(PROBE), "--engine", request.param],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_validation_succeeds(report: dict) -> None:
    assert report["ok"], report.get("error")


def test_gateway_is_rekeyed_to_its_mac(report: dict) -> None:
    assert report["gateways"] == ["00:03:50:aa:bb:cc"]


def test_button_platform_is_synthesised(report: dict) -> None:
    assert "button" in report["platforms"]


def test_devices_are_rekeyed_to_who_where(report: dict) -> None:
    assert report["devices"] == EXPECTED_DEVICES


@pytest.mark.parametrize("platform", sorted(set(EXPECTED_DEVICES) - {"sensor"}))
def test_device_defaults_are_injected(report: dict, platform: str) -> None:
    for device, keys in report["device_keys"][platform].items():
        missing = REQUIRED_DEVICE_KEYS - set(keys)
        assert not missing, f"{platform}.{device} is missing {sorted(missing)}"


def test_sensor_defaults_are_injected(report: dict) -> None:
    for device, keys in report["device_keys"]["sensor"].items():
        missing = REQUIRED_SENSOR_KEYS - set(keys)
        assert not missing, f"sensor.{device} is missing {sorted(missing)}"
