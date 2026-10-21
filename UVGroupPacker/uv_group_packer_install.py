"""Drag and drop this file into the Maya viewport to install UV Group Packer.

It adds a shelf button on the current shelf that loads the tool from this
folder, so keep the folder where it is (or drop the file again after moving it).

Maya imports a dropped file as a module named after the file, and reuses a
module of that name if one is already loaded. That is why this file has a
unique name (a plain "install.py" could run another tool's installer), and
why it removes itself from sys.modules when it is done: dropping the file from
a newer folder later must run the new file, not this one.
"""
import os
import sys

import maya.cmds as cmds
import maya.mel as mel

PACKAGE = "uv_group_packer"
SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts").replace("\\", "/")

# Runs on every shelf click: drop any loaded copy of the package so the click
# always uses the code in this folder, even after an update without a restart.
SHELF_COMMAND = """import sys
_p = r"{path}"
if _p not in sys.path:
    sys.path.insert(0, _p)
for _m in [m for m in sys.modules if m == "{pkg}" or m.startswith("{pkg}.")]:
    del sys.modules[_m]
import {pkg}
{pkg}.show()
""".format(path=SCRIPTS, pkg=PACKAGE)


def _forget_old_copies():
    """Unload the package and remove other folders that contain it from sys.path."""
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
        del sys.modules[name]
    for path in list(sys.path):
        same = os.path.normcase(os.path.abspath(path)) == os.path.normcase(SCRIPTS)
        if not same and os.path.isdir(os.path.join(path, PACKAGE)):
            sys.path.remove(path)
    if SCRIPTS in sys.path:
        sys.path.remove(SCRIPTS)
    sys.path.insert(0, SCRIPTS)


def install():
    _forget_old_copies()

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
    cmds.inViewMessage(amg="UV Group Packer %s installed on shelf <hl>%s</hl>"
                       % (uv_group_packer.__version__, shelf),
                       pos="midCenter", fade=True)


def onMayaDroppedPythonFile(*args):
    try:
        install()
    finally:
        sys.modules.pop(__name__, None)
