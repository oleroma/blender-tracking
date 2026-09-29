"""
Core Logic Module
Extracts inputs from target nodes and stores them in JSON.
"""
import bpy
import json
import os

def get_node_input_value(socket):
    try:
        val = socket.default_value
        if hasattr(val, "to_list"):
            return val.to_list()
        elif isinstance(val, (list, tuple)):
            return list(val)
        elif type(val).__name__ == "bpy_prop_array":
            return list(val)
        elif hasattr(val, "name"):
            return val.name
        elif type(val).__name__ == "Euler":
            return [val.x, val.y, val.z]
        elif hasattr(val, "__class__") and "Color" in val.__class__.__name__:
            return [val.r, val.g, val.b, val.a] if len(val) == 4 else [val.r, val.g, val.b]
        return val
    except Exception:
        return str(getattr(socket, "default_value", None))

def export_nodes_to_json(groups, filepath):
    result = {}
    
    for i, ng in enumerate(groups):
        if not ng.group_ptr:
            continue
        
        ng_data = {}
        for target_node in ng.group_ptr.nodes:
            node_name = target_node.name
            node_data = {}
            
            for inp in target_node.inputs:
                if not getattr(inp, "is_unavailable", False) and not getattr(inp, "hide", False):
                    node_data[inp.name] = get_node_input_value(inp)
            
            if node_data:
                ng_data[node_name] = node_data
            
        result[f"Group_{i}_{ng.group_ptr.name}"] = ng_data
        
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(result, f, indent=4)
