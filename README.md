# Geometry Nodes JSON Exporter

A Blender extension designed to automatically extract all input values from all internal nodes of specified Geometry Node groups and save them as a structured JSON file.

## Features
- **Dynamic Node Traversal:** Automatically iterates over all internal nodes within targeted Geometry Node groups.
- **Data Serialization:** Casts and extracts exposed values (including basic types, Vectors, and Colors) directly into JSON format.
- **Multiple Group Configuration:** Supports queuing multiple Node Groups for batched extraction in a single pass.
- **Modern Architecture:** Built for Blender 4.2+ using the `blender_manifest.toml` extension system.

## Usage
1. Open the **GeoJSON** tab in the 3D Viewport side-panel (N-panel).
2. Click the `+` button to add a Node Group target.
3. Select your desired Geometry Node tree in the `Node Group` property field.
4. Set the target export filepath.
5. Click **Export Node Inputs to JSON**.
