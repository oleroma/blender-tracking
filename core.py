"""
Core Logic Module
Extracts inputs from target nodes and stores them in JSON.
"""
import bpy
import json
import os

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

def export_nodes_to_json(groups, filepath):
    result = {}
    
    for i, ng in enumerate(groups):
        if not ng.group_ptr:
            continue
        
        ng_data = extract_node_tree_data(ng.group_ptr, 0, ng.depth, ng.groups_only)
        result[ng.group_ptr.name] = ng_data
        
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
        
    for i, ng in enumerate(groups):
        if not ng.group_ptr or ng.group_ptr.name not in data:
            continue
            
        ng_data = data[ng.group_ptr.name]
        apply_node_tree_data(ng.group_ptr, ng_data, 0, ng.depth, ng.groups_only)
