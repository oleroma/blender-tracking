"""
Core Logic Module
Extracts inputs from target nodes and stores them in JSON.
"""
import bpy
import json
import os
import hashlib

def get_node_tree_topology(node_tree):
    valid_links = []
    for link in node_tree.links:
        if link.from_node and link.to_node:
            valid_links.append(link)
            
    # Sort primarily by the socket names
    valid_links.sort(key=lambda l: (l.from_socket.name, l.to_socket.name, l.from_node.name, l.to_node.name))
    
    links = []
    for link in valid_links:
        # Using a clean format for easy readability in git diffs
        l_str = f"{link.from_node.name}:{link.from_socket.name} -> {link.to_node.name}:{link.to_socket.name}"
        links.append(l_str)
        
    return links

def extract_meshes_state(context):
    state = {}
    if not context:
        return state
        
    import array
    for obj in context.scene.objects:
        if obj.type == 'MESH' and obj.data:
            hasher = hashlib.md5()
            
            # Include base object location
            loc = f"{obj.location[0]:.4f}_{obj.location[1]:.4f}_{obj.location[2]:.4f}"
            hasher.update(loc.encode('utf-8'))
            
            # Include exact vertex positions using high-performance C API
            mesh = obj.data
            num_verts = len(mesh.vertices)
            if num_verts > 0:
                coords = [0.0] * (num_verts * 3)
                mesh.vertices.foreach_get("co", coords)
                
                # Convert float list to bytes instantly
                float_arr = array.array('f', coords)
                hasher.update(float_arr.tobytes())
                
            state[obj.name] = hasher.hexdigest()
            
    # Sort the dictionary by object name to guarantee clean git diffs
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

def export_nodes_to_json(groups, filepath, context=None):
    result = {
        "tracked_inputs": {},
        "meshes": extract_meshes_state(context),
        "all_node_trees": {}
    }
    
    # 1. Automatically track topology of EVERY node group in the file
    unsorted_trees = {}
    for ng in bpy.data.node_groups:
        unsorted_trees[ng.name] = get_node_tree_topology(ng)
        
    for name in sorted(unsorted_trees.keys()):
        result["all_node_trees"][name] = unsorted_trees[name]
        
    # 2. Extract inputs for the explicitly configured groups
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
            pass # Skip ID block lookups for safety
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
        
    # Backwards compatibility: support v3, v2, and v1 formats
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
        
        # Handle the intermediate v2 format where it was nested under "nodes"
        if isinstance(group_info, dict) and "nodes" in group_info:
            ng_data = group_info["nodes"]
        else:
            ng_data = group_info
            
        apply_node_tree_data(ng.group_ptr, ng_data, 0, ng.depth, ng.groups_only)
