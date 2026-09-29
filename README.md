# Geometry Nodes JSON Exporter

A Blender extension designed to automatically extract all input values from all internal nodes of specified Geometry Node groups and save them as a structured JSON file.

## Features
- **Dynamic Node Traversal:** Automatically iterates over all internal nodes within targeted Geometry Node groups.
- **Data Serialization:** Casts and extracts exposed values (including basic types, Vectors, and Colors) directly into JSON format.
- **Multiple Group Configuration:** Supports queuing multiple Node Groups for batched extraction in a single pass.
- **Recursive Extraction:** Specify a recursion depth to automatically drill down into nested Node Groups and extract their internal node configurations while preserving hierarchy.
- **Filter Standard Nodes:** Optionally extract inputs strictly from node groups, ignoring basic utility or math nodes.
- **Modern Architecture:** Built for Blender 4.2+ using the `blender_manifest.toml` extension system.

## Usage
1. Open the **GeoJSON** tab in the 3D Viewport side-panel (N-panel).
2. Click the `+` button to add a Node Group target.
3. Select your desired Geometry Node tree in the `Node Group` property field.
4. Set the target export filepath.
5. In the same row as your added Node Group, set its **Recursion Depth** (0 for top-level only, 1 for nested groups inside, etc.).
6. In that same row, toggle the **Groups Only** icon button if you want to skip standard nodes and only export group node inputs for that specific target.
7. Click **Export Node Inputs to JSON**.
