"""Drag and drop this file into the Maya viewport to install UV Group Packer.

It adds a shelf button on the current shelf that loads the tool from this
folder, so keep the folder where it is (or drop the file again after moving it).
"""
import os
import sys

import maya.cmds as cmds
import maya.mel as mel

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts").replace("\\", "/")

SHELF_COMMAND = """import sys
_p = r"{path}"
if _p not in sys.path:
    sys.path.insert(0, _p)
import uv_group_packer
uv_group_packer.show()
""".format(path=SCRIPTS)


def install():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)

    shelf_top = mel.eval("$_uvgp_tmp = $gShelfTopLevel")
    shelf = cmds.tabLayout(shelf_top, query=True, selectTab=True)
    for button in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
        if cmds.shelfButton(button, query=True, exists=True) and \
                cmds.shelfButton(button, query=True, label=True) == "UVGroupPacker":
            cmds.deleteUI(button)
    cmds.shelfButton(parent=shelf, label="UVGroupPacker", imageOverlayLabel="UVGP",
                     annotation="UV Group Packer: pack UV groups into boxes / UDIM tiles",
                     image1="polyLayoutUV.png", sourceType="python", command=SHELF_COMMAND)

    import uv_group_packer
    uv_group_packer.show()
    cmds.inViewMessage(amg="UV Group Packer installed on shelf <hl>%s</hl>" % shelf,
                       pos="midCenter", fade=True)


def onMayaDroppedPythonFile(*args):
    install()
