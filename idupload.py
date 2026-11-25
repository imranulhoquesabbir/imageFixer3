"""'ID card': the same as the Scan window's ID card mode, but the pictures come from FILES (a phone photo, a
saved scan, a picture with both sides on one page) instead of a scanner.

  1. the card is looked for with the program's own Auto crop (scanner.detect_document) and, as a second opinion, the
     card-shaped rectangle finder of the scan (idscan.find_cards);
  2. the 4 corners are shown (Corner crop window) so you can check / drag them - or not, see "Crop" below;
  3. the card is cut and straightened, made REAL SIZE (a smart NID is 85.6 x 54 mm; any other size can be typed) and
     FRONT + BACK are put on an A4 / A5 page as two layers with border + round corners (idscan.compose_page and
     ui.scan_id_page, exactly as after a scan).

Every choice is saved the moment it is changed and is there again next time (page, place, border, corners, card size,
crop mode ... - the page / border / corner settings are the same ones the Scan window and the Border tab use)."""
import math
import os
import tempfile
import time

import numpy as np
from PIL import Image, ImageOps
from PySide6.QtCore import QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)
from PySide6.QtGui import QColor, QCursor, QImage, QImageReader, QKeySequence, QPainter, QPen, QPixmap, QShortcut

import idpreview
import idscan
import jobs
import scanfix
import units
import valuedit

PICS = "Pictures (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)"
AUTO_OK_SCORE = 0.6                          # same limits as the Auto crop (card.py)
AUTO_OK_EDGE = 0.6
SEG_QSS = ("QPushButton { background: rgba(128,128,160,0.14); border: 1px solid rgba(128,128,160,0.35); border-radius: 7px; padding: 6px 10px; }"
           "QPushButton:hover { border-color: #7c83ff; }"
           "QPushButton:checked { background: #5b5bd6; border-color: #7c83ff; color: white; font-weight: 600; }")


def _area_frac(q, size):
    q = np.asarray(q, np.float64)
    a = abs(sum(q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1] for i in range(4))) / 2.0
    return a / float(size[0] * size[1])


def _card_like(q, want):
    """The 4 corners make a rectangle of about the wanted card shape (either way round)."""
    q = idscan._order(q)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2.0
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2.0
    if min(w, h) < 8:
        return False
    return abs(max(w, h) / min(w, h) / want - 1.0) <= 0.18


def load_picture(path):
    im = Image.open(path)
    im.load()
    try:
        im = ImageOps.exif_transpose(im)             # a phone photo knows which way up it is
    except Exception:
        pass
    return im.convert("RGB")


def guess_quad(img, mode, want):
    """-> (4 corners TL, TR, BR, BL as float32 4x2, sure).  'sure' = no need to ask the person (in mode 'sure')."""
    if mode == "none":
        return idscan.whole_quad(img), True
    W, H = img.size
    qa, sure = None, False
    try:
        import scanner
        quad, found, info = scanner.detect_document(img, return_score=True)
        qa = idscan._order(quad)
        sure = bool(found and info["score"] >= AUTO_OK_SCORE and info["edge"] >= AUTO_OK_EDGE
                    and _area_frac(qa, img.size) >= 0.02 and _card_like(qa, want))
    except Exception:
        qa, sure = None, False
    if sure:
        return qa, True
    if _card_like(idscan.whole_quad(img), want) and (qa is None or _area_frac(qa, img.size) >= 0.85):
        return idscan.whole_quad(img), qa is not None          # the picture already is just the card
    try:
        cs = idscan.find_cards(img, 1)                         # the card-shaped rectangle finder of the scan
    except Exception:
        cs = []
    if cs:
        return cs[0], False
    return (qa if qa is not None else idscan.whole_quad(img)), False


class Seg(QWidget):
    """A row of buttons, one of them chosen (like a switch with 2-3 positions).  data() = the chosen value."""
    changed = Signal()

    def __init__(self, items, tips=None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        self._data = []
        for i, (text, data) in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(SEG_QSS)
            if tips:
                b.setToolTip(tips[i])
            self._grp.addButton(b, i)
            lay.addWidget(b, 1)
            self._data.append(data)
        self._grp.idClicked.connect(lambda _i: self.changed.emit())
        self._grp.button(0).setChecked(True)

    def data(self):
        return self._data[max(0, self._grp.checkedId())]

    currentData = data

    def set_data(self, d):
        i = self._data.index(d) if d in self._data else 0
        self._grp.button(i).setChecked(True)


class PicCard(QFrame):
    """One big box for one picture: click it or drop a file on it; shows the picture so you know it is the right one."""
    clicked = Signal()
    dropped = Signal(str)
    imageDropped = Signal(QImage)          # a picture dragged from a web page / another program (no file)
    menuRequested = Signal(QPoint)         # right click

    def __init__(self, title, hint):
        super().__init__()
        self.setObjectName("piccard")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("#piccard { border: 1.5px dashed #5b6384; border-radius: 10px; background: rgba(128,128,160,0.08); }"
                           "#piccard:hover { border-color: #7c83ff; }")
        self.setMinimumHeight(150)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        self.lblTitle = QLabel(title)
        self.lblTitle.setStyleSheet("font-weight: 700; letter-spacing: 1px; border: none; background: transparent;")
        self.lblPic = QLabel(hint)
        self.lblPic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblPic.setStyleSheet("border: none; background: transparent; color: #8a93b2;")
        self.lblPic.setMinimumHeight(96)
        self.lblName = QLabel("")
        self.lblName.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblName.setStyleSheet("border: none; background: transparent;")
        v.addWidget(self.lblTitle)
        v.addWidget(self.lblPic, 1)
        v.addWidget(self.lblName)
        self._hint = hint

    def set_title(self, t):
        self.lblTitle.setText(t)

    def set_path(self, p):
        if not p:
            self.lblPic.setPixmap(QPixmap())
            self.lblPic.setText(self._hint)
            self.lblName.setText("")
            return
        self.lblName.setText(os.path.basename(p))
        try:
            r = QImageReader(p)
            r.setAutoTransform(True)
            sz = r.size()
            if sz.isValid():
                sz.scale(260, 110, Qt.AspectRatioMode.KeepAspectRatio)
                r.setScaledSize(sz)
            img = r.read()
            if img.isNull():
                raise ValueError("unreadable")
            self.lblPic.setPixmap(QPixmap.fromImage(img))
        except Exception:
            self.lblPic.setPixmap(QPixmap())
            self.lblPic.setText("(no preview)")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def contextMenuEvent(self, e):
        self.menuRequested.emit(e.globalPos())
        e.accept()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        for u in md.urls():
            if u.isLocalFile():
                self.dropped.emit(u.toLocalFile())
                e.acceptProposedAction()
                return
        if md.hasImage():
            img = QImage(md.imageData())
            if not img.isNull():
                self.imageDropped.emit(img)
                e.acceptProposedAction()


class PagePreview(QWidget):
    """The page as it will come out: A4 / A5 shape, the cards where they will be, with the chosen border and corners."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(170, 236)
        self.cfg = dict(page="A4", place="center", size=idscan.ID1_MM, two=True, border=True, color="#878787", bw=6, rnd=100)

    def set(self, **kw):
        self.cfg.update(kw)
        self.update()

    def paintEvent(self, _e):
        c = self.cfg
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pw, ph = idscan.PAGES_MM.get(c["page"], idscan.PAGES_MM["A4"])
        W, H = self.width() - 10, self.height() - 10
        k = min(W / pw, H / ph)
        x0, y0 = (self.width() - pw * k) / 2.0, (self.height() - ph * k) / 2.0
        p.setPen(QPen(QColor("#4a5268"), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(x0, y0, pw * k, ph * k))
        cl, cs = max(c["size"]), min(c["size"])
        f = min(1.0, pw * 0.9 / cl)
        cw, ch = cl * f, cs * f
        n = 2 if c["two"] else 1
        gap = 8.0
        total = ch * n + gap * (n - 1)
        y = 15.0 if c["place"] == "top" else max(0.0, (ph - total) / 2.0)
        rad = idscan.CORNER_MM / 85.6 * cl * f * (c["rnd"] / 100.0) * k
        for i in range(n):
            r = QRectF(x0 + (pw - cw) / 2.0 * k, y0 + y * k, cw * k, ch * k)
            if c["border"]:
                p.setPen(QPen(QColor(c["color"]), max(1.0, cw * k * c["bw"] / 1000.0)))
            else:
                p.setPen(QPen(QColor("#c4cadb"), 0.5))
            p.setBrush(QColor("#e3e8f5"))
            p.drawRoundedRect(r, rad, rad) if rad > 0.5 else p.drawRect(r)
            p.setPen(QColor("#6b7390"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Front" if i == 0 else "Back")
            y += ch + gap
        p.end()


def _section(text):
    l = QLabel(text)
    l.setStyleSheet("font-weight: 700; font-size: 13px; padding-top: 4px;")
    return l


class IdUploadDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.st = win.settings
        self.setWindowTitle("ID card")
        self.setModal(False)
        self.setMinimumWidth(600)
        self.work = tempfile.mkdtemp(prefix="idup_", dir=win.tmp)
        self._bcolor = idscan.BORDER_COLOR
        self._paths = {"front": "", "back": ""}
        self._bg = None
        self._busy = False
        self._loading = True

        outer = QVBoxLayout(self)                       # the options scroll when the screen is small; the buttons stay in view
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body = QWidget()
        v = QVBoxLayout(self._body)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(8)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        foot = QVBoxLayout()
        foot.setSpacing(8)
        outer.addLayout(foot)

        # ---------------------------------------------------------------- 1. pictures
        v.addWidget(_section("1   Pictures"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.cardFront = PicCard("FRONT", "Click, drop or paste (Ctrl+V)\nRight-click for more")
        self.cardBack = PicCard("BACK", "Click, drop or paste (Ctrl+V)\nRight-click for more")
        self.cardFront.clicked.connect(lambda: self._choose("front"))
        self.cardBack.clicked.connect(lambda: self._choose("back"))
        self.cardFront.dropped.connect(lambda p: self._set_path("front", p))
        self.cardBack.dropped.connect(lambda p: self._set_path("back", p))
        self.cardFront.imageDropped.connect(lambda im: self._set_qimage("front", im))
        self.cardBack.imageDropped.connect(lambda im: self._set_qimage("back", im))
        self.cardFront.menuRequested.connect(lambda pt: self._card_menu("front", pt))
        self.cardBack.menuRequested.connect(lambda pt: self._card_menu("back", pt))
        self._pasteN = 0
        self.setAcceptDrops(True)                                   # a drop anywhere in the window fills the next empty box
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self._paste_key)
        QShortcut(QKeySequence("Shift+Insert"), self, activated=self._paste_key)
        row.addWidget(self.cardFront, 1)
        row.addWidget(self.cardBack, 1)
        v.addLayout(row)
        self.chkOne = QCheckBox("Both sides are in ONE picture (cut both cards out of it)")
        self.chkOne.setToolTip("For a picture / page that has the front card above (or beside) the back card.")
        v.addWidget(self.chkOne)

        # ---------------------------------------------------------------- 2. page + look (with the live page picture)
        v.addWidget(_section("2   Page and look"))
        mid = QHBoxLayout()
        mid.setSpacing(14)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.cmbPage = Seg((("A4", "A4"), ("A5", "A5")))
        self.cmbPlace = Seg((("Centre", "center"), ("Top", "top")), ("The cards in the middle of the page", "The cards at the top of the page"))
        form.addRow("Page", self.cmbPage)
        form.addRow("Position", self.cmbPlace)
        self.cmbSize = Seg((("NID  85.6 x 54 mm", "id1"), ("Custom size", "custom")),
                           ("Smart NID / bank card size (ID-1)", "Type the width and height of the card yourself"))
        form.addRow("Card size", self.cmbSize)
        self.spW = units.UnitSpin(idscan.ID1_MM[0], 20.0, 400.0)
        self.spH = units.UnitSpin(idscan.ID1_MM[1], 20.0, 400.0)
        self.cmbUnit = units.UnitPicker()
        self.spW.setProperty("tip", "Long side of the card")
        self.spW.setToolTip("Long side of the card")
        self.spH.setProperty("tip", "Short side of the card")
        self.spH.setToolTip("Short side of the card")
        self.rowCustom = QWidget()
        rc = QHBoxLayout(self.rowCustom)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.addWidget(self.spW, 1)
        rc.addWidget(QLabel("x"))
        rc.addWidget(self.spH, 1)
        rc.addWidget(self.cmbUnit)
        self.lblCustom = QLabel("")
        form.addRow(self.lblCustom, self.rowCustom)
        self.chkBorder = QCheckBox("Border")
        self.btnBorderColor = QPushButton()
        self.btnBorderColor.setFixedSize(34, 24)
        self.btnBorderColor.setToolTip("Border colour")
        self.btnBorderColor.clicked.connect(self._pick_border_color)
        self.sldBorder = QSlider(Qt.Orientation.Horizontal)
        self.sldBorder.setRange(1, 40)
        self.sldBorder.setToolTip("How thick the border is")
        self.lblBorderW = QLabel("")
        self.lblBorderW.setMinimumWidth(28)
        valuedit.attach(self.lblBorderW, self.sldBorder)
        rb = QHBoxLayout()
        rb.addWidget(self.btnBorderColor)
        rb.addWidget(self.sldBorder, 1)
        rb.addWidget(self.lblBorderW)
        form.addRow(self.chkBorder, rb)
        self.chkRound = QCheckBox("Round corners")
        self.sldRound = QSlider(Qt.Orientation.Horizontal)
        self.sldRound.setRange(5, idscan.ROUND_MAX)
        self.sldRound.setToolTip("100% = the corner of a real card, less = sharper, more = rounder")
        self.lblRoundV = QLabel("")
        self.lblRoundV.setMinimumWidth(40)
        valuedit.attach(self.lblRoundV, self.sldRound)
        rr = QHBoxLayout()
        rr.addWidget(self.sldRound, 1)
        rr.addWidget(self.lblRoundV)
        form.addRow(self.chkRound, rr)
        mid.addLayout(form, 1)
        self.preview = PagePreview()
        mid.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(mid)

        # ---------------------------------------------------------------- 3. cutting
        v.addWidget(_section("3   Cutting the card out"))
        self.cmbCrop = Seg((("Auto + check corners", "check"), ("Auto", "sure"), ("Already cropped", "none")),
                           ("Finds the card, then shows the 4 corners so you can fix them", "Finds the card; asks you only when it is not sure",
                            "The picture is just the card: nothing is cut"))
        v.addWidget(self.cmbCrop)
        self.lblCropHint = QLabel("")
        self.lblCropHint.setWordWrap(True)
        self.lblCropHint.setStyleSheet("color: #8a93b2;")
        v.addWidget(self.lblCropHint)

        # ---------------------------------------------------------------- go
        self.chkPreview = QCheckBox("Show a preview before the page is made")
        v.addWidget(self.chkPreview)
        self.chkClose = QCheckBox("Close this window when the page is made")
        v.addWidget(self.chkClose)
        self.lblMsg = QLabel("")
        self.lblMsg.setWordWrap(True)
        foot.addWidget(self.lblMsg)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        foot.addWidget(self.bar)
        r = QHBoxLayout()
        self.btnGo = QPushButton("Make the ID card page")
        self.btnGo.setProperty("accent", True)
        self.btnGo.setDefault(True)
        self.btnGo.setMinimumHeight(36)
        self.btnGo.clicked.connect(self._go)
        self.btnClose = QPushButton("Close")
        self.btnClose.setMinimumHeight(36)
        self.btnClose.clicked.connect(self.close)
        r.addWidget(self.btnGo, 2)
        r.addWidget(self.btnClose, 1)
        foot.addLayout(r)

        self._load()
        for sig in (self.chkOne.toggled, self.chkBorder.toggled, self.chkRound.toggled, self.chkPreview.toggled, self.chkClose.toggled,
                    self.sldBorder.valueChanged, self.sldRound.valueChanged, self.spW.valueChanged, self.spH.valueChanged,
                    self.cmbCrop.changed, self.cmbSize.changed, self.cmbPage.changed, self.cmbPlace.changed):
            sig.connect(self._changed)
        self._loading = False
        self._paint()
        for sig in (self.chkOne.toggled, self.cmbSize.changed):          # the window gets taller / shorter with its content
            sig.connect(lambda *_: QTimer.singleShot(0, self._fit_screen))

    # ------------------------------------------------------------------ settings (saved at every change)
    def _load(self):
        """Also called when the window is shown again: the Scan window / Border tab may have changed the shared settings."""
        was = self._loading
        self._loading = True
        try:
            st = self.st
            self.cmbCrop.set_data(str(st.value("idup/crop", "check")))
            self.cmbSize.set_data(str(st.value("idup/size", "id1")))
            self.spW.set_mm(float(st.value("idup/w", idscan.ID1_MM[0]) or idscan.ID1_MM[0]))
            self.spH.set_mm(float(st.value("idup/h", idscan.ID1_MM[1]) or idscan.ID1_MM[1]))
            self.cmbPage.set_data(str(st.value("scan/page", "A4")))
            self.cmbPlace.set_data(str(st.value("scan/place", "center")))
            self.chkOne.setChecked(st.value("idup/one", False, type=bool))
            self.chkPreview.setChecked(st.value("scan/preview", True, type=bool))
            self.chkClose.setChecked(st.value("idup/autoclose", True, type=bool))
            self.chkBorder.setChecked(st.value("scan/border_on", True, type=bool))
            self._bcolor = str(st.value("scan/border_color", idscan.BORDER_COLOR, type=str) or idscan.BORDER_COLOR)
            self.sldBorder.setValue(max(1, min(40, int(st.value("scan/border_w", idscan.BORDER_W) or idscan.BORDER_W))))
            self.chkRound.setChecked(st.value("scan/round_on", True, type=bool))
            self.sldRound.setValue(max(5, min(idscan.ROUND_MAX, int(st.value("scan/round_pct", idscan.ROUND_PCT) or idscan.ROUND_PCT))))
        except Exception:
            pass
        self._loading = was

    def _changed(self, *_):
        self._paint()
        if self._loading:
            return
        s = self.st
        s.setValue("idup/crop", self.cmbCrop.currentData())
        s.setValue("idup/size", self.cmbSize.currentData())
        s.setValue("idup/w", float(self.spW.mm()))
        s.setValue("idup/h", float(self.spH.mm()))
        s.setValue("idup/one", self.chkOne.isChecked())
        s.setValue("scan/page", self.cmbPage.currentData())
        s.setValue("scan/place", self.cmbPlace.currentData())
        s.setValue("scan/preview", self.chkPreview.isChecked())
        s.setValue("idup/autoclose", self.chkClose.isChecked())
        s.setValue("scan/border_on", self.chkBorder.isChecked())
        s.setValue("scan/border_color", self._bcolor)
        s.setValue("scan/border_w", int(self.sldBorder.value()))
        s.setValue("scan/round_on", self.chkRound.isChecked())
        s.setValue("scan/round_pct", int(self.sldRound.value()))

    def _paint(self):
        custom = self.cmbSize.currentData() == "custom"
        self.rowCustom.setVisible(custom)
        self.lblCustom.setText("Width x height" if custom else "")
        if not custom:
            for sp, val in ((self.spW, idscan.ID1_MM[0]), (self.spH, idscan.ID1_MM[1])):
                sp.blockSignals(True)
                sp.set_mm(val)
                sp.blockSignals(False)
        one = self.chkOne.isChecked()
        self.cardBack.setVisible(not one)
        self.cardFront.set_title("FRONT + BACK  (one picture)" if one else "FRONT")
        self.cardFront.set_path(self._paths["front"])
        self.cardBack.set_path(self._paths["back"])
        on = self.chkBorder.isChecked()
        self.btnBorderColor.setEnabled(on)
        self.sldBorder.setEnabled(on)
        self.lblBorderW.setEnabled(on)
        self.btnBorderColor.setStyleSheet("background: %s; border-radius: 6px; border: 1px solid #4a5268;" % self._bcolor)
        self.lblBorderW.setText(str(self.sldBorder.value()))
        self.sldRound.setEnabled(self.chkRound.isChecked())
        self.lblRoundV.setEnabled(self.chkRound.isChecked())
        self.lblRoundV.setText("%d%%" % self.sldRound.value())
        self.lblCropHint.setText({
            "check": "The card is found by itself, then you see its 4 corners. Drag a corner if it is not exact.",
            "sure": "The card is found by itself. The 4 corners are shown only when the program is not sure.",
            "none": "Use this when the picture is already only the card (nothing around it)."}.get(self.cmbCrop.currentData(), ""))
        self.preview.set(page=self.cmbPage.currentData(), place=self.cmbPlace.currentData(), size=self._size_mm(),
                         two=(one or bool(self._paths["back"]) or not self._paths["front"]), border=on, color=self._bcolor,
                         bw=self.sldBorder.value(), rnd=self.sldRound.value() if self.chkRound.isChecked() else 0)

    def _pick_border_color(self):
        c = QColorDialog.getColor(QColor(self._bcolor), self, "Border colour")
        if c.isValid():
            self._bcolor = c.name()
            self._changed()

    def _border(self):
        return idscan.make_border(self.chkBorder.isChecked(), self._bcolor, self.sldBorder.value())

    def _round(self):
        return int(self.sldRound.value()) if self.chkRound.isChecked() else 0

    def _size_mm(self):
        if self.cmbSize.currentData() == "custom":
            return (max(self.spW.mm(), self.spH.mm()), min(self.spW.mm(), self.spH.mm()))
        return idscan.ID1_MM

    # ------------------------------------------------------------------ show / files
    def show_front(self):
        if not self.isVisible():
            self._load()
            self._paint()
        self.show()
        self._fit_screen()
        self.raise_()
        self.activateWindow()

    def _fit_screen(self):
        """Open at the height the content needs, but never taller / wider than the screen (the taskbar is left free), and keep
        the whole window inside the screen."""
        scr = (self.screen() if self.isVisible() else None) or self.win.screen() or QApplication.primaryScreen()
        ag = scr.availableGeometry()                     # the screen without the taskbar
        maxh, maxw = int(ag.height() * 0.94), int(ag.width() * 0.94)
        self.setMinimumWidth(min(620, maxw))
        self.setMaximumSize(maxw, maxh)
        fixed = self.sizeHint().height() - self._scroll.sizeHint().height()
        want = fixed + self._body.sizeHint().height() + 6
        w = min(max(self.minimumWidth(), self.width() if self.isVisible() else 640), maxw)
        self.resize(w, min(want, maxh))
        fg = self.frameGeometry()
        x = min(max(fg.x(), ag.x()), max(ag.x(), ag.right() - fg.width()))
        y = min(max(fg.y(), ag.y()), max(ag.y(), ag.bottom() - fg.height()))
        self.move(x, y)

    def _set_path(self, side, p):
        if not p or not os.path.isfile(p):
            return
        self._paths[side] = p
        self.st.setValue("idup/dir", os.path.dirname(p))
        self.lblMsg.setText("")
        self._paint()

    # ------------------------------------------------------------------ paste / copy / drop / right click
    def _side_for(self, side):
        """Which box gets the picture: the one asked for, else the one under the mouse, else the first empty one."""
        if side:
            return side
        pos = QCursor.pos()
        for name, card in (("front", self.cardFront), ("back", self.cardBack)):
            if card.isVisible() and card.rect().contains(card.mapFromGlobal(pos)):
                return name
        if not self._paths["front"]:
            return "front"
        if not self._paths["back"] and self.cardBack.isVisible():
            return "back"
        return "front"

    def _set_qimage(self, side, img):
        if img is None or img.isNull():
            return
        self._pasteN += 1
        p = os.path.join(self.work, "pasted_%s_%d.png" % (time.strftime("%H%M%S"), self._pasteN))
        if not img.save(p, "PNG"):
            self.lblMsg.setText("Could not take that picture.")
            return
        self._set_path(self._side_for(side), p)

    def _clipboard_has_picture(self):
        md = QApplication.clipboard().mimeData()
        return bool(md is not None and (md.hasImage() or any(u.isLocalFile() for u in md.urls())))

    def _paste_key(self):
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit):                   # typing a size: Ctrl+V pastes TEXT there, as everywhere in Windows
            fw.paste()
            return
        self._paste(None)

    def _paste(self, side=None):
        """Ctrl+V, right click > Paste: a picture copied from anywhere (a web page, a chat, a screenshot) or picture FILES
        copied in Explorer.  Two files at once fill Front and Back."""
        cb = QApplication.clipboard()
        md = cb.mimeData()
        if md is None:
            return
        files = [u.toLocalFile() for u in md.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
        if files:
            if len(files) >= 2 and side is None and not self.chkOne.isChecked():
                self._set_path("front", files[0])
                self._set_path("back", files[1])
            else:
                self._set_path(self._side_for(side), files[0])
            return
        if md.hasImage():
            self._set_qimage(side, cb.image())
            return
        self.lblMsg.setText("Nothing to paste. Copy a picture (or picture files in Explorer) first.")

    def _copy_picture(self, side):
        p = self._paths[side]
        if p and os.path.isfile(p):
            img = QImage(p)
            if not img.isNull():
                QApplication.clipboard().setImage(img)
                self.lblMsg.setText("Picture copied.")

    def _clear(self, side):
        self._paths[side] = ""
        self._paint()

    def _card_menu(self, side, global_pt):
        m = QMenu(self)
        name = "picture" if self.chkOne.isChecked() else side.upper() + " picture"
        a_paste = m.addAction("Paste %s\tCtrl+V" % name)
        a_paste.setEnabled(self._clipboard_has_picture())
        a_file = m.addAction("Choose a file ...")
        m.addSeparator()
        a_copy = m.addAction("Copy this picture")
        a_clear = m.addAction("Remove this picture")
        a_copy.setEnabled(bool(self._paths[side]))
        a_clear.setEnabled(bool(self._paths[side]))
        m.addSeparator()
        a_swap = m.addAction("Swap FRONT and BACK")
        a_swap.setEnabled(not self.chkOne.isChecked() and bool(self._paths["front"] or self._paths["back"]))
        act = m.exec(global_pt)
        if act is a_paste:
            self._paste(side)
        elif act is a_file:
            self._choose(side)
        elif act is a_copy:
            self._copy_picture(side)
        elif act is a_clear:
            self._clear(side)
        elif act is a_swap:
            self._paths["front"], self._paths["back"] = self._paths["back"], self._paths["front"]
            self._paint()

    def contextMenuEvent(self, e):
        """Right click anywhere else in the window."""
        m = QMenu(self)
        a_paste = m.addAction("Paste picture\tCtrl+V")
        a_paste.setEnabled(self._clipboard_has_picture())
        a_file = m.addAction("Choose a file ...")
        act = m.exec(e.globalPos())
        if act is a_paste:
            self._paste(None)
        elif act is a_file:
            self._choose(self._side_for(None))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if files:
            if len(files) >= 2 and not self.chkOne.isChecked():
                self._set_path("front", files[0])
                self._set_path("back", files[1])
            else:
                self._set_path(self._side_for(None), files[0])
            e.acceptProposedAction()
        elif md.hasImage():
            self._set_qimage(None, QImage(md.imageData()))
            e.acceptProposedAction()

    def _choose(self, side):
        start = str(self.st.value("idup/dir", "", type=str) or "")
        if not start or not os.path.isdir(start):
            start = ""
        p, _ = QFileDialog.getOpenFileName(self, "Choose the picture" if self.chkOne.isChecked() else "Choose the %s picture" % side.upper(),
                                           start, PICS)
        if p:
            self._set_path(side, p)

    def _files(self):
        return self._paths["front"], self._paths["back"]

    # ------------------------------------------------------------------ progress
    def _prog(self, f, text=""):
        self.bar.setValue(int(max(0.0, min(1.0, f)) * 100))
        if text:
            self.lblMsg.setText(text + " ...")

    def _busy_set(self, busy):
        self._busy = busy
        self.btnGo.setEnabled(not busy)
        self.bar.setVisible(busy)
        if busy:
            self.bar.setValue(0)

    def _fail(self, msg):
        self._busy_set(False)
        self.lblMsg.setText("Could not do it: %s" % (msg.splitlines()[-1] if msg else ""))

    # ------------------------------------------------------------------ 1. read the pictures + guess the corners
    def _go(self):
        if self._busy:
            return
        front, back = self._files()
        one = self.chkOne.isChecked()
        if not front or not os.path.isfile(front):
            self.lblMsg.setText("Choose the %s picture first." % ("" if one else "FRONT"))
            return
        if not one and back and not os.path.isfile(back):
            self.lblMsg.setText("The BACK picture was not found.")
            return
        mode = self.cmbCrop.currentData()
        size = self._size_mm()
        want = max(size) / min(size)
        paths = [front] if (one or not back) else [front, back]
        self._busy_set(True)
        self.lblMsg.setText("Looking for the card ...")

        def work(progress):
            sides = []
            if one:
                progress(0.1, "reading the picture")
                im = load_picture(front)
                progress(0.3, "looking for the two cards")
                quads = idscan.find_cards(im, 2) if mode != "none" else []
                whole = idscan.whole_quad(im)
                for i in range(2):
                    sure = i < len(quads) and len(quads) == 2
                    sides.append({"path": front, "img": im, "quad": quads[i] if i < len(quads) else whole, "sure": sure})
                return sides
            for i, p in enumerate(paths):
                progress(i / float(len(paths)), "reading picture %d / %d" % (i + 1, len(paths)))
                im = load_picture(p)
                q, sure = guess_quad(im, mode, want)
                sides.append({"path": p, "img": im, "quad": q, "sure": sure})
            return sides

        def done(sides):
            self._busy_set(False)
            self._check_corners(sides, mode)
        self._bg = jobs.run_background(work, done, lambda m: self._fail(m), self._prog)

    # ------------------------------------------------------------------ 2. corners: show them when asked / not sure
    def _corner_dialog(self, s, name):
        dlg = idpreview.CornerDialog(self, s["img"], [tuple(map(float, p)) for p in s["quad"]], "Corner crop: " + name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        s["quad"] = np.asarray(dlg.quad(), np.float32)
        s["sure"] = True
        return True

    def _check_corners(self, sides, mode):
        names = ["Front", "Back"]
        for i, s in enumerate(sides):
            if mode == "none":
                continue
            if mode == "check" or not s["sure"]:
                if not self._corner_dialog(s, names[i]):
                    self.lblMsg.setText("Cancelled.")
                    return
        self._cut(sides)

    # ------------------------------------------------------------------ 3. cut, straighten, real size
    def _cut_one(self, s, mode, size, fix, progress):
        img, q = s["img"], s["quad"]
        if mode == "none":
            W, H = img.size
            k = max(1, int(round(min(W, H) * 0.003)))
            card = img.crop((k, k, W - k, H - k)) if min(W, H) > 40 else img
        else:
            card = idscan.cut_quad_refined(img, idscan.inset_quad(q), keep_orientation=True)
        est = max(card.size) / (max(size) / 25.4)                    # the dpi the card has now (its long side = the card's long side)
        card, eff = scanfix.enhance(card, max(50.0, est), fix, progress)
        card, dpi = idscan.to_real_size(card, size, enhance_dpi=eff)
        return dict(s, card=card, dpi=dpi)

    def _cut(self, sides):
        mode = self.cmbCrop.currentData()
        size = self._size_mm()
        fix = self.st.value("scan/fix", scanfix.BEST)
        self._busy_set(True)

        def work(progress):
            out = []
            for i, s in enumerate(sides):
                out.append(self._cut_one(s, mode, size, fix, lambda f, t="", i=i: progress((i + f) / len(sides), "cutting the card out")))
            return out

        def done(cut):
            self._busy_set(False)
            self.lblMsg.setText("")
            if self.chkPreview.isChecked():
                self._preview(cut, mode, size, fix)
            else:
                self._finish(cut)
        self._bg = jobs.run_background(work, done, lambda m: self._fail(m), self._prog)

    # ------------------------------------------------------------------ 4. preview (corner crop again / OK)
    def _preview(self, sides, mode, size, fix):
        names = ["Front", "Back"][:len(sides)]
        img = idscan.preview_image([(s["card"], True) for s in sides], self._border(), round_pct=self._round())
        text = "This is how the card(s) will look on the page.  OK puts them on the %s page." % self.cmbPage.currentData()
        dlg = idpreview.PreviewDialog(self, "ID card preview", text, img, names, crop=(mode != "none"),
                                      cards=[(s["card"], True) for s in sides],
                                      border={"on": self.chkBorder.isChecked(), "color": self._bcolor, "width": int(self.sldBorder.value())},
                                      rnd={"on": self.chkRound.isChecked(), "pct": int(self.sldRound.value())})
        dlg.exec()
        try:                                                         # what was changed in the preview is kept
            self.chkBorder.setChecked(bool(dlg.border["on"]))
            self._bcolor = str(dlg.border["color"])
            self.sldBorder.setValue(int(dlg.border["width"]))
            self.chkRound.setChecked(bool(dlg.rnd["on"]))
            self.sldRound.setValue(int(dlg.rnd["pct"]))
            self._changed()
        except Exception:
            pass
        act = dlg.action
        if act == "ok":
            self._finish(sides)
        elif isinstance(act, tuple):
            i = act[1]
            if self._corner_dialog(sides[i], names[i]):
                self._busy_set(True)

                def work(progress):
                    return self._cut_one(sides[i], mode, size, fix, progress)

                def done(ns):
                    sides[i] = ns
                    self._busy_set(False)
                    self._preview(sides, mode, size, fix)
                self._bg = jobs.run_background(work, done, lambda m: self._fail(m), self._prog)
            else:
                self._preview(sides, mode, size, fix)
        else:
            self.lblMsg.setText("Nothing was made. Change the corners / settings and press the button again.")

    # ------------------------------------------------------------------ 5. the page
    def _finish(self, sides):
        border = self._border()
        rpct = self._round()
        page, place = self.cmbPage.currentData(), self.cmbPlace.currentData()
        front = sides[0]["card"]
        back = sides[1]["card"] if len(sides) > 1 else None
        dpi = int(round(max(s["dpi"] for s in sides)))
        self._busy_set(True)

        def work(progress):
            progress(0.8, "putting the page together")
            pg, items = idscan.compose_page(front, back, dpi, page, place, rounded=rpct > 0, round_pct=rpct)
            path = os.path.join(self.work, "idcard_%s.png" % time.strftime("%H%M%S"))
            pg.save(path, compress_level=1)
            return path, items

        def done(r):
            path, items = r
            self._busy_set(False)
            self.win.scan_id_page(path, items, border, rpct)
            self.lblMsg.setText("ID card page is ready (%s are layers). You can make the next one." %
                                ("front and back" if back is not None else "front"))
            if self.chkClose.isChecked():
                QTimer.singleShot(700, self.close)
        self._bg = jobs.run_background(work, done, lambda m: self._fail(m), self._prog)
