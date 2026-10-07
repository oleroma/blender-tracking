import bpy
import hashlib
import json
import math
import os
import subprocess
import traceback
from collections import Counter

import numpy as np
from bpy_extras.io_utils import ExportHelper, ImportHelper

# ==============================================================================
# CONFIGURATION
# ==============================================================================

UI_INDENT = 0.02        # Left indentation for nested elements (lower = less indent)
UI_SPACING = 0.6        # Line spacing/compactness for items (lower = more compact)
MAX_DIFF_ROWS = 60      # Max rows drawn per diff section (inputs / meshes / links)

DEFAULT_TRACKING_PATH = "//tracking.json"   # relative to the .blend file
FORMAT_VERSION = 2                          # bump whenever the tracking JSON layout changes
GIT_TIMEOUT = 5                             # seconds

# Socket types whose default_value is a datablock, mapped to the bpy.data collection holding it.
ID_SOCKET_COLLECTIONS = {
    "NodeSocketObject": "objects",
    "NodeSocketCollection": "collections",
    "NodeSocketMaterial": "materials",
    "NodeSocketImage": "images",
    "NodeSocketTexture": "textures",
}

# ==============================================================================
# GLOBALS & CACHE
# ==============================================================================

_cached_diff = None     # result of update_cached_diff, drawn by the panel
_export_error = None    # message of the last failed automatic export, drawn by the panel

# ==============================================================================
# TRACKING SETTINGS
# ==============================================================================

def is_tracking_enabled(scene=None):
    scene = scene or bpy.context.scene
    return bool(scene) and scene.geonodes_json_exporter_enabled

def get_tracking_filepath(scene=None):
    """Returns the absolute tracking file path configured for the scene, or None if it can't be resolved."""
    scene = scene or bpy.context.scene
    if not scene:
        return None
    path = scene.geonodes_json_exporter_filepath.strip()
    if not path:
        return None
    if path.startswith("//") and not bpy.data.filepath:
        return None  # relative path needs a saved .blend
    return os.path.normpath(bpy.path.abspath(path))

def is_tracking_filepath(filepath, scene=None):
    tracking_path = get_tracking_filepath(scene)
    return bool(tracking_path) and os.path.normcase(os.path.normpath(filepath)) == os.path.normcase(tracking_path)

def collect_tracked_groups():
    """Returns {NodeTree: {"depth", "groups_only"}} for the targets of all scenes.

    A tree tracked more than once gets the deepest depth, and groups_only only if every entry asks for it.
    """
    merged = {}
    for scene in bpy.data.scenes:
        for item in scene.geonodes_json_exporter_groups:
            tree = item.group_ptr
            if not tree:
                continue
            config = merged.get(tree)
            if config is None:
                merged[tree] = {"depth": item.depth, "groups_only": item.groups_only}
            else:
                config["depth"] = max(config["depth"], item.depth)
                config["groups_only"] = config["groups_only"] and item.groups_only
    return merged

def collect_tracked_collections():
    """Returns the set of collections tracked by any scene."""
    return {
        item.collection_ptr
        for scene in bpy.data.scenes
        for item in scene.geonodes_json_exporter_collections
        if item.collection_ptr
    }

# ==============================================================================
# CORE LOGIC
# ==============================================================================

def tree_key(tree):
    """Unique JSON key of a node tree (linked trees can share a name with local ones)."""
    if tree.library:
        return f"{tree.name} ({os.path.basename(tree.library.filepath)})"
    return tree.name

def socket_labels(sockets):
    """Labels for a node's sockets. Duplicate names (e.g. Math 'Value') get an index suffix."""
    counts = Counter(s.name for s in sockets)
    seen = Counter()
    labels = []
    for s in sockets:
        if counts[s.name] > 1:
            labels.append(f"{s.name}[{seen[s.name]}]")
            seen[s.name] += 1
        else:
            labels.append(s.name)
    return labels

def get_node_tree_topology(node_tree):
    label_cache = {}

    def label_of(node, socket, is_output):
        key = (node.as_pointer(), is_output)
        if key not in label_cache:
            sockets = list(node.outputs if is_output else node.inputs)
            label_cache[key] = {s.as_pointer(): l for s, l in zip(sockets, socket_labels(sockets))}
        return label_cache[key].get(socket.as_pointer(), socket.name)

    links = []
    for link in node_tree.links:
        from_label = label_of(link.from_node, link.from_socket, True)
        to_label = label_of(link.to_node, link.to_socket, False)
        text = f"[{link.from_node.name}] {from_label} -> {to_label} [{link.to_node.name}]"
        if link.is_muted:
            text += " (muted)"
        links.append(text)

    return sorted(links)

def extract_meshes_state(collections):
    """Hashes world transform, vertex positions and topology of every mesh object in the collections."""
    objects = {obj for coll in collections for obj in coll.all_objects if obj.type == 'MESH' and obj.data}

    # (collection attribute, property, values per element, dtype)
    layers = (
        ("vertices", "co", 3, np.float32),
        ("edges", "vertices", 2, np.int32),
        ("polygons", "loop_start", 1, np.int32),
        ("loops", "vertex_index", 1, np.int32),
    )

    state = {}
    for obj in objects:
        if obj.mode == 'EDIT':
            obj.update_from_editmode()

        hasher = hashlib.md5(usedforsecurity=False)
        matrix = ",".join(f"{round(v, 4) + 0.0:.4f}" for row in obj.matrix_world for v in row)
        hasher.update(matrix.encode('utf-8'))

        mesh = obj.data
        for coll_name, prop, width, dtype in layers:
            coll = getattr(mesh, coll_name)
            count = len(coll)
            if count == 0:
                continue
            values = np.empty(count * width, dtype=dtype)
            coll.foreach_get(prop, values)
            if dtype == np.float32:
                values = np.round(values, 4) + 0.0  # +0.0 turns -0.0 into 0.0
            hasher.update(f"{coll_name}:{count}".encode('utf-8'))
            hasher.update(values.tobytes())

        state[obj.name] = hasher.hexdigest()

    return {k: state[k] for k in sorted(state)}

def format_value(val, decimals=4):
    """Converts a socket value into JSON-safe data (rounded floats, nested lists)."""
    if isinstance(val, float):
        return round(val, decimals) if math.isfinite(val) else str(val)
    if val is None or isinstance(val, (bool, int, str)):
        return val
    return [format_value(v, decimals) for v in val]

def get_node_input_value(socket):
    val = socket.default_value
    try:
        if isinstance(val, bpy.types.ID):
            return val.name
        return format_value(val)
    except Exception:
        return str(val)

def set_node_input_value(socket, val):
    """Applies a JSON value to a socket. Returns 'applied', 'unchanged', 'skipped' or 'failed'."""
    try:
        collection_name = ID_SOCKET_COLLECTIONS.get(socket.bl_idname)
        if collection_name:
            if val is None:
                target = None
            elif isinstance(val, str):
                target = getattr(bpy.data, collection_name).get(val)
                if target is None:
                    return 'skipped'  # datablock doesn't exist in this file
            else:
                return 'failed'
            if socket.default_value == target:
                return 'unchanged'
            socket.default_value = target
            return 'applied'

        if get_node_input_value(socket) == val:
            return 'unchanged'
        socket.default_value = val
        return 'applied'
    except Exception:
        return 'failed'

def node_key(node):
    return f"{node.name} ({node.label})" if node.label else node.name

def iter_input_sockets(node):
    """Yields (label, socket) for visible inputs that carry a value."""
    for label, socket in zip(socket_labels(list(node.inputs)), node.inputs):
        if socket.is_unavailable or socket.hide or not hasattr(socket, "default_value"):
            continue
        yield label, socket

def extract_node_tree_data(node_tree, current_depth, max_depth, groups_only=False):
    """Returns {node: {"inputs": {...}, "nodes": {nested group content}}}; empty parts are omitted."""
    tree_data = {}
    for node in node_tree.nodes:
        is_group = node.type == 'GROUP'
        entry = {}

        if not groups_only or is_group:
            inputs = {label: get_node_input_value(socket) for label, socket in iter_input_sockets(node)}
            if inputs:
                entry["inputs"] = inputs

        if is_group and node.node_tree and current_depth < max_depth:
            nested = extract_node_tree_data(node.node_tree, current_depth + 1, max_depth, groups_only)
            if nested:
                entry["nodes"] = nested

        if entry:
            tree_data[node_key(node)] = entry

    return tree_data

def apply_node_tree_data(node_tree, tree_data, current_depth, max_depth, groups_only, stats):
    if not isinstance(tree_data, dict):
        return

    for node in node_tree.nodes:
        entry = tree_data.get(node_key(node))
        if not isinstance(entry, dict):
            continue

        is_group = node.type == 'GROUP'

        inputs = entry.get("inputs")
        if isinstance(inputs, dict) and (not groups_only or is_group):
            for label, socket in iter_input_sockets(node):
                if label in inputs:
                    stats[set_node_input_value(socket, inputs[label])] += 1

        if is_group and node.node_tree and current_depth < max_depth:
            apply_node_tree_data(node.node_tree, entry.get("nodes"), current_depth + 1, max_depth, groups_only, stats)

def export_nodes_to_json(filepath):
    """Writes the tracking file for the targets of all scenes."""
    result = {
        "version": FORMAT_VERSION,
        "tracked_inputs": {},
        "meshes": extract_meshes_state(collect_tracked_collections()),
        "all_node_trees": {},
    }

    topologies = {tree_key(tree): get_node_tree_topology(tree) for tree in bpy.data.node_groups}
    for name in sorted(topologies):
        result["all_node_trees"][name] = topologies[name]

    tracked = {
        tree_key(tree): extract_node_tree_data(tree, 0, config["depth"], config["groups_only"])
        for tree, config in collect_tracked_groups().items()
    }
    for name in sorted(tracked):
        result["tracked_inputs"][name] = tracked[name]

    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # Write to a temp file first so a failure never leaves a truncated tracking file.
    temp_path = filepath + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=4)
    os.replace(temp_path, filepath)

def import_nodes_from_json(filepath):
    """Applies stored inputs to the tracked groups of all scenes. Returns a Counter of per-input outcomes."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"JSON file not found: {filepath}")

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    version = data.get("version") if isinstance(data, dict) else None
    if version != FORMAT_VERSION:
        raise ValueError(
            f"Unsupported tracking file format (version {version}, expected {FORMAT_VERSION}). "
            "Re-export it with this version of the add-on."
        )

    tracked_inputs = data.get("tracked_inputs", {})
    stats = Counter({'applied': 0, 'unchanged': 0, 'skipped': 0, 'failed': 0})

    for tree, config in collect_tracked_groups().items():
        key = tree_key(tree)
        if key in tracked_inputs:
            apply_node_tree_data(tree, tracked_inputs[key], 0, config["depth"], config["groups_only"], stats)

    return stats

# ==============================================================================
# GIT DIFF PARSING
# ==============================================================================

def run_git(args, cwd):
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=GIT_TIMEOUT,
        env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

def flatten_inputs(nodes, path=()):
    """Flattens {node: {"inputs", "nodes"}} into {(node path, input): value}."""
    flat = {}
    for name, entry in nodes.items():
        if not isinstance(entry, dict):
            continue
        node_path = path + (name,)
        for label, value in entry.get("inputs", {}).items():
            flat[(node_path, label)] = value
        flat.update(flatten_inputs(entry.get("nodes", {}), node_path))
    return flat

def diff_meshes(old_meshes, new_meshes):
    changes = []
    for obj in sorted(new_meshes):
        if obj not in old_meshes:
            changes.append((obj, 'ADD'))
        elif new_meshes[obj] != old_meshes[obj]:
            changes.append((obj, 'FILE_REFRESH'))
    for obj in sorted(old_meshes):
        if obj not in new_meshes:
            changes.append((obj, 'REMOVE'))
    return changes

def diff_trees(old_trees, new_trees):
    changes = {}
    for tree_name in sorted(set(old_trees) | set(new_trees)):
        old_links = set(old_trees.get(tree_name, []))
        new_links = set(new_trees.get(tree_name, []))
        added = sorted(new_links - old_links)
        removed = sorted(old_links - new_links)
        if added or removed:
            changes[tree_name] = {"added": added, "removed": removed}
    return changes

def diff_inputs(old_inputs, new_inputs):
    """Returns {tracked tree: {node path: [(input, change type, old, new), ...]}}."""
    def flatten_all(tracked):
        flat = {}
        for tree_name, nodes in tracked.items():
            for (node_path, label), value in flatten_inputs(nodes).items():
                flat[(tree_name, " > ".join(node_path), label)] = value
        return flat

    old_flat = flatten_all(old_inputs)
    new_flat = flatten_all(new_inputs)

    changes = {}
    for key in sorted(set(old_flat) | set(new_flat)):
        tree_name, node_path, label = key
        if key not in old_flat:
            change = (label, 'ADD', None, new_flat[key])
        elif key not in new_flat:
            change = (label, 'REMOVE', old_flat[key], None)
        elif old_flat[key] != new_flat[key]:
            change = (label, 'CHANGE', old_flat[key], new_flat[key])
        else:
            continue
        changes.setdefault(tree_name, {}).setdefault(node_path, []).append(change)
    return changes

def update_cached_diff(filepath):
    global _cached_diff
    _cached_diff = {
        "status": "OK",
        "msg": "",
        "has_changes": False,
        "meshes": [],
        "trees": {},
        "inputs": {}
    }

    if not filepath or not os.path.exists(filepath):
        _cached_diff["status"] = "NO_FILE"
        return

    dir_path = os.path.dirname(filepath) or "."
    file_name = os.path.basename(filepath)

    try:
        repo_check = run_git(["rev-parse", "--is-inside-work-tree"], dir_path)
        if repo_check.returncode != 0:
            _cached_diff["status"] = "NO_REPO"
            return

        head_show = run_git(["show", f"HEAD:./{file_name}"], dir_path)
        if head_show.returncode != 0:
            _cached_diff["status"] = "NO_COMMIT"
            return

        try:
            committed_data = json.loads(head_show.stdout)
        except json.JSONDecodeError:
            _cached_diff["status"] = "INVALID_JSON"
            return

        if not isinstance(committed_data, dict) or committed_data.get("version") != FORMAT_VERSION:
            _cached_diff["status"] = "OLD_FORMAT"
            return

        with open(filepath, 'r', encoding='utf-8') as f:
            current_data = json.load(f)

        _cached_diff["meshes"] = diff_meshes(committed_data.get("meshes", {}), current_data.get("meshes", {}))
        _cached_diff["trees"] = diff_trees(committed_data.get("all_node_trees", {}), current_data.get("all_node_trees", {}))
        _cached_diff["inputs"] = diff_inputs(committed_data.get("tracked_inputs", {}), current_data.get("tracked_inputs", {}))
        _cached_diff["has_changes"] = bool(_cached_diff["meshes"] or _cached_diff["trees"] or _cached_diff["inputs"])

    except FileNotFoundError:
        _cached_diff["status"] = "NO_GIT"
    except subprocess.TimeoutExpired:
        _cached_diff["status"] = "ERROR"
        _cached_diff["msg"] = f"git did not respond within {GIT_TIMEOUT}s"
    except Exception as e:
        _cached_diff["status"] = "ERROR"
        _cached_diff["msg"] = str(e)

def trigger_ui_redraw():
    if bpy.context and hasattr(bpy.context, "window_manager"):
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

# ==============================================================================
# PROPERTIES
# ==============================================================================

class GeoNodesExporterNodeGroup(bpy.types.PropertyGroup):
    group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Node Group")  # type: ignore
    depth: bpy.props.IntProperty(name="Depth", default=0, min=0)  # type: ignore
    groups_only: bpy.props.BoolProperty(name="Groups Only", default=False)  # type: ignore

class GeoNodesExporterCollection(bpy.types.PropertyGroup):
    collection_ptr: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection")  # type: ignore

# ==============================================================================
# OPERATORS & HANDLERS
# ==============================================================================

def default_dialog_filepath():
    return get_tracking_filepath() or "tracking.json"

class GEONODES_JSON_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "geonodes_json.export"
    bl_label = "Export tracking to JSON"
    bl_description = "Extracts all inputs from configured nodes and saves to a specific JSON file"

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})  # type: ignore

    def invoke(self, context, event):
        self.filepath = default_dialog_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        global _export_error
        try:
            export_nodes_to_json(self.filepath)

            if is_tracking_enabled(context.scene) and is_tracking_filepath(self.filepath, context.scene):
                _export_error = None
                update_cached_diff(self.filepath)
                trigger_ui_redraw()

            self.report({'INFO'}, f"Successfully exported to {self.filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}

class GEONODES_JSON_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "geonodes_json.import"
    bl_label = "Import input values from JSON"
    bl_description = "Applies stored inputs from JSON to configured nodes"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})  # type: ignore

    def invoke(self, context, event):
        self.filepath = default_dialog_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            stats = import_nodes_from_json(self.filepath)
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {str(e)}")
            return {'CANCELLED'}

        context.view_layer.update()
        summary = (f"{stats['applied']} applied, {stats['unchanged']} unchanged, "
                   f"{stats['skipped']} skipped (missing datablock), {stats['failed']} failed")
        if stats['failed'] or stats['skipped']:
            self.report({'WARNING'}, f"Imported from {self.filepath}: {summary}")
        elif sum(stats.values()) == 0:
            self.report({'WARNING'}, f"Nothing to import: no tracked node group matched {self.filepath}")
        else:
            self.report({'INFO'}, f"Imported from {self.filepath}: {summary}")
        return {'FINISHED'}

class GEONODES_JSON_OT_actions(bpy.types.Operator):
    bl_idname = "geonodes_json.actions"
    bl_label = "Action"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[('ADD', "Add", ""), ('DEL', "Delete", "")])  # type: ignore
    item_idx: bpy.props.IntProperty(default=-1)  # type: ignore
    list_type: bpy.props.EnumProperty(items=[('GROUPS', "Groups", ""), ('COLLECTIONS', "Collections", "")], default='GROUPS')  # type: ignore

    def execute(self, context):
        scene = context.scene

        if self.list_type == 'GROUPS':
            target_list = scene.geonodes_json_exporter_groups
        else:
            target_list = scene.geonodes_json_exporter_collections

        if self.action == 'ADD':
            target_list.add()
        elif 0 <= self.item_idx < len(target_list):
            target_list.remove(self.item_idx)

        return {'FINISHED'}

@bpy.app.handlers.persistent
def auto_export_handler(dummy):
    global _export_error
    scene = bpy.context.scene
    if not is_tracking_enabled(scene):
        return
    filepath = get_tracking_filepath(scene)
    if not filepath:
        return

    try:
        export_nodes_to_json(filepath)
        _export_error = None
        update_cached_diff(filepath)
    except Exception as e:
        traceback.print_exc()
        _export_error = f"{type(e).__name__}: {e}"
    trigger_ui_redraw()

@bpy.app.handlers.persistent
def load_post_handler(dummy):
    scene = bpy.context.scene
    if not is_tracking_enabled(scene):
        return
    filepath = get_tracking_filepath(scene)
    if filepath:
        update_cached_diff(filepath)
        trigger_ui_redraw()

# ==============================================================================
# USER INTERFACE
# ==============================================================================

class RowBudget:
    """Limits how many rows a diff section draws; counts what was left out."""
    def __init__(self, limit):
        self.left = limit
        self.skipped = 0

    def take(self):
        if self.left > 0:
            self.left -= 1
            return True
        self.skipped += 1
        return False

    def draw_remainder(self, layout):
        if self.skipped:
            layout.label(text=f"... {self.skipped} more not shown")

def shorten(text, limit=50):
    return text[:limit] + ("..." if len(text) > limit else "")

def draw_link(layout, link, icon):
    link_box = layout.box()
    link_box.scale_y = UI_SPACING
    parts = link.split(" -> ")
    if len(parts) == 2:
        row = link_box.row(align=True)
        row.alignment = 'LEFT'
        row.label(text=shorten(parts[0]))
        row.label(text="", icon=icon)
        row.separator()
        row.label(text=shorten(parts[1]))
    else:
        link_box.label(text=shorten(link, 100), icon=icon)

def draw_inputs_section(box):
    inputs = _cached_diff["inputs"]
    total = sum(len(changes) for nodes in inputs.values() for changes in nodes.values())
    section = box.box()
    section.label(text=f"Inputs ({total} inputs changed)", icon='PREFERENCES')
    budget = RowBudget(MAX_DIFF_ROWS)

    for parent, nodes in inputs.items():
        pb = section.box()
        pb_row = pb.row()
        pb_row.scale_y = UI_SPACING
        pb_row.label(text=parent, icon='NODETREE')

        for node, changes in nodes.items():
            nsplit = pb.split(factor=UI_INDENT)
            nsplit.column()
            nb = nsplit.column().box()

            nb_row = nb.row()
            nb_row.scale_y = UI_SPACING
            nb_row.label(text=node, icon='NODE')

            isplit = nb.split(factor=UI_INDENT)
            isplit.column()
            ib = isplit.column().box()

            for inp, change_type, old_val, new_val in changes:
                if not budget.take():
                    continue
                row = ib.row(align=True)
                row.scale_y = UI_SPACING
                row.alignment = 'LEFT'

                if change_type == 'ADD':
                    row.label(text=f"{inp}: {new_val}", icon='ADD')
                elif change_type == 'REMOVE':
                    row.label(text=f"{inp}: {old_val}", icon='REMOVE')
                else:
                    row.label(text=f"{inp}:", icon='DOT')
                    row.label(text=str(old_val))
                    row.label(text="", icon='FORWARD')
                    row.label(text=str(new_val))

    budget.draw_remainder(section)

def draw_meshes_section(box):
    meshes = _cached_diff["meshes"]
    section = box.box()
    section.label(text=f"Meshes ({len(meshes)} objects changed)", icon='MESH_DATA')
    budget = RowBudget(MAX_DIFF_ROWS)

    for msg, icon in meshes:
        if not budget.take():
            continue
        mesh_box = section.box()
        mesh_box.scale_y = UI_SPACING
        mesh_box.label(text=msg, icon=icon)

    budget.draw_remainder(section)

def draw_links_section(box):
    trees = _cached_diff["trees"]
    total_added = sum(len(t["added"]) for t in trees.values())
    total_removed = sum(len(t["removed"]) for t in trees.values())
    section = box.box()
    section.label(text=f"Links ({total_added} added, {total_removed} removed)", icon='LINKED')
    budget = RowBudget(MAX_DIFF_ROWS)

    for tree_name, tree_data in trees.items():
        b = section.box()
        b.label(text=tree_name, icon='NODETREE')
        for links, icon in ((tree_data["removed"], 'UNLINKED'), (tree_data["added"], 'LINKED')):
            for link in links:
                if budget.take():
                    draw_link(b, link, icon)

    budget.draw_remainder(section)

DIFF_STATUS_MESSAGES = {
    "NO_FILE": ("Tracking file not written yet.", 'INFO'),
    "NO_GIT": ("git executable not found.", 'ERROR'),
    "NO_REPO": ("Tracking file is not inside a git repository.", 'INFO'),
    "NO_COMMIT": ("File not yet in Git or no previous commit.", 'INFO'),
    "INVALID_JSON": ("Committed tracking file is not valid JSON.", 'ERROR'),
    "OLD_FORMAT": ("Committed tracking file uses an older format. Commit the new one to resume diffing.", 'INFO'),
}

def draw_git_diff(layout):
    box = layout.box()

    if not is_tracking_enabled():
        box.label(text="Tracking is disabled.", icon='INFO')
        return

    if not get_tracking_filepath():
        box.label(text="Save the .blend file to enable auto-tracking.", icon='INFO')
        return

    if _cached_diff is None:
        box.label(text="Press Save or Export to generate diff.", icon='INFO')
        return

    status = _cached_diff["status"]
    if status in DIFF_STATUS_MESSAGES:
        text, icon = DIFF_STATUS_MESSAGES[status]
        box.label(text=text, icon=icon)
        return
    if status == "ERROR":
        box.label(text=f"Diff Error: {_cached_diff['msg']}", icon='ERROR')
        return

    if not _cached_diff["has_changes"]:
        box.label(text="Working tree clean.", icon='CHECKMARK')
        return

    if _cached_diff["inputs"]:
        draw_inputs_section(box)
    if _cached_diff["meshes"]:
        draw_meshes_section(box)
    if _cached_diff["trees"]:
        draw_links_section(box)

class GEONODES_JSON_PT_diff(bpy.types.Panel):
    bl_label = "Structural Changes (Uncommitted)"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'GeoJSON'

    def draw(self, context):
        draw_git_diff(self.layout)

class GEONODES_JSON_PT_config(bpy.types.Panel):
    bl_label = "Configuration"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'GeoJSON'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        groups = scene.geonodes_json_exporter_groups
        collections = scene.geonodes_json_exporter_collections

        # --- Node Groups ---
        box = layout.box()
        header_row = box.row()
        header_row.label(text="Tracked Node Groups")

        op = header_row.operator("geonodes_json.actions", text="", icon='ADD')
        op.action = 'ADD'
        op.list_type = 'GROUPS'

        if len(groups) == 0:
            box.label(text="No groups added.")

        for idx, ng in enumerate(groups):
            ng_box = box.box()
            ng_row = ng_box.row(align=True)

            ng_row.prop(ng, "group_ptr", text="")
            ng_row.prop(ng, "depth", text="Depth")
            ng_row.prop(ng, "groups_only", text="", icon='NODETREE', toggle=True)

            op = ng_row.operator("geonodes_json.actions", text="", icon='TRASH')
            op.action = 'DEL'
            op.item_idx = idx
            op.list_type = 'GROUPS'

        # --- Collections ---
        coll_box = layout.box()
        coll_header = coll_box.row()
        coll_header.label(text="Tracked Collections (Meshes)")

        op = coll_header.operator("geonodes_json.actions", text="", icon='ADD')
        op.action = 'ADD'
        op.list_type = 'COLLECTIONS'

        if len(collections) == 0:
            coll_box.label(text="No collections added.")

        for idx, coll_item in enumerate(collections):
            row = coll_box.row(align=True)
            row.prop(coll_item, "collection_ptr", text="")

            op = row.operator("geonodes_json.actions", text="", icon='TRASH')
            op.action = 'DEL'
            op.item_idx = idx
            op.list_type = 'COLLECTIONS'

        other_scenes = [s for s in bpy.data.scenes if s != scene]
        other_groups = sum(1 for s in other_scenes for g in s.geonodes_json_exporter_groups if g.group_ptr)
        other_collections = sum(1 for s in other_scenes for c in s.geonodes_json_exporter_collections if c.collection_ptr)
        if other_groups or other_collections:
            layout.label(
                text=f"Also tracked in other scenes: {other_groups} groups, {other_collections} collections",
                icon='INFO',
            )

        # --- Tracking settings ---
        track_box = layout.box()
        track_box.prop(scene, "geonodes_json_exporter_enabled")
        path_row = track_box.row()
        path_row.active = scene.geonodes_json_exporter_enabled
        path_row.prop(scene, "geonodes_json_exporter_filepath", text="")
        if _export_error:
            track_box.label(text=f"Last auto-export failed: {_export_error}", icon='ERROR')

        layout.separator()
        row = layout.row(align=True)
        row.operator("geonodes_json.export", icon='EXPORT')
        row.operator("geonodes_json.import", icon='IMPORT')

# ==============================================================================
# REGISTRATION
# ==============================================================================

classes = (
    GeoNodesExporterNodeGroup,
    GeoNodesExporterCollection,
    GEONODES_JSON_OT_export,
    GEONODES_JSON_OT_import,
    GEONODES_JSON_OT_actions,
    GEONODES_JSON_PT_diff,
    GEONODES_JSON_PT_config,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.geonodes_json_exporter_groups = bpy.props.CollectionProperty(type=GeoNodesExporterNodeGroup)
    bpy.types.Scene.geonodes_json_exporter_collections = bpy.props.CollectionProperty(type=GeoNodesExporterCollection)
    bpy.types.Scene.geonodes_json_exporter_enabled = bpy.props.BoolProperty(
        name="Tracking Enabled",
        description="Write the tracking file on every save and show its git diff",
        default=True,
    )
    bpy.types.Scene.geonodes_json_exporter_filepath = bpy.props.StringProperty(
        name="Tracking File",
        description="Tracking JSON file written on save. '//' is relative to the .blend file",
        subtype='FILE_PATH',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
        default=DEFAULT_TRACKING_PATH,
    )

    if auto_export_handler not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(auto_export_handler)
    if load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_post_handler)

def unregister():
    global _cached_diff, _export_error

    if auto_export_handler in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(auto_export_handler)
    if load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post_handler)

    del bpy.types.Scene.geonodes_json_exporter_filepath
    del bpy.types.Scene.geonodes_json_exporter_enabled
    del bpy.types.Scene.geonodes_json_exporter_collections
    del bpy.types.Scene.geonodes_json_exporter_groups

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    _cached_diff = None
    _export_error = None
