import sys

# Dynamic sys.path bootstrapping for CLI execution
if __name__ == "__main__":
    import os
    sys.path.append(os.path.dirname(__file__))

if "bpy" in locals():
    import importlib
    if "properties" in locals():
        importlib.reload(core)
        importlib.reload(properties)
        importlib.reload(operators)
        importlib.reload(ui)

import bpy
from . import properties, operators, ui, core

def register():
    properties.register()
    operators.register()
    ui.register()

def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()

if __name__ == "__main__":
    register()
