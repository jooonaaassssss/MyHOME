"""Run ``validate.config_schema`` under one validation engine and report the result.

Home Assistant Core 2026.9 swapped ``voluptuous`` for ``probatio``, which installs
itself into ``sys.modules`` under the name ``voluptuous``. Which engine is active is
therefore a process-wide, one-way decision, so the test harness runs this module as a
subprocess once per engine and reads the JSON report from stdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MYHOME_DIR = REPO_ROOT / "custom_components" / "myhome"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "myhome_minimal.yaml"

ENGINES = ("voluptuous", "probatio")


def _activate(engine: str) -> str:
    """Make ``import voluptuous`` resolve to the requested engine."""
    if engine == "probatio":
        import probatio
        from probatio.compat import install_as_voluptuous

        install_as_voluptuous()
        return getattr(probatio, "__version__", "unknown")

    import voluptuous

    return getattr(voluptuous, "__version__", "unknown")


def _load_validate() -> types.ModuleType:
    """Import ``validate.py`` without executing the integration's ``__init__.py``.

    The real package ``__init__`` pulls in aiofiles, OWNd and large parts of Home
    Assistant. Only the relative ``.const`` import has to work, so a synthetic parent
    package pointed at the integration directory is enough.
    """
    package = types.ModuleType("myhome_under_test")
    package.__path__ = [str(MYHOME_DIR)]  # type: ignore[attr-defined]
    sys.modules["myhome_under_test"] = package

    spec = importlib.util.spec_from_file_location(
        "myhome_under_test.validate", MYHOME_DIR / "validate.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("could not build an import spec for validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["myhome_under_test.validate"] = module
    spec.loader.exec_module(module)
    return module


def probe(engine: str) -> dict:
    """Validate the fixture and describe what the schema produced."""
    engine_version = _activate(engine)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ha_stubs

    ha_stubs.install()

    import yaml

    validate = _load_validate()

    with FIXTURE.open(encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle)

    report: dict = {"engine": engine, "engine_version": engine_version}
    try:
        validated = validate.config_schema(raw_config)
    except Exception as err:  # noqa: BLE001 - the failure mode is the payload
        report["ok"] = False
        report["error"] = f"{type(err).__name__}: {err}"
        return report

    report["ok"] = True
    report["gateways"] = sorted(validated)
    platforms = next(iter(validated.values()))["platforms"]
    report["platforms"] = sorted(platforms)
    report["devices"] = {
        platform: sorted(devices) for platform, devices in platforms.items()
    }
    report["device_keys"] = {
        platform: {device: sorted(payload) for device, payload in devices.items()}
        for platform, devices in platforms.items()
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=ENGINES, required=True)
    args = parser.parse_args()
    json.dump(probe(args.engine), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
