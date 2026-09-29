"""
Operators Module
Defines interactive functions attached to UI elements, manipulating the PropertyGroups
or firing off execution commands (e.g., initiating export tasks).
"""
import bpy
import json
from .core import export_nodes_to_json, import_nodes_from_json

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
            
            # Force a single, full scene update after all values have been successfully set
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
    ng_idx: bpy.props.IntProperty(default=-1)  # type: ignore
    
    def execute(self, context):
        groups = context.scene.geonodes_json_exporter_groups
        
        if self.action == 'ADD_GROUP':
            ng = groups.add()
        elif self.action == 'DEL_GROUP':
            groups.remove(self.ng_idx)
            
        return {'FINISHED'}

classes = (
    GEONODES_JSON_OT_export,
    GEONODES_JSON_OT_import,
    GEONODES_JSON_OT_actions,
)

@bpy.app.handlers.persistent
def auto_export_handler(dummy):
    context = bpy.context
    if not getattr(context.scene, "geonodes_json_auto_export", False):
        return
        
    groups = getattr(context.scene, "geonodes_json_exporter_groups", None)
    path = getattr(context.scene, "geonodes_json_export_path", "")
    if not groups or not path: 
        return
        
    filepath = bpy.path.abspath(path)
    try:
        from .core import export_nodes_to_json
        export_nodes_to_json(groups, filepath)
    except Exception:
        pass

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if auto_export_handler not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(auto_export_handler)

def unregister():
    if auto_export_handler in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(auto_export_handler)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
