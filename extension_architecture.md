# Extension Architecture

## Overview
`Geometry Nodes JSON Exporter` is built using a modular architecture for clarity, maintainability, and compatibility with Blender 4.2+ headless operations.

## Module Breakdown
- **`__init__.py`**: Handles addon registration/unregistration. Contains CLI headless bootstrap injection (`sys.path` modification) and `importlib.reload` blocks to correctly refresh inner modules during Blender's `Reload Scripts` operator.
- **`blender_manifest.toml`**: Stores addon metadata and requirements for Blender 4.2 extensions (replaces `bl_info`).
- **`properties.py`**: Defines the `GeoNodesExporterNodeGroup` property group, which is registered dynamically to `bpy.types.Scene`. This stores the user's targeted `NodeTree` pointers.
- **`ui.py`**: Registers the `GEONODES_JSON_PT_panel` in the 3D Viewport side-bar, rendering the list of configured node groups and the export button.
- **`operators.py`**: Defines interactive operations:
  - `GEONODES_JSON_OT_actions`: Manages adding and deleting node groups from the UI list.
  - `GEONODES_JSON_OT_export`: Triggers the extraction logic and provides UI feedback (Info/Error reports).
- **`core.py`**: Contains the primary data logic. `export_nodes_to_json` loops over all configured `NodeTree` pointers, then traverses every internal node (`group_ptr.nodes`). It pulls the `default_value` of each exposed socket and safely casts complex types (like Vectors/Colors) to native python lists before dumping everything via the `json` module.

## Data Flow
`UI (Panel)` -> `Scene Property (Collection of NodeGroups)` -> `Operator (Export)` -> `Core Logic (Iterate nodes -> Extract socket values -> JSON Dump)`
