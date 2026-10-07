import bpy
import json
import os
import hashlib
import array
import subprocess
from bpy_extras.io_utils import ExportHelper, ImportHelper

# ==============================================================================
# UI CONFIGURATION
# ==============================================================================

UI_INDENT = 0.02    # Left indentation for nested elements (lower = less indent)
UI_SPACING = 0.6    # Line spacing/compactness for items (lower = more compact)

# ==============================================================================
# GLOBALS & CACHE
# ==============================================================================

_cached_diff = None

DEFAULT_TRACKING_PATH = "//tracking.json"  # relative to the .blend file

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

# ==============================================================================
# CORE LOGIC
# ==============================================================================

def get_node_tree_topology(node_tree):
    valid_links = []
    for link in node_tree.links:
        if link.from_node and link.to_node:
            valid_links.append(link)

    valid_links.sort(key=lambda l: (l.from_socket.name, l.to_socket.name, l.from_node.name, l.to_node.name))

    links = []
    for link in valid_links:
        l_str = f"[{link.from_node.name}] {link.from_socket.name} -> {link.to_socket.name} [{link.to_node.name}]"
        links.append(l_str)

    return links

def extract_meshes_state(collections):
    state = {}
    target_objects = set()

    for coll in collections:
        for obj in coll.all_objects:
            if obj.type == 'MESH' and obj.data:
                target_objects.add(obj)

    for obj in target_objects:
        hasher = hashlib.md5()
        loc = f"{obj.location[0]:.4f}_{obj.location[1]:.4f}_{obj.location[2]:.4f}"
        hasher.update(loc.encode('utf-8'))

        mesh = obj.data
        num_verts = len(mesh.vertices)
        if num_verts > 0:
            coords = [0.0] * (num_verts * 3)
            mesh.vertices.foreach_get("co", coords)
            float_arr = array.array('f', coords)
            hasher.update(float_arr.tobytes())

        state[obj.name] = hasher.hexdigest()

    return {k: state[k] for k in sorted(state.keys())}

def format_value(val, decimals=4):
    if isinstance(val, float):
        return round(val, decimals)
    elif isinstance(val, (list, tuple)):
        return [format_value(v, decimals) for v in val]
    return val

def get_node_input_value(socket):
    try:
        val = socket.default_value
        if hasattr(val, "to_list"):
            res = val.to_list()
        elif isinstance(val, (list, tuple)):
            res = list(val)
        elif type(val).__name__ == "bpy_prop_array":
            res = list(val)
        elif hasattr(val, "name"):
            res = val.name
        elif type(val).__name__ == "Euler":
            res = [val.x, val.y, val.z]
        elif hasattr(val, "__class__") and "Color" in val.__class__.__name__:
            res = [val.r, val.g, val.b, val.a] if len(val) == 4 else [val.r, val.g, val.b]
        else:
            res = val
        return format_value(res)
    except Exception:
        return str(getattr(socket, "default_value", None))

def extract_node_tree_data(node_tree, current_depth, max_depth, groups_only=False):
    ng_data = {}
    for target_node in node_tree.nodes:
        node_name = target_node.name
        if getattr(target_node, "label", ""):
            node_name = f"{target_node.name} ({target_node.label})"
        node_data = {}

        is_group = getattr(target_node, "type", "") == 'GROUP'

        if not groups_only or is_group:
            for inp in target_node.inputs:
                if not getattr(inp, "is_unavailable", False) and not getattr(inp, "hide", False):
                    node_data[inp.name] = get_node_input_value(inp)

        if is_group and getattr(target_node, "node_tree", None) and current_depth < max_depth:
            nested_data = extract_node_tree_data(target_node.node_tree, current_depth + 1, max_depth, groups_only)
            if nested_data:
                node_data.update(nested_data)

        if node_data:
            ng_data[node_name] = node_data

    return ng_data

def export_nodes_to_json(filepath):
    merged_groups = {}
    merged_collections = set()

    for scene in bpy.data.scenes:
        if hasattr(scene, "geonodes_json_exporter_groups"):
            for ng in scene.geonodes_json_exporter_groups:
                if ng.group_ptr:
                    ptr = ng.group_ptr
                    if ptr not in merged_groups:
                        merged_groups[ptr] = {"depth": ng.depth, "groups_only": ng.groups_only}
                    else:
                        merged_groups[ptr]["depth"] = max(merged_groups[ptr]["depth"], ng.depth)
                        merged_groups[ptr]["groups_only"] = merged_groups[ptr]["groups_only"] and ng.groups_only

        if hasattr(scene, "geonodes_json_exporter_collections"):
            for coll_item in scene.geonodes_json_exporter_collections:
                if coll_item.collection_ptr:
                    merged_collections.add(coll_item.collection_ptr)

    result = {
        "tracked_inputs": {},
        "meshes": extract_meshes_state(merged_collections),
        "all_node_trees": {}
    }

    unsorted_trees = {}
    for ng in bpy.data.node_groups:
        unsorted_trees[ng.name] = get_node_tree_topology(ng)

    for name in sorted(unsorted_trees.keys()):
        result["all_node_trees"][name] = unsorted_trees[name]

    unsorted_inputs = {}
    for ptr, config in merged_groups.items():
        ng_data = extract_node_tree_data(ptr, 0, config["depth"], config["groups_only"])
        unsorted_inputs[ptr.name] = ng_data

    for name in sorted(unsorted_inputs.keys()):
        result["tracked_inputs"][name] = unsorted_inputs[name]

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(result, f, indent=4)

def set_node_input_value(socket, val):
    try:
        current_val = socket.default_value
        if hasattr(current_val, "name") and isinstance(val, str):
            pass
        else:
            if get_node_input_value(socket) == val:
                return
            socket.default_value = val
    except Exception:
        pass

def apply_node_tree_data(node_tree, ng_data, current_depth, max_depth, groups_only=False):
    if not isinstance(ng_data, dict):
        return

    for target_node in node_tree.nodes:
        node_name = target_node.name
        if getattr(target_node, "label", ""):
            node_name = f"{target_node.name} ({target_node.label})"

        if node_name not in ng_data:
            continue

        node_data = ng_data[node_name]
        if not isinstance(node_data, dict):
            continue

        is_group = getattr(target_node, "type", "") == 'GROUP'

        if not groups_only or is_group:
            for inp in target_node.inputs:
                if not getattr(inp, "is_unavailable", False) and not getattr(inp, "hide", False):
                    if inp.name in node_data:
                        set_node_input_value(inp, node_data[inp.name])

        if is_group and getattr(target_node, "node_tree", None) and current_depth < max_depth:
            apply_node_tree_data(target_node.node_tree, node_data, current_depth + 1, max_depth, groups_only)

def import_nodes_from_json(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"JSON file not found: {filepath}")

    with open(filepath, 'r') as f:
        data = json.load(f)

    if "tracked_inputs" in data:
        input_data = data["tracked_inputs"]
    elif "node_groups" in data:
        input_data = data["node_groups"]
    else:
        input_data = data

    merged_groups = {}
    for scene in bpy.data.scenes:
        if hasattr(scene, "geonodes_json_exporter_groups"):
            for ng in scene.geonodes_json_exporter_groups:
                if ng.group_ptr:
                    ptr = ng.group_ptr
                    if ptr not in merged_groups:
                        merged_groups[ptr] = {"depth": ng.depth, "groups_only": ng.groups_only}
                    else:
                        merged_groups[ptr]["depth"] = max(merged_groups[ptr]["depth"], ng.depth)
                        merged_groups[ptr]["groups_only"] = merged_groups[ptr]["groups_only"] and ng.groups_only

    for ptr, config in merged_groups.items():
        if ptr.name not in input_data:
            continue

        group_info = input_data[ptr.name]
        if isinstance(group_info, dict) and "nodes" in group_info:
            ng_data = group_info["nodes"]
        else:
            ng_data = group_info

        apply_node_tree_data(ptr, ng_data, 0, config["depth"], config["groups_only"])

# ==============================================================================
# GIT DIFF PARSING
# ==============================================================================

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
        repo_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=dir_path, capture_output=True, text=True)
        if repo_check.returncode != 0:
            _cached_diff["status"] = "NO_REPO"
            return

        head_show = subprocess.run(["git", "show", f"HEAD:./{file_name}"], cwd=dir_path, capture_output=True, text=True)
        if head_show.returncode != 0:
            _cached_diff["status"] = "NO_COMMIT"
            return

        try:
            committed_data = json.loads(head_show.stdout)
        except json.JSONDecodeError:
            _cached_diff["status"] = "INVALID_JSON"
            return

        with open(filepath, 'r') as f:
            current_data = json.load(f)

        # 1. Compare Meshes
        old_meshes = committed_data.get("meshes", {})
        new_meshes = current_data.get("meshes", {})
        for obj in sorted(new_meshes.keys()):
            if obj not in old_meshes:
                _cached_diff["meshes"].append((obj, 'ADD'))
            elif new_meshes[obj] != old_meshes[obj]:
                _cached_diff["meshes"].append((obj, 'FILE_REFRESH'))
        for obj in sorted(old_meshes.keys()):
            if obj not in new_meshes:
                _cached_diff["meshes"].append((obj, 'REMOVE'))

        # 2. Compare Node Trees
        old_trees = committed_data.get("all_node_trees", {})
        new_trees = current_data.get("all_node_trees", {})
        all_tree_names = sorted(set(old_trees.keys()).union(set(new_trees.keys())))

        for tree_name in all_tree_names:
            old_links = set(old_trees.get(tree_name, []))
            new_links = set(new_trees.get(tree_name, []))

            added = sorted(new_links - old_links)
            removed = sorted(old_links - new_links)

            if added or removed:
                _cached_diff["trees"][tree_name] = {
                    "added": added,
                    "removed": removed
                }

        # 3. Tracked Inputs
        old_inputs = committed_data.get("tracked_inputs", {})
        new_inputs = current_data.get("tracked_inputs", {})

        def flatten_inputs_dict(nd, p=""):
            f = {}
            for n, c in nd.items():
                if isinstance(c, dict):
                    for k, v in c.items():
                        if isinstance(v, dict):
                            f.update(flatten_inputs_dict({k: v}, p=n))
                        else:
                            f[(p, n, k)] = v
            return f

        # Flatten globally first to naturally deduplicate overlapping nested groups
        global_old_flat = {}
        for group, nodes in old_inputs.items():
            global_old_flat.update(flatten_inputs_dict(nodes, p=group))

        global_new_flat = {}
        for group, nodes in new_inputs.items():
            global_new_flat.update(flatten_inputs_dict(nodes, p=group))

        all_keys = sorted(set(global_old_flat.keys()).union(set(global_new_flat.keys())))

        for key in all_keys:
            parent, node, inp = key

            old_val = global_old_flat.get(key)
            new_val = global_new_flat.get(key)

            change_type = None
            if key not in global_old_flat:
                change_type = 'ADD'
            elif key not in global_new_flat:
                change_type = 'REMOVE'
            elif old_val != new_val:
                change_type = 'CHANGE'

            if change_type:
                if parent not in _cached_diff["inputs"]:
                    _cached_diff["inputs"][parent] = {}
                if node not in _cached_diff["inputs"][parent]:
                    _cached_diff["inputs"][parent][node] = []

                _cached_diff["inputs"][parent][node].append((inp, change_type, old_val, new_val))

        if _cached_diff["meshes"] or _cached_diff["trees"] or _cached_diff["inputs"]:
            _cached_diff["has_changes"] = True

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

def default_dialog_filepath():
    return get_tracking_filepath() or "tracking.json"


# ==============================================================================
# OPERATORS & HANDLERS
# ==============================================================================

class GEONODES_JSON_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "geonodes_json.export"
    bl_label = "Export tracking to JSON"
    bl_description = "Extracts all inputs from configured nodes and saves to a specific JSON file"

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = default_dialog_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            export_nodes_to_json(self.filepath)

            tracking_path = get_tracking_filepath()
            if (is_tracking_enabled() and tracking_path
                    and os.path.normpath(self.filepath) == tracking_path):
                update_cached_diff(tracking_path)
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
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = default_dialog_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            import_nodes_from_json(self.filepath)
            context.view_layer.update()
            self.report({'INFO'}, f"Successfully imported from {self.filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}

class GEONODES_JSON_OT_actions(bpy.types.Operator):
    bl_idname = "geonodes_json.actions"
    bl_label = "Action"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.StringProperty()  # type: ignore
    item_idx: bpy.props.IntProperty(default=-1)  # type: ignore
    list_type: bpy.props.StringProperty(default="GROUPS")  # type: ignore

    def execute(self, context):
        scene = context.scene

        if self.list_type == 'GROUPS':
            target_list = scene.geonodes_json_exporter_groups
        else:
            target_list = scene.geonodes_json_exporter_collections

        if self.action == 'ADD':
            target_list.add()
        elif self.action == 'DEL':
            target_list.remove(self.item_idx)

        return {'FINISHED'}

@bpy.app.handlers.persistent
def auto_export_handler(dummy):
    if not is_tracking_enabled():
        return
    filepath = get_tracking_filepath()
    if not filepath:
        return

    try:
        export_nodes_to_json(filepath)
        update_cached_diff(filepath)
        trigger_ui_redraw()
    except Exception:
        pass

@bpy.app.handlers.persistent
def load_post_handler(dummy):
    if not is_tracking_enabled():
        return
    filepath = get_tracking_filepath()
    if filepath:
        update_cached_diff(filepath)
        trigger_ui_redraw()

# ==============================================================================
# USER INTERFACE
# ==============================================================================

def draw_git_diff(layout):
    global _cached_diff

    box = layout.box()
    box.label(text="Structural Changes (Uncommitted):", icon='FILE_TEXT')

    if not is_tracking_enabled():
        box.label(text="Tracking is disabled.", icon='INFO')
        return

    tracking_file = get_tracking_filepath()
    if not tracking_file:
        box.label(text="Save the .blend file to enable auto-tracking.", icon='INFO')
        return

    if _cached_diff is None:
        box.label(text="Press Save or Export to generate diff.", icon='INFO')
        return

    if _cached_diff["status"] == "NO_FILE":
        return
    elif _cached_diff["status"] == "NO_REPO":
        return
    elif _cached_diff["status"] == "NO_COMMIT":
        box.label(text="File not yet in Git or no previous commit.", icon='INFO')
        return
    elif _cached_diff["status"] == "INVALID_JSON":
        box.label(text="Committed tracking file is not valid JSON.", icon='ERROR')
        return
    elif _cached_diff["status"] == "ERROR":
        box.label(text=f"Diff Error: {_cached_diff['msg']}", icon='ERROR')
        return

    if not _cached_diff["has_changes"]:
        box.label(text="Working tree clean.", icon='CHECKMARK')
        return

    # 1. Inputs Block
    if _cached_diff["inputs"]:
        total_inputs_changed = sum(len(changes) for nodes in _cached_diff["inputs"].values() for changes in nodes.values())
        main_inputs_box = box.box()
        main_inputs_box.label(text=f"Inputs ({total_inputs_changed} inputs changed)", icon='PREFERENCES')

        for parent, nodes in _cached_diff["inputs"].items():
            pb = main_inputs_box.box()
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
                    row = ib.row(align=True)
                    row.scale_y = UI_SPACING
                    row.alignment = 'LEFT'

                    if change_type == 'ADD':
                        row.label(text=f"{inp}: {new_val}", icon='ADD')
                    elif change_type == 'REMOVE':
                        row.label(text=f"{inp}: {old_val}", icon='REMOVE')
                    elif change_type == 'CHANGE':
                        row.label(text=f"{inp}:", icon='DOT')
                        row.label(text=str(old_val))
                        row.label(text="", icon='FORWARD')
                        row.label(text=str(new_val))

    # 2. Meshes Block
    if _cached_diff["meshes"]:
        main_meshes_box = box.box()
        obj_count = len(_cached_diff["meshes"])
        main_meshes_box.label(text=f"Meshes ({obj_count} objects changed)", icon='MESH_DATA')

        for msg, icon in _cached_diff["meshes"]:
            mesh_box = main_meshes_box.box()
            mesh_box.scale_y = UI_SPACING
            mesh_box.label(text=msg, icon=icon)

    # 3. Links Block
    if _cached_diff["trees"]:
        total_added = sum(len(t["added"]) for t in _cached_diff["trees"].values())
        total_removed = sum(len(t["removed"]) for t in _cached_diff["trees"].values())
        main_links_box = box.box()
        main_links_box.label(text=f"Links ({total_added} added, {total_removed} removed)", icon='LINKED')

        for tree_name, tree_data in _cached_diff["trees"].items():
            b = main_links_box.box()
            b.label(text=tree_name, icon='NODETREE')

            for link in tree_data["removed"]:
                link_box = b.box()
                link_box.scale_y = UI_SPACING
                parts = link.split(" -> ")
                if len(parts) == 2:
                    row = link_box.row(align=True)
                    row.alignment = 'LEFT'
                    row.label(text=parts[0][:50] + ("..." if len(parts[0]) > 50 else ""))
                    row.label(text="", icon='UNLINKED')
                    row.separator()
                    row.label(text=parts[1][:50] + ("..." if len(parts[1]) > 50 else ""))
                else:
                    display = link[:100] + "..." if len(link) > 100 else link
                    link_box.label(text=display, icon='UNLINKED')

            for link in tree_data["added"]:
                link_box = b.box()
                link_box.scale_y = UI_SPACING
                parts = link.split(" -> ")
                if len(parts) == 2:
                    row = link_box.row(align=True)
                    row.alignment = 'LEFT'
                    row.label(text=parts[0][:50] + ("..." if len(parts[0]) > 50 else ""))
                    row.label(text="", icon='LINKED')
                    row.separator()
                    row.label(text=parts[1][:50] + ("..." if len(parts[1]) > 50 else ""))
                else:
                    display = link[:100] + "..." if len(link) > 100 else link
                    link_box.label(text=display, icon='LINKED')


class GEONODES_JSON_PT_panel(bpy.types.Panel):
    bl_label = "Geometry Nodes JSON Exporter"
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

        # --- Tracking settings ---
        track_box = layout.box()
        track_box.prop(scene, "geonodes_json_exporter_enabled")
        path_row = track_box.row()
        path_row.active = scene.geonodes_json_exporter_enabled
        path_row.prop(scene, "geonodes_json_exporter_filepath", text="")

        layout.separator()
        row = layout.row(align=True)
        row.operator("geonodes_json.export", icon='EXPORT')
        row.operator("geonodes_json.import", icon='IMPORT')

        layout.separator()

        # --- Git Diff Display ---
        draw_git_diff(layout)

# ==============================================================================
# REGISTRATION
# ==============================================================================

classes = (
    GeoNodesExporterNodeGroup,
    GeoNodesExporterCollection,
    GEONODES_JSON_OT_export,
    GEONODES_JSON_OT_import,
    GEONODES_JSON_OT_actions,
    GEONODES_JSON_PT_panel,
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
        default=DEFAULT_TRACKING_PATH,
    )

    if auto_export_handler not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(auto_export_handler)
    if load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_post_handler)

def unregister():
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

if __name__ == "__main__":
    register()
