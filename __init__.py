import bpy
import json
import os
import hashlib
import array
import subprocess

# ==============================================================================
# UI CONFIGURATION
# ==============================================================================

UI_INDENT = 0.03    # Left indentation for nested elements (lower = less indent)
UI_SPACING = 0.6    # Line spacing/compactness for items (lower = more compact)

# ==============================================================================
# GLOBALS & CACHE
# ==============================================================================

_cached_diff = None

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

def extract_meshes_state(context, collections):
    state = {}
    if not context:
        return state

    target_objects = set()
    for coll_item in collections:
        coll = coll_item.collection_ptr
        if coll:
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

def export_nodes_to_json(groups, collections, filepath, context=None):
    result = {
        "tracked_inputs": {},
        "meshes": extract_meshes_state(context, collections),
        "all_node_trees": {}
    }

    unsorted_trees = {}
    for ng in bpy.data.node_groups:
        unsorted_trees[ng.name] = get_node_tree_topology(ng)

    for name in sorted(unsorted_trees.keys()):
        result["all_node_trees"][name] = unsorted_trees[name]

    unsorted_inputs = {}
    for i, ng in enumerate(groups):
        if not ng.group_ptr:
            continue

        ng_data = extract_node_tree_data(ng.group_ptr, 0, ng.depth, ng.groups_only)
        unsorted_inputs[ng.group_ptr.name] = ng_data

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

def import_nodes_from_json(groups, filepath):
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

    for i, ng in enumerate(groups):
        if not ng.group_ptr or ng.group_ptr.name not in input_data:
            continue

        group_info = input_data[ng.group_ptr.name]
        if isinstance(group_info, dict) and "nodes" in group_info:
            ng_data = group_info["nodes"]
        else:
            ng_data = group_info

        apply_node_tree_data(ng.group_ptr, ng_data, 0, ng.depth, ng.groups_only)

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
        all_input_groups = sorted(set(old_inputs.keys()).union(set(new_inputs.keys())))

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

        for group in all_input_groups:
            old_nodes = old_inputs.get(group, {})
            new_nodes = new_inputs.get(group, {})

            old_flat = flatten_inputs_dict(old_nodes, p=group)
            new_flat = flatten_inputs_dict(new_nodes, p=group)

            all_keys = sorted(set(old_flat.keys()).union(set(new_flat.keys())))
            for key in all_keys:
                parent, node, inp = key

                old_val = old_flat.get(key)
                new_val = new_flat.get(key)

                change_type = None
                if key not in old_flat:
                    change_type = 'ADD'
                elif key not in new_flat:
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


# ==============================================================================
# OPERATORS & HANDLERS
# ==============================================================================

class GEONODES_JSON_OT_export(bpy.types.Operator):
    bl_idname = "geonodes_json.export"
    bl_label = "Export Node Inputs to JSON"
    bl_description = "Extracts all inputs from configured nodes and saves to JSON"

    def execute(self, context):
        groups = context.scene.geonodes_json_exporter_groups
        collections = context.scene.geonodes_json_exporter_collections
        filepath = bpy.path.abspath(context.scene.geonodes_json_export_path)

        try:
            export_nodes_to_json(groups, collections, filepath, context)
            update_cached_diff(filepath)
            self.report({'INFO'}, f"Successfully exported to {filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}

class GEONODES_JSON_OT_import(bpy.types.Operator):
    bl_idname = "geonodes_json.import"
    bl_label = "Import Node Inputs from JSON"
    bl_description = "Applies stored inputs from JSON to configured nodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        groups = context.scene.geonodes_json_exporter_groups
        filepath = bpy.path.abspath(context.scene.geonodes_json_export_path)

        try:
            import_nodes_from_json(groups, filepath)
            context.view_layer.update()
            self.report({'INFO'}, f"Successfully imported from {filepath}")
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
    context = bpy.context
    if not getattr(context.scene, "geonodes_json_auto_export", False):
        return

    groups = getattr(context.scene, "geonodes_json_exporter_groups", None)
    collections = getattr(context.scene, "geonodes_json_exporter_collections", None)
    path = getattr(context.scene, "geonodes_json_export_path", "")
    if not groups or not path:
        return

    filepath = bpy.path.abspath(path)
    try:
        export_nodes_to_json(groups, collections, filepath, context)
        update_cached_diff(filepath)
        trigger_ui_redraw()
    except Exception:
        pass

@bpy.app.handlers.persistent
def load_post_handler(dummy):
    context = bpy.context
    path = getattr(context.scene, "geonodes_json_export_path", "")
    if path:
        filepath = bpy.path.abspath(path)
        update_cached_diff(filepath)
        trigger_ui_redraw()

# ==============================================================================
# USER INTERFACE
# ==============================================================================

def draw_git_diff(layout):
    global _cached_diff

    box = layout.box()
    box.label(text="Structural Changes (Uncommitted):", icon='FILE_TEXT')

    if _cached_diff is None:
        box.label(text="Save file or press Export to generate diff.", icon='INFO')
        return

    if _cached_diff["status"] == "NO_FILE":
        return
    elif _cached_diff["status"] == "NO_REPO":
        return
    elif _cached_diff["status"] == "NO_COMMIT":
        box.label(text="File not yet in Git or no previous commit.", icon='INFO')
        return
    elif _cached_diff["status"] == "INVALID_JSON":
        box.label(text="Committed file is not valid JSON.", icon='ERROR')
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
            pb.label(text=parent, icon='NODETREE')

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

        layout.prop(scene, "geonodes_json_export_path")
        layout.prop(scene, "geonodes_json_auto_export")

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
    bpy.types.Scene.geonodes_json_export_path = bpy.props.StringProperty(
        name="Export Filepath",
        subtype='FILE_PATH',
        default="//node_inputs.json"
    )
    bpy.types.Scene.geonodes_json_auto_export = bpy.props.BoolProperty(
        name="Auto-Export on Save",
        description="Automatically export JSON when saving the blend file",
        default=True
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

    del bpy.types.Scene.geonodes_json_auto_export
    del bpy.types.Scene.geonodes_json_export_path
    del bpy.types.Scene.geonodes_json_exporter_collections
    del bpy.types.Scene.geonodes_json_exporter_groups

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
