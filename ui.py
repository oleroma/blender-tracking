"""
User Interface Module
Registers UIList and Panel classes for the 3D viewport side-bar.
"""
import bpy

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
        
        box = layout.box()
        header_row = box.row()
        header_row.label(text="Node Groups")
        
        op = header_row.operator("geonodes_json.actions", text="", icon='ADD')
        op.action = 'ADD_GROUP'
        
        if len(groups) == 0:
            box.label(text="No groups added.")
        
        for ng_idx, ng in enumerate(groups):
            ng_box = box.box()
            ng_row = ng_box.row(align=True)
            
            ng_row.prop(ng, "group_ptr", text="")
            ng_row.prop(ng, "depth", text="Depth")
            ng_row.prop(ng, "groups_only", text="", icon='GROUP', toggle=True)
            
            op = ng_row.operator("geonodes_json.actions", text="", icon='TRASH')
            op.action = 'DEL_GROUP'
            op.ng_idx = ng_idx

        layout.separator()
        row = layout.row(align=True)
        row.operator("geonodes_json.export", icon='EXPORT')
        row.operator("geonodes_json.import", icon='IMPORT')

classes = (
    GEONODES_JSON_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
