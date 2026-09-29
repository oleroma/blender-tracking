# Extension Architecture

## Overview
`Geometry Nodes JSON Exporter` is built using a modular architecture for clarity, maintainability, and compatibility with Blender 4.2+ headless operations.

## Module Breakdown
- **`__init__.py`**: Handles addon registration/unregistration. Contains CLI headless bootstrap injection (`sys.path` modification) and `importlib.reload` blocks to correctly refresh inner modules during Blender's `Reload Scripts` operator.
- **`blender_manifest.toml`**: Stores addon metadata and requirements for Blender 4.2 extensions (replaces `bl_info`).
- **`properties.py`**: Defines the `GeoNodesExporterNodeGroup` property group, which is registered dynamically to `bpy.types.Scene`. This stores the user's targeted `NodeTree` pointer, as well as per-group `depth` and `groups_only` settings.
- **`ui.py`**: Registers the `GEONODES_JSON_PT_panel` in the 3D Viewport side-bar, rendering the list of configured node groups and their per-group settings in the same row, along with the export button.
- **`operators.py`**: Defines interactive operations:
  - `GEONODES_JSON_OT_actions`: Manages adding and deleting node groups from the UI list.
  - `GEONODES_JSON_OT_export`: Triggers the extraction logic and provides UI feedback (Info/Error reports).
- **`core.py`**: Contains the primary data logic. `export_nodes_to_json` calls a recursive helper `extract_node_tree_data` which traverses nodes in a given tree up to the group's `depth`. It pulls the `default_value` of each exposed socket and safely casts complex types to native python lists. It recurses into nested `GROUP` nodes, merging their internal nodes directly into the parent node's data dictionary.

## Data Flow
`UI (Panel)` -> `Scene Property (Collection of NodeGroups with individual Depth and GroupsOnly settings)` -> `Operator (Export)` -> `Core Logic (Recursive iteration up to per-group max_depth -> JSON Dump)`
