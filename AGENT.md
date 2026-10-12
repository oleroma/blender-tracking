# Geometry Nodes JSON Exporter

Blender extension (5.2+) that exports Geometry Nodes input values, node links and mesh hashes to a `tracking.json`, imports the values back, and shows a git diff of the tracking file in the 3D Viewport N-panel (**GeoJSON** tab). Keep `README.md` in sync with user-visible changes.

## Layout
- `__init__.py`: the whole add-on, in one file with section banners: configuration, tracking settings, core logic (extract/apply), git diff, properties, operators and handlers, UI, registration. Keep it a single file unless asked to split it.
- `blender_manifest.toml`: extension metadata and permissions. There is no `bl_info`; do not add one.
- No build step and no third-party dependencies beyond what Blender ships (`bpy`, `numpy`).

## How it works
- Tracked targets are per scene (`scene.geonodes_json_exporter_groups`, `_collections`) but the tracking file is global: `collect_tracked_groups()` / `collect_tracked_collections()` merge the targets of all `bpy.data.scenes`, and export and import use that merged set. `_enabled` and `_filepath` (default `//tracking.json`) are also stored per scene; the handlers and operators read them from the active scene (`bpy.context.scene`).
- `save_post` runs `auto_export_handler` (export, then `update_cached_diff`). `load_post` resets `_cached_diff` / `_export_error`, then only refreshes the diff. Both do nothing more when tracking is disabled or the path can't be resolved.
- Tracking file layout is `{version, tracked_inputs, modifiers, meshes, all_node_trees}`. Each tracked node is `{"properties": {setting: value}, "inputs": {socket: value}, "nodes": {nested group nodes}}`; `modifiers` is `{object: {modifier: {"inputs": {...}}}}` for Geometry Nodes modifiers using a tracked group. Datablocks are keyed by `id_key` (adds the library file for linked data). **Any change to this layout must bump `FORMAT_VERSION`.** Import rejects other versions and the diff reports `OLD_FORMAT` (committed) or `STALE_FILE` (working copy).
- Socket and node identity: duplicate socket names are disambiguated by `socket_labels` (`Value[0]`, `Value[1]`); nodes are keyed by `node_key` (`name` or `name (label)`). Export, import, topology and diff must all use these helpers so keys stay consistent.
- `_cached_diff` and `_export_error` are module globals read by the panel. Reset them in `unregister()`.

## Conventions
- Floats are rounded to 4 decimals in `format_value`; mesh hashes round to the same precision. Output must be deterministic (sorted keys and lists) so git diffs stay minimal.
- Do not swallow exceptions silently. Export errors go to the console and `_export_error`; import counts outcomes (`applied`, `unchanged`, `skipped`, `failed`) and the operator reports them.
- Write the tracking file atomically (temp file, then `os.replace`). Run `git` only through `run_git` (timeout, no console window, `GIT_OPTIONAL_LOCKS=0`).
- Context overrides use `with bpy.context.temp_override(...)`, never dicts.
- Properties added to `bpy.types.Scene` must be registered after their PropertyGroups and deleted in reverse order in `unregister()`.
- UI sizes live in `UI_INDENT`, `UI_SPACING` and `MAX_DIFF_ROWS`; diff sections cap their rows with `RowBudget`.
- Keep edits scoped to the task and match the existing style (plain functions, section banners, `# type: ignore` on annotated Blender properties).
