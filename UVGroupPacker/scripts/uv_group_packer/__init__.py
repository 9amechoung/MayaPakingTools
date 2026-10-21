"""UV Group Packer - pack UV groups into boxes of one tile, or into separate UDIM tiles.

Usage in Maya:
    import uv_group_packer
    uv_group_packer.show()
"""
__version__ = "0.1.0"


def show():
    from . import ui
    return ui.show()
