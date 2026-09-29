"""
Properties Module
Contains custom Blender data structures extending PropertyGroup, defining
how the addon configuration data is saved within the blend file.
"""
import bpy

class GeoNodesExporterNodeGroup(bpy.types.PropertyGroup):
    group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Node Group")
    depth: bpy.props.IntProperty(name="Depth", default=0, min=0)
    groups_only: bpy.props.BoolProperty(name="Groups Only", default=False)

classes = (
    GeoNodesExporterNodeGroup,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.geonodes_json_exporter_groups = bpy.props.CollectionProperty(type=GeoNodesExporterNodeGroup)
    bpy.types.Scene.geonodes_json_export_path = bpy.props.StringProperty(
        name="Export Filepath",
        subtype='FILE_PATH',
        default="//node_inputs.json"
    )

def unregister():
    del bpy.types.Scene.geonodes_json_export_path
    del bpy.types.Scene.geonodes_json_exporter_groups
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
