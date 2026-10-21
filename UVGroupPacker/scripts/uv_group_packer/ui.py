"""Qt window for UV Group Packer (PySide6 in Maya 2025+, PySide2 in older versions)."""
import maya.cmds as cmds
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:  # Maya 2024 and older
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance

from . import __version__
from . import groups as uvgroups
from . import packer

WINDOW_OBJECT = "UVGroupPackerWindow"

COL_COLOR, COL_NAME, COL_FACES, COL_WEIGHT, COL_LOCK = range(5)


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None


def _qcolor(rgb):
    return QtGui.QColor.fromRgbF(*[min(max(c, 0.0), 1.0) for c in rgb])


def _swatch(rgb, size=14):
    pix = QtGui.QPixmap(size, size)
    pix.fill(_qcolor(rgb))
    return QtGui.QIcon(pix)


class BoxPreview(QtWidgets.QWidget):
    """Draws the computed group boxes over the UV tile(s)."""

    def __init__(self, parent=None):
        super(BoxPreview, self).__init__(parent)
        self.setMinimumHeight(220)
        self._boxes = {}
        self._colors = {}

    def set_boxes(self, boxes, colors):
        self._boxes = dict(boxes)
        self._colors = dict(colors)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(43, 43, 43))
        if not self._boxes:
            painter.setPen(QtGui.QColor(140, 140, 140))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Preview Boxes to see the layout")
            return

        # Whole tiles that contain boxes, so UDIM layouts show every tile.
        u_min = min(int(b[0] // 1) for b in self._boxes.values())
        v_min = min(int(b[1] // 1) for b in self._boxes.values())
        u_max = max(int(-(-b[2] // 1)) for b in self._boxes.values())
        v_max = max(int(-(-b[3] // 1)) for b in self._boxes.values())
        tiles_u, tiles_v = max(1, u_max - u_min), max(1, v_max - v_min)

        margin = 8
        side = min((self.width() - 2 * margin) / float(tiles_u),
                   (self.height() - 2 * margin) / float(tiles_v))
        ox = (self.width() - side * tiles_u) / 2.0
        oy = (self.height() - side * tiles_v) / 2.0

        def to_px(u, v):
            return (ox + (u - u_min) * side, oy + (v_max - v) * side)

        painter.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90), 1))
        painter.setBrush(QtGui.QColor(60, 60, 60))
        for tu in range(tiles_u):
            for tv in range(tiles_v):
                painter.drawRect(QtCore.QRectF(ox + tu * side, oy + tv * side, side, side))

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for name, (u0, v0, u1, v1) in self._boxes.items():
            x0, y1 = to_px(u0, v0)
            x1, y0 = to_px(u1, v1)
            rect = QtCore.QRectF(x0, y0, x1 - x0, y1 - y0)
            color = _qcolor(self._colors.get(name, (1, 1, 1)))
            fill = QtGui.QColor(color)
            fill.setAlpha(50)
            painter.setBrush(fill)
            painter.setPen(QtGui.QPen(color, 2))
            painter.drawRect(rect)
            painter.drawText(rect.adjusted(4, 2, -2, -2),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, name)


class UVGroupPackerWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(UVGroupPackerWindow, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle("UV Group Packer %s" % __version__)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self.resize(380, 720)
        self._updating = False
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        group_box = QtWidgets.QGroupBox("Groups")
        gl = QtWidgets.QVBoxLayout(group_box)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["", "Group", "Faces", "Weight", "Lock"])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(150)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QtWidgets.QHeaderView.Stretch)
        for col, width in ((COL_COLOR, 26), (COL_FACES, 56), (COL_WEIGHT, 64), (COL_LOCK, 40)):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.Fixed)
            self.tree.setColumnWidth(col, width)
        self.tree.setToolTip("Double-click the colour to change it, the name to rename,\n"
                             "the weight to make a group's box bigger or smaller.")
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        gl.addWidget(self.tree)

        row1 = QtWidgets.QHBoxLayout()
        row2 = QtWidgets.QHBoxLayout()
        for row, label, slot, tip in (
                (row1, "New", self._new_group, "Create a group from the selected faces/objects"),
                (row1, "Assign", self._assign, "Move the selected faces into the highlighted group"),
                (row1, "Remove", self._remove, "Remove the selected faces from every group"),
                (row2, "Select", self._select, "Select the faces of the highlighted groups"),
                (row2, "Delete", self._delete, "Delete the highlighted groups (faces stay untouched)"),
                (row2, "From Materials", self._from_materials,
                 "One group per material, for the selection or the whole scene"),
                (row2, "Refresh", self.refresh, "Re-read the groups from the scene")):
            btn = QtWidgets.QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        gl.addLayout(row1)
        gl.addLayout(row2)
        layout.addWidget(group_box, 1)

        pack_box = QtWidgets.QGroupBox("Packing")
        form = QtWidgets.QFormLayout(pack_box)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Groups To Single Tile", packer.MODE_SINGLE_TILE)
        self.mode.addItem("Groups To Tiles (UDIM)", packer.MODE_TILES)
        self.mode.currentIndexChanged.connect(self._preview)
        form.addRow("Mode", self.mode)

        self.resolution = QtWidgets.QComboBox()
        for res in (512, 1024, 2048, 4096, 8192):
            self.resolution.addItem(str(res), res)
        self.resolution.setCurrentIndex(2)
        form.addRow("Texture size", self.resolution)

        self.padding = QtWidgets.QDoubleSpinBox()
        self.padding.setRange(0, 128)
        self.padding.setValue(4)
        self.padding.setSuffix(" px")
        form.addRow("Shell padding", self.padding)

        self.gap = QtWidgets.QDoubleSpinBox()
        self.gap.setRange(0, 256)
        self.gap.setValue(8)
        self.gap.setSuffix(" px")
        self.gap.valueChanged.connect(self._preview)
        form.addRow("Box gap", self.gap)

        self.rotation = QtWidgets.QComboBox()
        self.rotation.addItem("90°", 90)
        self.rotation.addItem("45°", 45)
        self.rotation.addItem("None", 0)
        form.addRow("Rotation step", self.rotation)

        self.area = QtWidgets.QComboBox()
        self.area.addItem("3D surface area", packer.AREA_3D)
        self.area.addItem("Current UV area", packer.AREA_UV)
        self.area.setToolTip("What decides each group's box size in single tile mode")
        self.area.currentIndexChanged.connect(self._preview)
        form.addRow("Box size from", self.area)

        tile_row = QtWidgets.QHBoxLayout()
        self.tile_u = QtWidgets.QSpinBox()
        self.tile_v = QtWidgets.QSpinBox()
        for spin, prefix in ((self.tile_u, "U "), (self.tile_v, "V ")):
            spin.setRange(0, 99)
            spin.setPrefix(prefix)
            spin.valueChanged.connect(self._preview)
            tile_row.addWidget(spin)
        form.addRow("Start tile", tile_row)

        self.split = QtWidgets.QCheckBox("Split UV shells shared by two groups")
        self.split.setChecked(True)
        self.split.setToolTip("Cuts UV seams along group borders before packing")
        form.addRow(self.split)
        layout.addWidget(pack_box)

        self.preview = BoxPreview()
        layout.addWidget(self.preview, 1)

        buttons = QtWidgets.QHBoxLayout()
        preview_btn = QtWidgets.QPushButton("Preview Boxes")
        preview_btn.clicked.connect(self._preview)
        pack_btn = QtWidgets.QPushButton("Pack")
        pack_btn.setMinimumHeight(32)
        pack_btn.setStyleSheet("font-weight: bold;")
        pack_btn.clicked.connect(self._pack)
        buttons.addWidget(preview_btn)
        buttons.addWidget(pack_btn, 1)
        layout.addLayout(buttons)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # -------------------------------------------------------------- helpers
    def options(self):
        return packer.PackOptions(
            mode=self.mode.currentData(),
            resolution=self.resolution.currentData(),
            shell_padding_px=self.padding.value(),
            box_gap_px=self.gap.value(),
            rotate_step=self.rotation.currentData(),
            area_source=self.area.currentData(),
            tile_u=self.tile_u.value(),
            tile_v=self.tile_v.value(),
            split_shared_shells=self.split.isChecked())

    def _selected_names(self):
        return [item.data(COL_NAME, QtCore.Qt.UserRole) for item in self.tree.selectedItems()]

    def _current_name(self):
        names = self._selected_names()
        if not names:
            self._message("Highlight a group in the list first.")
            return None
        return names[0]

    def _message(self, text):
        self.status.setText(text)

    def refresh(self):
        selected = set(self._selected_names())
        self._updating = True
        self.tree.clear()
        for g in uvgroups.list_groups():
            item = QtWidgets.QTreeWidgetItem()
            item.setIcon(COL_COLOR, _swatch(g["color"]))
            item.setText(COL_NAME, g["name"])
            item.setData(COL_NAME, QtCore.Qt.UserRole, g["name"])
            item.setText(COL_FACES, str(g["count"]))
            item.setText(COL_WEIGHT, "%.2f" % g["weight"])
            item.setCheckState(COL_LOCK, QtCore.Qt.Checked if g["locked"] else QtCore.Qt.Unchecked)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsUserCheckable)
            self.tree.addTopLevelItem(item)
            item.setSelected(g["name"] in selected)
        self._updating = False
        self._preview()

    # -------------------------------------------------------------- slots
    def _on_item_double_clicked(self, item, column):
        name = item.data(COL_NAME, QtCore.Qt.UserRole)
        if column == COL_COLOR:
            g = next((g for g in uvgroups.list_groups() if g["name"] == name), None)
            color = QtWidgets.QColorDialog.getColor(_qcolor(g["color"]) if g else QtGui.QColor(),
                                                    self, "Group colour")
            if color.isValid():
                uvgroups.set_color(name, (color.redF(), color.greenF(), color.blueF()))
                self.refresh()
        elif column in (COL_NAME, COL_WEIGHT):
            self.tree.editItem(item, column)

    def _on_item_changed(self, item, column):
        if self._updating:
            return
        name = item.data(COL_NAME, QtCore.Qt.UserRole)
        if column == COL_NAME:
            uvgroups.rename_group(name, item.text(COL_NAME))
        elif column == COL_WEIGHT:
            try:
                uvgroups.set_weight(name, float(item.text(COL_WEIGHT)))
            except ValueError:
                self._message("Weight must be a number.")
        elif column == COL_LOCK:
            uvgroups.set_locked(name, item.checkState(COL_LOCK) == QtCore.Qt.Checked)
        QtCore.QTimer.singleShot(0, self.refresh)

    def _on_tree_selection(self):
        names = self._selected_names()
        if names:
            cmds.select(clear=True)
            for name in names:
                uvgroups.select_group(name, add=True)

    def _new_group(self):
        faces = uvgroups.selected_faces()
        default = "group%d" % (len(uvgroups.list_groups()) + 1)
        name, ok = QtWidgets.QInputDialog.getText(self, "New group", "Group name:", text=default)
        if not ok or not name.strip():
            return
        name = uvgroups.create_group(name, faces=faces)
        self._message("Created group '%s' with %d faces." % (name, uvgroups.count_faces(faces)))
        self.refresh()

    def _assign(self):
        name = self._current_name()
        if not name:
            return
        count = uvgroups.assign(name, cmds.ls(selection=True))
        self._message("Assigned %d faces to '%s'." % (count, name) if count
                      else "Select faces or objects to assign.")
        self.refresh()

    def _remove(self):
        uvgroups.unassign(cmds.ls(selection=True))
        self.refresh()

    def _select(self):
        names = self._selected_names()
        cmds.select(clear=True)
        for name in names:
            uvgroups.select_group(name, add=True)

    def _delete(self):
        for name in self._selected_names():
            uvgroups.delete_group(name)
        self.refresh()

    def _from_materials(self):
        created = uvgroups.groups_from_materials()
        self._message("Groups from materials: %s" % (", ".join(created) or "none found"))
        self.refresh()

    def _preview(self, *args):
        try:
            group_list = [g for g in uvgroups.list_groups() if g["faces"] and not g["locked"]]
            boxes = packer.compute_group_boxes(group_list, self.options())
        except Exception as exc:  # keep the window usable if the scene is odd
            self._message("Preview failed: %s" % exc)
            return
        self.preview.set_boxes(boxes, {g["name"]: g["color"] for g in group_list})

    def _pack(self):
        options = self.options()
        try:
            boxes = packer.pack_groups(options)
        except Exception as exc:
            self._message("Pack failed: %s" % exc)
            raise
        if not boxes:
            self._message("Nothing to pack: create groups (or unlock them) first.")
            return
        self._message("Packed %d group(s). Ctrl+Z undoes the whole pack." % len(boxes))
        self.refresh()


_window = None


def show():
    global _window
    # Close a window left over from a previous reload of this module.
    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == WINDOW_OBJECT:
            widget.close()
            widget.deleteLater()
    _window = UVGroupPackerWindow()
    _window.show()
    return _window
