# Geometry Nodes JSON Exporter

Blender extension (5.2+) that exports Geometry Nodes input values, node links and mesh hashes to a `tracking.json`, imports the values back, and shows a git diff of the tracking file in the 3D Viewport N-panel (**GeoJSON** tab). Keep `README.md` in sync with user-visible changes.

## Layout
- `__init__.py`: the whole add-on, in one file with section banners: configuration, tracking settings, core logic (extract/apply), git diff, properties, operators and handlers, UI, registration. Keep it a single file unless asked to split it.
- `blender_manifest.toml`: extension metadata and permissions. There is no `bl_info`; do not add one.
- No build step and no third-party dependencies beyond what Blender ships (`bpy`, `numpy`).

## How it works
- Settings are per scene: `scene.geonodes_json_exporter_groups`, `_collections`, `_enabled`, `_filepath` (default `//tracking.json`). Export, import and auto-export all take the scene explicitly; do not merge data across scenes.
- `save_post` runs `auto_export_handler` (export, then `update_cached_diff`). `load_post` only refreshes the diff. Both do nothing when tracking is disabled or the path can't be resolved.
- Tracking file layout is `{version, tracked_inputs, meshes, all_node_trees}`. Each tracked node is `{"inputs": {socket: value}, "nodes": {nested group nodes}}`. **Any change to this layout must bump `FORMAT_VERSION`.** Import rejects other versions and the diff reports `OLD_FORMAT`.
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

## Verifying changes
There is no test suite. `python -m py_compile __init__.py` only checks syntax; real checks need Blender. Run a headless script that loads the add-on from this directory and exercises it:

```
blender -b --factory-startup -P your_test.py -- <path to this directory>
```

In the script, load `__init__.py` with `importlib.util.spec_from_file_location(..., submodule_search_locations=[dir])`, call `register()`, build node groups, add tracked groups and collections to `bpy.context.scene`, then call `bpy.ops.wm.save_as_mainfile(...)` to trigger the save handler. Check the written JSON, `_cached_diff` (initialise a git repo and commit the tracking file first), and an export/import round trip. The factory scene already contains a `Cube` object, so use unique names in tests. Also validate the manifest with `blender --command extension validate <path to this directory>`.

## Gotchas
- Target is Blender 5.2+ only (`blender_version_min`); do not add compatibility code for older versions.
- `Mesh.from_pydata` does not append to an existing mesh. Set vertex coordinates directly in tests.
- Mesh hashes cover stored mesh data and world transform, not evaluated modifier output.
