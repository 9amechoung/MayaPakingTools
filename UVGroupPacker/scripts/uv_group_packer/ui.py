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
from . import align
from . import align_math
from . import display
from . import groups as uvgroups
from . import islands as uvislands
from . import layout as uvlayout
from . import packer

WINDOW_OBJECT = "UVGroupPackerWindow"

COL_COLOR, COL_NAME, COL_FACES, COL_WEIGHT, COL_LOCK, COL_TD = range(6)

# Option rows shown per mode; rows not listed for a mode are hidden.
MODE_ROWS = {
    packer.MODE_SINGLE_TILE: ("gap", "area", "tile", "td"),
    packer.MODE_TILES: ("gap", "tile", "per_row", "td"),
    packer.MODE_ATLAS: ("gap", "tile", "atlas", "cells", "td"),
    packer.MODE_DYNAMIC: ("tile", "per_row", "fill"),
    packer.MODE_TILE_RANGE: ("tile", "per_row", "count", "fill"),
}

COMPASS = (("NW", "N", "NE"), ("W", None, "E"), ("SW", "S", "SE"))


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
    """Draws the UV tiles, the islands in their group colours and the group boxes
    (or the tiles of an island mode) on top, like UVPackmaster's UV overlay."""

    def __init__(self, parent=None):
        super(BoxPreview, self).__init__(parent)
        self.setMinimumHeight(260)
        self._boxes = {}
        self._colors = {}
        self._custom = set()
        # [(rgb, [face polygons], [border segments], bbox, label, label (u, v))]
        self._islands = []

    def set_boxes(self, boxes, colors, custom=()):
        self._boxes = dict(boxes)
        self._colors = dict(colors)
        self._custom = set(custom)
        self.update()

    def set_islands(self, islands):
        self._islands = list(islands)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(43, 43, 43))
        regions = list(self._boxes.values()) + [i[3] for i in self._islands]
        if not regions:
            painter.setPen(QtGui.QColor(140, 140, 140))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Preview Boxes to see the layout")
            return

        # Whole tiles that contain boxes or islands, so UDIM layouts show every tile.
        u_min = min(int(b[0] // 1) for b in regions)
        v_min = min(int(b[1] // 1) for b in regions)
        u_max = max(int(-(-b[2] // 1)) for b in regions)
        v_max = max(int(-(-b[3] // 1)) for b in regions)
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

        # Islands: faces filled with the group colour, outlined along the border.
        for rgb, faces, borders, _, _, _ in self._islands:
            color = _qcolor(rgb)
            fill = QtGui.QColor(color)
            fill.setAlpha(110)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(fill)
            for face in faces:
                painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*to_px(u, v)) for u, v in face]))
            painter.setPen(QtGui.QPen(color.lighter(130), 1))
            for (ua, va), (ub, vb) in borders:
                painter.drawLine(QtCore.QPointF(*to_px(ua, va)), QtCore.QPointF(*to_px(ub, vb)))
        # Group names on their biggest island.
        for rgb, _, _, _, label, at in self._islands:
            if label:
                x, y = to_px(*at)
                painter.setPen(QtGui.QColor(0, 0, 0))
                painter.drawText(QtCore.QPointF(x + 1, y + 1), label)
                painter.setPen(_qcolor(rgb).lighter(160))
                painter.drawText(QtCore.QPointF(x, y), label)

        painter.setBrush(QtCore.Qt.NoBrush)
        for name, (u0, v0, u1, v1) in self._boxes.items():
            x0, y1 = to_px(u0, v0)
            x1, y0 = to_px(u1, v1)
            rect = QtCore.QRectF(x0, y0, x1 - x0, y1 - y0)
            color = _qcolor(self._colors.get(name, (0.9, 0.85, 0.1)))
            fill = QtGui.QColor(color)
            fill.setAlpha(50)
            painter.setBrush(fill)
            pen = QtGui.QPen(color, 2)
            if name in self._custom:
                pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.drawText(rect.adjusted(4, 2, -2, -2),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, str(name))


class UVGroupPackerWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(UVGroupPackerWindow, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle("UV Group Packer %s" % __version__)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self.resize(400, 900)
        self._updating = False
        self._boxes = {}
        self._group_signature = None
        self._build()
        self._on_mode_changed()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        layout.addWidget(self._build_groups())
        layout.addWidget(self._build_packing())
        layout.addWidget(self._build_group_box())
        layout.addWidget(self._build_texel_density())
        layout.addWidget(self._build_aligning())

        # Outside the scroll area so the layout stays visible while packing.
        self.preview = BoxPreview()
        outer.addWidget(self.preview)
        view_row = QtWidgets.QHBoxLayout()
        self.show_islands = QtWidgets.QCheckBox("Show islands in group colours")
        self.show_islands.setChecked(True)
        self.show_islands.setToolTip("Draw every group's UV islands in its colour, with its name")
        self.show_islands.toggled.connect(self._update_islands)
        self.show_colors = QtWidgets.QCheckBox("Group colours on the model")
        self.show_colors.setChecked(display.is_shown())
        self.show_colors.setToolTip("Paint each group's faces in its colour in the 3D viewport\n"
                                    "(a temporary colour set; switching off removes it)")
        self.show_colors.toggled.connect(self._toggle_colors)
        view_row.addWidget(self.show_islands)
        view_row.addWidget(self.show_colors)
        view_row.addStretch(1)
        outer.addLayout(view_row)

        buttons = QtWidgets.QHBoxLayout()
        preview_btn = QtWidgets.QPushButton("Preview Boxes")
        preview_btn.clicked.connect(self._preview)
        pack_btn = QtWidgets.QPushButton("Pack")
        pack_btn.setMinimumHeight(32)
        pack_btn.setStyleSheet("font-weight: bold;")
        pack_btn.clicked.connect(self._pack)
        buttons.addWidget(preview_btn)
        buttons.addWidget(pack_btn, 1)
        outer.addLayout(buttons)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def _build_groups(self):
        group_box = QtWidgets.QGroupBox("Groups")
        gl = QtWidgets.QVBoxLayout(group_box)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["", "Group", "Faces", "Weight", "Lock", "TD"])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(150)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QtWidgets.QHeaderView.Stretch)
        for col, width in ((COL_COLOR, 26), (COL_FACES, 52), (COL_WEIGHT, 56), (COL_LOCK, 36),
                           (COL_TD, 52)):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.Fixed)
            self.tree.setColumnWidth(col, width)
        self.tree.setToolTip("Double-click the colour to change it, the name to rename,\n"
                             "the weight to make a group's box bigger or smaller.\n"
                             "In the island modes the weight is a texel density multiplier "
                             "(texel density tiers).")
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
                (row1, "From Objects", self._from_objects,
                 "One group per mesh object, for the selection or the whole scene"),
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
        return group_box

    def _build_packing(self):
        pack_box = QtWidgets.QGroupBox("Packing")
        form = QtWidgets.QFormLayout(pack_box)
        self._form = form
        self._rows = {}

        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Groups To Single Tile", packer.MODE_SINGLE_TILE)
        self.mode.addItem("Groups To Tiles (UDIM)", packer.MODE_TILES)
        self.mode.addItem("Groups To Texture Atlas", packer.MODE_ATLAS)
        self.mode.addItem("Dynamic Tiles (islands)", packer.MODE_DYNAMIC)
        self.mode.addItem("Tile Range (islands)", packer.MODE_TILE_RANGE)
        self.mode.setToolTip(
            "Group modes pack each group into its own box.\n"
            "Island modes pack the selection (or every group, or the whole scene)\n"
            "over several UDIM tiles at one texel density.")
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Mode", self.mode)

        self.resolution = QtWidgets.QComboBox()
        for res in (512, 1024, 2048, 4096, 8192):
            self.resolution.addItem(str(res), res)
        self.resolution.setCurrentIndex(2)
        self.resolution.currentIndexChanged.connect(self._refresh_density)
        form.addRow("Texture size", self.resolution)

        self.padding = QtWidgets.QDoubleSpinBox()
        self.padding.setRange(0, 128)
        self.padding.setValue(4)
        self.padding.setSuffix(" px")
        self.padding.setToolTip("Space between islands, in texture pixels")
        form.addRow("Shell padding", self.padding)

        self.margin = QtWidgets.QDoubleSpinBox()
        self.margin.setRange(0, 256)
        self.margin.setValue(4)
        self.margin.setSuffix(" px")
        self.margin.setToolTip("Space between islands and the box/tile border, in texture pixels")
        form.addRow("Tile margin", self.margin)

        self.gap = QtWidgets.QDoubleSpinBox()
        self.gap.setRange(0, 256)
        self.gap.setValue(8)
        self.gap.setSuffix(" px")
        self.gap.valueChanged.connect(self._preview)
        self._add_row("gap", "Box gap", self.gap)

        self.rotation = QtWidgets.QComboBox()
        self.rotation.addItem("90°", 90)
        self.rotation.addItem("45°", 45)
        self.rotation.addItem("None", 0)
        form.addRow("Rotation step", self.rotation)

        self.precision = QtWidgets.QComboBox()
        for index, label in enumerate(("Low (fast)", "Normal", "High", "Max (slow)")):
            self.precision.addItem(label, index)
        self.precision.setCurrentIndex(1)
        self.precision.setToolTip("Higher precision places islands more tightly but takes longer")
        form.addRow("Precision", self.precision)

        self.search_time = QtWidgets.QDoubleSpinBox()
        self.search_time.setRange(0, 600)
        self.search_time.setDecimals(0)
        self.search_time.setSuffix(" s")
        self.search_time.setSpecialValueText("off")
        self.search_time.setToolTip(
            "Heuristic search: keep trying higher precision settings for up to this many\n"
            "seconds and keep the tightest pack. Each try is undone, so undo must be on.")
        form.addRow("Heuristic search", self.search_time)

        self.area = QtWidgets.QComboBox()
        self.area.addItem("3D surface area", packer.AREA_3D)
        self.area.addItem("Current UV area", packer.AREA_UV)
        self.area.setToolTip("What decides each group's box size in single tile mode")
        self.area.currentIndexChanged.connect(self._preview)
        self._add_row("area", "Box size from", self.area)

        tile_row = QtWidgets.QHBoxLayout()
        self.tile_u = QtWidgets.QSpinBox()
        self.tile_v = QtWidgets.QSpinBox()
        for spin, prefix in ((self.tile_u, "U "), (self.tile_v, "V ")):
            spin.setRange(0, 99)
            spin.setPrefix(prefix)
            spin.valueChanged.connect(self._preview)
            tile_row.addWidget(spin)
        tile_widget = QtWidgets.QWidget()
        tile_row.setContentsMargins(0, 0, 0, 0)
        tile_widget.setLayout(tile_row)
        self._add_row("tile", "Start tile", tile_widget)

        self.per_row = QtWidgets.QSpinBox()
        self.per_row.setRange(1, 10)
        self.per_row.setValue(4)
        self.per_row.valueChanged.connect(self._preview)
        self._add_row("per_row", "Tiles in row", self.per_row)

        self.count = QtWidgets.QSpinBox()
        self.count.setRange(1, 100)
        self.count.setValue(4)
        self.count.valueChanged.connect(self._preview)
        self._add_row("count", "Tile count", self.count)

        self.fill = QtWidgets.QComboBox()
        self.fill.addItem("Simultaneously", uvlayout.FILL_SIMULTANEOUS)
        self.fill.addItem("Sequentially", uvlayout.FILL_SEQUENTIAL)
        self.fill.setToolTip("Simultaneously: every tile about equally full.\n"
                             "Sequentially: fill the first tile, then the next.")
        self._add_row("fill", "Tile filling", self.fill)

        atlas_row = QtWidgets.QHBoxLayout()
        atlas_row.setContentsMargins(0, 0, 0, 0)
        self.atlas_x = QtWidgets.QSpinBox()
        self.atlas_y = QtWidgets.QSpinBox()
        for spin, prefix in ((self.atlas_x, "X "), (self.atlas_y, "Y ")):
            spin.setRange(1, 16)
            spin.setValue(3)
            spin.setPrefix(prefix)
            spin.valueChanged.connect(self._preview)
            atlas_row.addWidget(spin)
        atlas_widget = QtWidgets.QWidget()
        atlas_widget.setLayout(atlas_row)
        self._add_row("atlas", "Atlas size", atlas_widget)

        self.cells = QtWidgets.QSpinBox()
        self.cells.setRange(1, 64)
        self.cells.setValue(1)
        self.cells.setToolTip("Atlas cells each group gets")
        self.cells.valueChanged.connect(self._preview)
        self._add_row("cells", "Cells per group", self.cells)

        self.split = QtWidgets.QCheckBox("Split UV shells shared by two groups")
        self.split.setChecked(True)
        self.split.setToolTip("Cuts UV seams along group borders before packing")
        form.addRow(self.split)

        self.td_policy = QtWidgets.QComboBox()
        self.td_policy.addItem("Independent", packer.TD_INDEPENDENT)
        self.td_policy.addItem("Uniform", packer.TD_UNIFORM)
        self.td_policy.setToolTip("Independent: every group fills its box.\n"
                                  "Uniform: all groups share one texel density (times their weight).")
        self._add_row("td", "Texel density", self.td_policy)

        self.pixel_perfect = QtWidgets.QCheckBox("Pixel perfect: snap islands to the pixel grid")
        form.addRow(self.pixel_perfect)

        self.lock_overlap = QtWidgets.QComboBox()
        self.lock_overlap.addItem("Off", packer.LOCK_OFF)
        self.lock_overlap.addItem("Any part", packer.LOCK_ANY)
        self.lock_overlap.addItem("Exact (stacked)", packer.LOCK_EXACT)
        self.lock_overlap.setToolTip("Overlapping islands are packed as one piece.\n"
                                     "Any part: when they overlap at all.\n"
                                     "Exact: only islands stacked on the same spot.")
        form.addRow("Lock overlapping", self.lock_overlap)

        lock_row = QtWidgets.QHBoxLayout()
        for label, slot, tip in (
                ("New Lock Group", self._new_lock_group,
                 "The selected islands keep their relative positions while packing"),
                ("Clear Lock Group", self._clear_lock_group,
                 "Take the selected islands out of their lock group")):
            btn = QtWidgets.QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            lock_row.addWidget(btn)
        form.addRow(lock_row)
        return pack_box

    def _build_group_box(self):
        box = QtWidgets.QGroupBox("Group Box (highlighted group)")
        vl = QtWidgets.QVBoxLayout(box)
        self.use_box = QtWidgets.QCheckBox("Custom box: always pack this group here")
        self.use_box.toggled.connect(self._on_box_edited)
        vl.addWidget(self.use_box)

        coords = QtWidgets.QGridLayout()
        self.box_spins = []
        for index, label in enumerate(("U min", "V min", "U max", "V max")):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(-10, 100)
            spin.setDecimals(3)
            spin.setSingleStep(0.05)
            spin.editingFinished.connect(self._on_box_edited)
            coords.addWidget(QtWidgets.QLabel(label), index // 2, (index % 2) * 2)
            coords.addWidget(spin, index // 2, (index % 2) * 2 + 1)
            self.box_spins.append(spin)
        vl.addLayout(coords)

        row = QtWidgets.QHBoxLayout()
        from_layout = QtWidgets.QPushButton("From Layout")
        from_layout.setToolTip("Start from the box the automatic layout gives this group")
        from_layout.clicked.connect(self._box_from_layout)
        to_tile = QtWidgets.QPushButton("Set To Tile")
        to_tile.setToolTip("Make the box fill the whole tile it is in")
        to_tile.clicked.connect(self._box_to_tile)
        row.addWidget(from_layout)
        row.addWidget(to_tile)
        vl.addLayout(row)

        move_row = QtWidgets.QHBoxLayout()
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(2)
        for r, names in enumerate(COMPASS):
            for c, direction in enumerate(names):
                if not direction:
                    continue
                btn = QtWidgets.QPushButton(direction)
                btn.setFixedWidth(40)
                btn.setToolTip("Move the box to the adjacent tile")
                btn.clicked.connect(lambda checked=False, d=direction: self._move_box(d))
                grid.addWidget(btn, r, c)
        move_row.addLayout(grid)
        self.move_islands = QtWidgets.QCheckBox("Move islands\nwith the box")
        self.move_islands.setChecked(True)
        move_row.addWidget(self.move_islands)
        move_row.addStretch(1)
        vl.addLayout(move_row)

        overrides = QtWidgets.QHBoxLayout()
        self.group_padding = QtWidgets.QDoubleSpinBox()
        self.group_padding.setRange(-1, 128)
        self.group_padding.setSuffix(" px")
        self.group_padding.setSpecialValueText("global")
        self.group_padding.setToolTip("Shell padding for this group only (-1 = global option)")
        self.group_padding.editingFinished.connect(self._on_overrides_edited)
        self.group_rotation = QtWidgets.QComboBox()
        for label, value in (("global", None), ("90°", 90), ("45°", 45), ("None", 0)):
            self.group_rotation.addItem(label, value)
        self.group_rotation.setToolTip("Rotation step for this group only")
        self.group_rotation.currentIndexChanged.connect(self._on_overrides_edited)
        overrides.addWidget(QtWidgets.QLabel("Padding"))
        overrides.addWidget(self.group_padding, 1)
        overrides.addWidget(QtWidgets.QLabel("Rotation"))
        overrides.addWidget(self.group_rotation, 1)
        vl.addLayout(overrides)
        return box

    def _build_texel_density(self):
        box = QtWidgets.QGroupBox("Texel Density")
        form = QtWidgets.QFormLayout(box)
        self.density = QtWidgets.QDoubleSpinBox()
        self.density.setRange(0, 100000)
        self.density.setDecimals(2)
        self.density.setSuffix(" px/unit")
        self.density.setSpecialValueText("current (keep)")
        self.density.setToolTip("Pixels per scene unit at the texture size.\n"
                                "Dynamic Tiles packs at this density; 0 keeps the current one.")
        form.addRow("Density", self.density)
        row = QtWidgets.QHBoxLayout()
        for label, slot, tip in (
                ("Get", self._get_density, "Read the average density of the selection"),
                ("Set Per-Island", self._set_density,
                 "Scale every selected island to this density (times its group weight)")):
            btn = QtWidgets.QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        form.addRow(row)
        self.show_td = QtWidgets.QCheckBox("Show each group's density in the list")
        self.show_td.toggled.connect(self.refresh)
        form.addRow(self.show_td)
        return box

    def _build_aligning(self):
        box = QtWidgets.QGroupBox("Stacking / Aligning")
        form = QtWidgets.QFormLayout(box)
        self.align_mode = QtWidgets.QComboBox()
        self.align_mode.addItem("Border Shape", align.MODE_BORDER)
        self.align_mode.addItem("Vertex Position", align.MODE_VERTEX)
        self.align_mode.addItem("Topology", align.MODE_TOPOLOGY)
        self.align_mode.setToolTip("Border Shape: compare outlines (any topology).\n"
                                   "Vertex Position: same UV count, matched vertex by vertex.\n"
                                   "Topology: like Vertex Position, and the faces must match too.")
        self.align_mode.currentIndexChanged.connect(self._on_align_mode_changed)
        form.addRow("Similarity mode", self.align_mode)
        self.align_threshold = QtWidgets.QDoubleSpinBox()
        self.align_threshold.setRange(0.001, 1.0)
        self.align_threshold.setDecimals(3)
        self.align_threshold.setSingleStep(0.01)
        self.align_threshold.setValue(0.1)
        self.align_threshold.setToolTip("Largest accepted shape difference, relative to the "
                                        "template's size")
        form.addRow("Threshold", self.align_threshold)
        self.align_flip = QtWidgets.QCheckBox("Flipping enable")
        self.align_scale = QtWidgets.QCheckBox("Adjust scale")
        self.align_correct = QtWidgets.QCheckBox("Correct vertices")
        self.align_correct.setChecked(True)
        self.align_correct.setToolTip("Vertex Position mode: snap every UV exactly onto the template")
        checks = QtWidgets.QHBoxLayout()
        for check in (self.align_flip, self.align_scale, self.align_correct):
            checks.addWidget(check)
        form.addRow(checks)

        self.align_axis = QtWidgets.QComboBox()
        self.align_axis.addItem("None", None)
        for axis in ("x", "y", "z"):
            self.align_axis.addItem(axis.upper(), axis)
        self.align_axis.setToolTip("Match 3D Axis: symmetric islands that fit in several\n"
                                   "orientations keep this world axis pointing the same way")
        form.addRow("Match 3D axis", self.align_axis)

        prio_row = QtWidgets.QHBoxLayout()
        self.priority = QtWidgets.QSpinBox()
        self.priority.setRange(0, 999)
        self.priority.setValue(1)
        self.priority.setToolTip("Similar islands are stacked onto the one with the highest "
                                 "priority (0 clears it)")
        set_prio = QtWidgets.QPushButton("Set Priority")
        set_prio.setToolTip("Give the selected islands this align priority")
        set_prio.clicked.connect(self._set_priority)
        prio_row.addWidget(self.priority)
        prio_row.addWidget(set_prio, 1)
        form.addRow("Align priority", prio_row)

        row = QtWidgets.QHBoxLayout()
        for label, slot, tip in (
                ("Stack", self._stack,
                 "Put similar selected islands on top of each other"),
                ("Align To Unselected", self._align_to_unselected,
                 "Move every selected island onto the most similar unselected island"),
                ("Select Similar", self._select_similar,
                 "Add unselected islands similar to the selection to the selection")):
            btn = QtWidgets.QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        form.addRow(row)
        self._on_align_mode_changed()
        return box

    def _add_row(self, key, label, widget):
        self._form.addRow(label, widget)
        self._rows[key] = (self._form.labelForField(widget), widget)

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
            tiles_per_row=self.per_row.value(),
            tile_count=self.count.value(),
            fill_method=self.fill.currentData(),
            texel_density=self.density.value(),
            atlas_cols=self.atlas_x.value(),
            atlas_rows=self.atlas_y.value(),
            cells_per_group=self.cells.value(),
            split_shared_shells=self.split.isChecked(),
            tile_margin_px=self.margin.value(),
            precision=self.precision.currentData(),
            pixel_perfect=self.pixel_perfect.isChecked(),
            lock_overlapping=self.lock_overlap.currentData(),
            search_time=self.search_time.value(),
            td_policy=self.td_policy.currentData())

    def align_options(self):
        return align.AlignOptions(mode=self.align_mode.currentData(),
                                  threshold=self.align_threshold.value(),
                                  allow_flip=self.align_flip.isChecked(),
                                  adjust_scale=self.align_scale.isChecked(),
                                  correct_vertices=self.align_correct.isChecked(),
                                  match_axis=self.align_axis.currentData())

    def _selected_names(self):
        return [item.data(COL_NAME, QtCore.Qt.UserRole) for item in self.tree.selectedItems()]

    def _current_name(self):
        names = self._selected_names()
        if not names:
            self._message("Highlight a group in the list first.")
            return None
        return names[0]

    def _group(self, name):
        return next((g for g in uvgroups.list_groups() if g["name"] == name), None)

    def _message(self, text):
        self.status.setText(text)

    def refresh(self, *args):
        selected = set(self._selected_names())
        show_td = self.show_td.isChecked()
        self.tree.setColumnHidden(COL_TD, not show_td)
        res = self.resolution.currentData()
        self._updating = True
        self.tree.clear()
        group_list = uvgroups.list_groups()
        # Repaint the model only when membership or colours changed, not after a pack.
        signature = [(g["name"], g["count"], tuple(g["color"])) for g in group_list]
        if signature != self._group_signature and display.is_shown():
            display.show()
        self._group_signature = signature
        for g in group_list:
            item = QtWidgets.QTreeWidgetItem()
            item.setIcon(COL_COLOR, _swatch(g["color"]))
            item.setText(COL_NAME, g["name"] + ("  [box]" if g["box"] else ""))
            item.setData(COL_NAME, QtCore.Qt.UserRole, g["name"])
            item.setText(COL_FACES, str(g["count"]))
            item.setText(COL_WEIGHT, "%.2f" % g["weight"])
            item.setCheckState(COL_LOCK, QtCore.Qt.Checked if g["locked"] else QtCore.Qt.Unchecked)
            if show_td and g["faces"]:
                item.setText(COL_TD, "%.1f" % uvislands.faces_density(g["faces"], res))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsUserCheckable)
            self.tree.addTopLevelItem(item)
            item.setSelected(g["name"] in selected)
        self._updating = False
        self._load_box_fields()
        self._update_islands()
        self._preview()

    def _update_islands(self, *args):
        """Re-read the islands drawn in the preview (each group's, in its colour)."""
        if not self.show_islands.isChecked():
            self.preview.set_islands([])
            return
        drawn = []
        group_list = [g for g in uvgroups.list_groups() if g["faces"]]
        sources = [(g["color"], g["name"], g["faces"]) for g in group_list]
        if not sources and self.mode.currentData() in packer.ISLAND_MODES:
            # No groups: show what the island modes would pack, in one colour.
            faces = [f for i in packer.island_targets() for f in i["faces"]]
            sources = [((1.0, 0.6, 0.2), None, faces)] if faces else []
        for rgb, name, faces in sources:
            island_list = uvislands.shells(faces)
            biggest = max(island_list, key=lambda i: i["area_uv"]) if island_list else None
            for island in island_list:
                coords = island["coords"]
                polys = [[coords[i] for i in loop] for loop in island["loops"]]
                borders = [(coords[a], coords[b])
                           for a, b in align_math.border_edges(island["loops"])]
                labelled = name if island is biggest else None
                bbox = island["bbox"]
                at = ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)
                drawn.append((rgb, polys, borders, bbox, labelled, at))
        self.preview.set_islands(drawn)

    def _toggle_colors(self, on):
        if on:
            count = display.show()
            self._message("Group colours shown on %d group(s). Untick to remove them." % count
                          if count else "Create groups first to see their colours.")
        else:
            display.hide()
            self._message("Group colours removed from the model.")

    def _on_progress(self, done, total, label):
        """Called by the packer after every group/tile: redraw so the pack is seen live."""
        self._message("Packing %s (%d/%d)..." % (label, done, total))
        self._update_islands()
        self.preview.repaint()
        QtWidgets.QApplication.processEvents()

    def _refresh_density(self, *args):
        if self.show_td.isChecked():
            self.refresh()

    # -------------------------------------------------------------- slots
    def _on_mode_changed(self, *args):
        visible = MODE_ROWS.get(self.mode.currentData(), ())
        for key, (label, widget) in self._rows.items():
            label.setVisible(key in visible)
            widget.setVisible(key in visible)
        self._update_islands()
        self._preview()

    def _on_item_double_clicked(self, item, column):
        name = item.data(COL_NAME, QtCore.Qt.UserRole)
        if column == COL_COLOR:
            g = self._group(name)
            color = QtWidgets.QColorDialog.getColor(_qcolor(g["color"]) if g else QtGui.QColor(),
                                                    self, "Group colour")
            if color.isValid():
                uvgroups.set_color(name, (color.redF(), color.greenF(), color.blueF()))
                self.refresh()
        elif column == COL_NAME:
            # Edit the plain name, not the "[box]" marker.
            self._updating = True
            item.setText(COL_NAME, name)
            self._updating = False
            self.tree.editItem(item, column)
        elif column == COL_WEIGHT:
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
        self._load_box_fields()

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

    def _from_objects(self):
        created = uvgroups.groups_from_objects()
        self._message("Groups from objects: %s" % (", ".join(created) or "no meshes found"))
        self.refresh()

    def _new_lock_group(self):
        number = uvgroups.new_lock_group(cmds.ls(selection=True))
        self._message("Lock group %d: these islands keep their relative positions." % number
                      if number else "Select the islands to lock together first.")

    def _clear_lock_group(self):
        uvgroups.clear_lock_group(cmds.ls(selection=True))
        self._message("Selected islands are no longer locked together.")

    # ------------------------------------------------------------ group box
    def _load_box_fields(self):
        names = self._selected_names()
        g = self._group(names[0]) if names else None
        box = (g["box"] if g else None) or self._boxes.get(names[0] if names else None)
        widgets = [self.use_box, self.group_padding, self.group_rotation] + self.box_spins
        for w in widgets:
            w.blockSignals(True)
        self.use_box.setChecked(bool(g and g["box"]))
        for spin, value in zip(self.box_spins, box or (0.0, 0.0, 1.0, 1.0)):
            spin.setValue(value)
        padding = g["padding"] if g else None
        self.group_padding.setValue(-1 if padding is None else padding)
        rotation = g["rotation"] if g else None
        self.group_rotation.setCurrentIndex(max(0, self.group_rotation.findData(rotation)))
        for w in widgets:
            w.blockSignals(False)
            w.setEnabled(g is not None)

    def _on_overrides_edited(self, *args):
        name = self._current_name()
        if not name:
            return
        padding = self.group_padding.value()
        uvgroups.set_overrides(name, padding=None if padding < 0 else padding,
                               rotation=self.group_rotation.currentData())
        self._message("Overrides for '%s' saved." % name)

    def _box_fields(self):
        return tuple(spin.value() for spin in self.box_spins)

    def _write_box(self, box):
        name = self._current_name()
        if not name:
            return
        for spin, value in zip(self.box_spins, box):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.use_box.blockSignals(True)
        self.use_box.setChecked(True)
        self.use_box.blockSignals(False)
        uvgroups.set_box(name, box)
        self.refresh()

    def _on_box_edited(self, *args):
        name = self._current_name()
        if not name:
            return
        box = self._box_fields()
        if self.use_box.isChecked() and (box[2] <= box[0] or box[3] <= box[1]):
            self._message("The box needs U max > U min and V max > V min.")
            return
        uvgroups.set_box(name, box if self.use_box.isChecked() else None)
        self.refresh()

    def _box_from_layout(self):
        name = self._current_name()
        if not name:
            return
        g = self._group(name)
        others = [x for x in uvgroups.list_groups()
                  if x["faces"] and not x["locked"] and x["name"] != name and not x["box"]]
        boxes = packer.compute_group_boxes(others + [dict(g, box=None)], self.options())
        if name in boxes:
            self._write_box(boxes[name])

    def _box_to_tile(self):
        u0, v0 = self._box_fields()[:2]
        u, v = int(u0 // 1), int(v0 // 1)
        self._write_box((u, v, u + 1.0, v + 1.0))

    def _move_box(self, direction):
        name = self._current_name()
        if not name:
            return
        du, dv = uvlayout.DIRECTIONS[direction]
        if self.move_islands.isChecked():
            faces = uvgroups.group_faces(name)
            uvs = cmds.polyListComponentConversion(faces, toUV=True) if faces else []
            if uvs:
                cmds.polyEditUV(uvs, uValue=du, vValue=dv, relative=True)
        self._write_box(uvlayout.move_box(self._box_fields(), direction))
        self._message("Moved '%s' box %s." % (name, direction))

    # -------------------------------------------------------- texel density
    def _get_density(self):
        faces = uvgroups.selected_faces()
        if not faces:
            self._message("Select faces or objects first.")
            return
        value = uvislands.faces_density(faces, self.resolution.currentData())
        self.density.setValue(value)
        self._message("Average texel density of the selection: %.2f px/unit." % value)

    def _set_density(self):
        faces = uvgroups.selected_faces()
        if not faces or not self.density.value():
            self._message("Select faces or objects and enter a density first.")
            return
        cmds.undoInfo(openChunk=True, chunkName="UVGroupPacker TD")
        try:
            count = uvislands.set_density(uvislands.shells(faces), self.density.value(),
                                          self.resolution.currentData(), use_weight=False)
        finally:
            cmds.undoInfo(closeChunk=True)
        self._message("Set %d island(s) to %.2f px/unit." % (count, self.density.value()))
        self.refresh()

    # ------------------------------------------------------------- aligning
    def _on_align_mode_changed(self, *args):
        vertex = self.align_mode.currentData() in (align.MODE_VERTEX, align.MODE_TOPOLOGY)
        self.align_correct.setEnabled(vertex)
        # Outlines and vertices measure different things, so start from their own default.
        self.align_threshold.setValue(0.01 if vertex else 0.1)

    def _set_priority(self):
        if not uvgroups.selected_faces():
            self._message("Select the islands to give a priority first.")
            return
        uvgroups.set_priority(cmds.ls(selection=True), self.priority.value())
        self._message("Align priority %d set." % self.priority.value() if self.priority.value()
                      else "Align priority cleared.")

    def _stack(self):
        if not uvgroups.selected_faces():
            self._message("Select the islands to stack first.")
            return
        result = align.stack(self.align_options())
        self._message("Made %d stack(s) from %d island(s); %d had nothing similar."
                      % (result["stacks"], result["moved"] + result["stacks"], result["single"]))

    def _select_similar(self):
        if not uvgroups.selected_faces():
            self._message("Select one or more islands to compare with first.")
            return
        count = align.select_similar(self.align_options())
        self._message("Selected %d similar island(s)." % count)

    def _align_to_unselected(self):
        if not uvgroups.selected_faces():
            self._message("Select the islands to align first; unselected islands are the templates.")
            return
        result = align.align_to_unselected(self.align_options())
        text = "Aligned %d island(s)." % result["aligned"]
        if result["skipped"]:
            errors = [e for e in result["errors"] if e is not None]
            text += " %d had no similar unselected island" % result["skipped"]
            text += " (closest %.3f, raise Threshold?)." % min(errors) if errors else "."
        self._message(text)

    # ---------------------------------------------------------------- pack
    def _preview(self, *args):
        options = self.options()
        try:
            if options.mode in packer.ISLAND_MODES:
                tiles = packer.estimate_tile_boxes(options)
                boxes = {1001 + int(b[0]) + 10 * int(b[1]): b for b in tiles}
                self.preview.set_boxes(boxes, {})
                return
            group_list = [g for g in uvgroups.list_groups() if g["faces"] and not g["locked"]]
            boxes = packer.compute_group_boxes(group_list, options)
        except Exception as exc:  # keep the window usable if the scene is odd
            self._message("Preview failed: %s" % exc)
            return
        self._boxes = boxes
        self.preview.set_boxes(boxes, {g["name"]: g["color"] for g in group_list},
                               custom=[g["name"] for g in group_list if g["box"]])

    def _pack(self):
        options = self.options()
        tries = []
        try:
            if options.search_time > 0:
                self._message("Heuristic search running (up to %d s)..." % options.search_time)
                QtWidgets.QApplication.processEvents()
                boxes, tries = packer.search(options, progress=self._on_progress)
            else:
                boxes = packer.pack(options, progress=self._on_progress)
        except Exception as exc:
            self._message("Pack failed: %s" % exc)
            raise
        if not boxes:
            self._message("Nothing to pack: create groups (or unlock them), or select islands.")
            return
        if options.mode in packer.ISLAND_MODES:
            text = "Packed into %d tile(s): %s." % (len(boxes), ", ".join(str(t) for t in sorted(boxes)))
        else:
            text = "Packed %d group(s)." % len(boxes)
        if tries:
            text += " Heuristic search tried %d setting(s) and kept the tightest." % len(tries)
        self._message(text + " Ctrl+Z undoes the whole pack.")
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
