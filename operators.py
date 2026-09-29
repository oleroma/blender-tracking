"""
Operators Module
Defines interactive functions attached to UI elements, manipulating the PropertyGroups
or firing off execution commands (e.g., initiating export tasks).
"""
import bpy
import json
from .core import export_nodes_to_json

class GEONODES_JSON_OT_export(bpy.types.Operator):
    bl_idname = "geonodes_json.export"
    bl_label = "Export Node Inputs to JSON"
    bl_description = "Extracts all inputs from configured nodes and saves to JSON"
    
    def execute(self, context):
        groups = context.scene.geonodes_json_exporter_groups
        filepath = bpy.path.abspath(context.scene.geonodes_json_export_path)
        
        try:
            export_nodes_to_json(groups, filepath)
            self.report({'INFO'}, f"Successfully exported to {filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {str(e)}")
            return {'CANCELLED'}
            
        return {'FINISHED'}

class GEONODES_JSON_OT_actions(bpy.types.Operator):
    bl_idname = "geonodes_json.actions"
    bl_label = "Action"
    bl_options = {'REGISTER', 'UNDO'}
    
    action: bpy.props.StringProperty()
    ng_idx: bpy.props.IntProperty(default=-1)
    
    def execute(self, context):
        groups = context.scene.geonodes_json_exporter_groups
        
        if self.action == 'ADD_GROUP':
            ng = groups.add()
        elif self.action == 'DEL_GROUP':
            groups.remove(self.ng_idx)
            
        return {'FINISHED'}

classes = (
    GEONODES_JSON_OT_export,
    GEONODES_JSON_OT_actions,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
