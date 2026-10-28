# Automatic excavation for BDSP

[Download v0.2.0](https://github.com/xCyrusBR/pokeldn-bdsp-excavation/releases/tag/v0.2.0)


Install the `.pokeplugin` in PokéLDN, enable it, and select **BDSP → Automatic excavation**.
Connect the ESP32 with its existing PokéLDN radio firmware. Enter the Grand Underground using
**Local Communication**, then start the tool. Open a wall and promptly close the initial
treasure message. Keep the controls still until the sweep finishes. Each subsequent wall is
detected and cleared automatically when **Automatic renewal** is enabled. With this switch
disabled (the default), Start clears one wall and then disconnects both helpers. With it
enabled, the plugin runs without a time limit until you press Stop. Stop disconnects both helpers.

Two independently accepted helpers share one ESP32 using separate UDP ports. Each owns 65
different cells, beginning at opposite corners; eight pickaxe hits per cell cover the tested
layers. The tool requests fresh positions and permissions for each wall. It stops attacks
without permission or after an ambiguous delivery. No firmware replacement is needed.

Two-helper hammer and pickaxe clearance were confirmed on a physical BDSP 1.3.0 console.
The packaged plugin also cleared two successive walls automatically without reconnection or
manual commands; the user confirmed both clears on 2026-10-28.
The pickaxe transport acknowledged 1,040 hits in about eight seconds in the development test;
speed depends on the connection and game. Wall cracks may advance. The tool cannot read hidden
treasures or exact layer thickness, and does not promise a one-second clear.

Use one radio tool at a time. The separate Diglett plugin cannot run on the same ESP32
simultaneously. Session logs contain keys and should remain private. The package contains
source code only, with no console keys, firmware, game assets, or session captures.

## Build and test

Python 3.11+ builds the source-only package with `python build.py`. Install `pytest`
and `trio` to run `python -m pytest -q`. The plugin itself runs inside PokéLDN
(plugin API 1.8 or later), using its existing BDSP/LDN libraries.

Version 0.2.0 exposes the **Automatic renewal** switch. Off clears one wall and exits;
on repeats until Stop, with no default time limit. It includes the `build_parser()`
entry point required by PokéLDN's GUI. The two modes and GUI entry point were
validated in development; two successive automatic physical clears were confirmed.

See [NOTICE.md](NOTICE.md) for upstream attribution and [LICENSE](LICENSE) for AGPL-3.0.
