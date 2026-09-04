# MyHOME

MyHOME integration for Home Assistant. Drives a BTicino / Legrand OpenWebNet
installation through an F452, F453AV, F454, F455, MH200, MH200N, MH201, MH202,
HL4684, AM4890 or MyHOMEServer1 gateway.

> **This is a modified fork of [anotherjulien/MyHOME](https://github.com/anotherjulien/MyHOME).**
> It is released under the same licence, the GNU Affero General Public License
> v3.0. See [Fork history](#fork-history), [Changes in this fork](#changes-in-this-fork)
> and [Licence](#licence).

## Requirements

- Home Assistant Core **2026.8 or newer**. Entities are linked to their gateway
  with `DeviceInfo.via_device_id`, which older releases reject.
- A supported gateway on the same network as Home Assistant.

## Installation

This fork is not in the HACS default store; add it as a custom repository.

1. **HACS** → the **⋮** menu, top right → **Custom repositories**
2. Repository: `https://github.com/jooonaaassssss/MyHOME`, Category:
   **Integration** → **Add**
3. Search for **MyHOME** in HACS → **Download**
4. Restart Home Assistant

The gateway itself is then set up through the Home Assistant UI. Most gateways
are auto-discovered; one that is not can still be added by hand. The devices
behind the gateway are configured in YAML — see below.

On a first install, and sometimes after an update, the OWNd listener can fail
to come up and devices report no status. Restarting Home Assistant clears it.

## Upgrading from 0.8 or earlier

The configuration structure changed after 0.8. You need to create and populate
the configuration file described in the wiki before the integration will start.

## Configuration and use

The upstream wiki still applies to this fork; the YAML syntax is unchanged.

- [Configuration](https://github.com/anotherjulien/MyHOME/wiki/Configuration)
- [Advanced uses](https://github.com/anotherjulien/MyHOME/wiki/Advanced-uses)

## Fork history

| Repository | Role |
| --- | --- |
| [anotherjulien/MyHOME](https://github.com/anotherjulien/MyHOME) | The original integration. All of the design and the overwhelming majority of the code is theirs. |
| [rdr-66/MyHOME](https://github.com/rdr-66/MyHOME) | Fork that added the SSDP-triggered event listener restart and moved to `OWNd==0.7.49`. |
| **this repository** | Forked from `rdr-66/MyHOME` at 0.9.3 to restore the integration on Home Assistant Core 2026.9. |

## Changes in this fork

Modified on **4 September 2026**, released as 0.9.5. The full record is in the
Git history; this is the summary.

**Setup no longer fails on Home Assistant Core 2026.9.** That release swapped
`voluptuous` for `probatio`, which registers itself in `sys.modules` under the
name `voluptuous`. The device schemas in `validate.py` are `Schema` subclasses
whose `__call__` rekeys devices and injects several defaults after validation.
Voluptuous invoked those nested schemas; probatio compiles them from their
declaration instead, so the post-processing was silently skipped and setup died
with `KeyError: 'icon'`, `'entity_name'` or `'entities'`. Each nested schema is
now wrapped in a plain callable that both engines have to call. The YAML syntax
is untouched.

**Bug fixes**

- An unreachable gateway raised `KeyError: 'gateway'` instead of
  `ConfigEntryNotReady`, so the setup was never retried.
- The SSDP restart handler awaited a loop that never returns, and stopping the
  old listener did not wait for it, so a restart could run two listeners on one
  session.
- Unloading ignored the per-platform unload results and never stopped the
  command workers.
- A failure after the platforms were forwarded left the entry half set up, so
  the retry reported `Config entry ... has already been setup!` rather than the
  real error.
- The three `return False` paths in setup became `ConfigEntryError`,
  `ConfigEntryNotReady` and `ConfigEntryAuthFailed`.
- The options dialog could not open: the handler assigned to
  `OptionsFlow.config_entry`, a read-only property since 2026.9.
- A debug call in the command worker raised inside `logging`.

**Deprecations**

- `DeviceInfo.via_device` → `via_device_id`
- `device_registry.devices` as a mapping → `async_entries_for_config_entry`
- `async_timeout` → `asyncio.timeout`

**Housekeeping**

- Log messages are English throughout, routine ones moved to `debug`.
- Line endings normalised to LF, with `.gitattributes` and `.editorconfig`.
- `hacs.json` no longer declares `zip_release`; HACS installs
  `custom_components/myhome` straight from the release tag, so a release can no
  longer exist in a state that fails to install.
- A regression test runs the configuration schema under both `probatio` and
  `voluptuous` on every push and asserts they agree.
- `actions/checkout` updated, ruff configured and its findings cleared.

The SSDP listener restart inherited from `rdr-66/MyHOME` is refactored but
functionally intact.

## Licence

Copyright of the original work belongs to its respective authors; see the Git
history for authorship.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, version 3.

This program is distributed in the hope that it will be useful, but **WITHOUT
ANY WARRANTY**; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the [LICENSE](LICENSE) file for the full text.

The complete corresponding source for this version is this repository:
<https://github.com/jooonaaassssss/MyHOME>.

The `LICENSE` file is carried over from the original project byte for byte;
`.gitattributes` keeps it out of the repository's line-ending normalisation.
