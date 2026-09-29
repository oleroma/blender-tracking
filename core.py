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
