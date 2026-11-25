"""'ID card from files': the same as the Scan window's ID card mode, but the pictures come from FILES (a phone photo, a
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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider, QVBoxLayout)
from PySide6.QtGui import QColor

import idpreview
import idscan
import jobs
import scanfix
import valuedit

PICS = "Pictures (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)"
AUTO_OK_SCORE = 0.6                          # same limits as the Auto crop (card.py)
AUTO_OK_EDGE = 0.6
CROP_MODES = (("Auto crop, then show me the 4 corners to check", "check"),
              ("Auto crop, ask me only when it is not sure", "sure"),
              ("Already cropped: do not crop", "none"))
SIZES = (("Smart NID / ID-1  (85.6 x 54 mm)", "id1"), ("Custom size (mm)", "custom"))


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


class IdUploadDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.st = win.settings
        self.setWindowTitle("ID card from files")
        self.setModal(False)
        self.setMinimumWidth(560)
        self.work = tempfile.mkdtemp(prefix="idup_", dir=win.tmp)
        self._bcolor = idscan.BORDER_COLOR
        self._paths = {"front": "", "back": ""}
        self._bg = None
        self._busy = False
        self._loading = True

        v = QVBoxLayout(self)
        t = QLabel("Choose the FRONT and the BACK picture (a phone photo, a saved scan ...). They are cut out, made real size and "
                   "put on one page, as two layers.")
        t.setWordWrap(True)
        v.addWidget(t)

        # ---- files
        self.lblFront = QLabel("")
        self.lblBack = QLabel("")
        self.btnFront = QPushButton("Front ...")
        self.btnBack = QPushButton("Back ...")
        self.btnFront.clicked.connect(lambda: self._choose("front"))
        self.btnBack.clicked.connect(lambda: self._choose("back"))
        for b in (self.btnFront, self.btnBack):
            b.setMinimumWidth(90)
        self.chkOne = QCheckBox("Front and Back are in ONE picture (cut both out of it)")
        self.chkOne.setToolTip("For a picture / page that has the front card above (or beside) the back card.\n"
                               "Both are found and cut out; check the corners of each.")
        r = QHBoxLayout()
        r.addWidget(self.btnFront)
        r.addWidget(self.lblFront, 1)
        v.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(self.btnBack)
        r.addWidget(self.lblBack, 1)
        v.addLayout(r)
        v.addWidget(self.chkOne)

        # ---- crop + card size
        form = QFormLayout()
        self.cmbCrop = QComboBox()
        for text, key in CROP_MODES:
            self.cmbCrop.addItem(text, key)
        self.cmbCrop.setToolTip("How the card is cut out of the picture.\n'Already cropped' = the picture is just the card.")
        form.addRow("Crop", self.cmbCrop)
        self.cmbSize = QComboBox()
        for text, key in SIZES:
            self.cmbSize.addItem(text, key)
        self.spW = QDoubleSpinBox()
        self.spH = QDoubleSpinBox()
        for sp in (self.spW, self.spH):
            sp.setRange(20.0, 400.0)
            sp.setDecimals(1)
            sp.setSuffix(" mm")
        self.spW.setValue(idscan.ID1_MM[0])
        self.spH.setValue(idscan.ID1_MM[1])
        self.spW.setToolTip("Long side of the card")
        self.spH.setToolTip("Short side of the card")
        r = QHBoxLayout()
        r.addWidget(self.cmbSize, 2)
        r.addWidget(self.spW, 1)
        r.addWidget(QLabel("x"))
        r.addWidget(self.spH, 1)
        form.addRow("Card size", r)
        self.cmbPage = QComboBox()
        for p in ("A4", "A5"):
            self.cmbPage.addItem(p, p)
        self.cmbPlace = QComboBox()
        self.cmbPlace.addItem("centre of the page", "center")
        self.cmbPlace.addItem("top of the page", "top")
        r = QHBoxLayout()
        r.addWidget(self.cmbPage, 1)
        r.addWidget(self.cmbPlace, 2)
        form.addRow("Page", r)
        v.addLayout(form)

        # ---- border + corners (the same settings as the Scan window and the Border tab)
        self.chkBorder = QCheckBox("Border")
        self.btnBorderColor = QPushButton()
        self.btnBorderColor.setFixedSize(34, 24)
        self.btnBorderColor.clicked.connect(self._pick_border_color)
        self.sldBorder = QSlider(Qt.Orientation.Horizontal)
        self.sldBorder.setRange(1, 40)
        self.lblBorderW = QLabel("")
        self.lblBorderW.setMinimumWidth(24)
        valuedit.attach(self.lblBorderW, self.sldBorder)
        r = QHBoxLayout()
        r.addWidget(self.chkBorder)
        r.addWidget(self.btnBorderColor)
        r.addWidget(self.sldBorder, 1)
        r.addWidget(self.lblBorderW)
        v.addLayout(r)
        self.chkRound = QCheckBox("Round corners")
        self.sldRound = QSlider(Qt.Orientation.Horizontal)
        self.sldRound.setRange(5, idscan.ROUND_MAX)
        self.lblRoundV = QLabel("")
        self.lblRoundV.setMinimumWidth(40)
        valuedit.attach(self.lblRoundV, self.sldRound)
        self.sampleRound = idpreview.CornerSample(w=48)
        r = QHBoxLayout()
        r.addWidget(self.chkRound)
        r.addWidget(self.sldRound, 1)
        r.addWidget(self.lblRoundV)
        r.addWidget(self.sampleRound)
        v.addLayout(r)
        self.chkPreview = QCheckBox("Show a preview before the page is made")
        v.addWidget(self.chkPreview)

        # ---- go
        self.lblMsg = QLabel("")
        self.lblMsg.setWordWrap(True)
        v.addWidget(self.lblMsg)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        v.addWidget(self.bar)
        r = QHBoxLayout()
        self.btnGo = QPushButton("Make the ID card page")
        self.btnGo.setProperty("accent", True)
        self.btnGo.setDefault(True)
        self.btnGo.clicked.connect(self._go)
        self.btnClose = QPushButton("Close")
        self.btnClose.clicked.connect(self.close)
        r.addWidget(self.btnGo, 2)
        r.addWidget(self.btnClose, 1)
        v.addLayout(r)

        self._load()
        for sig in (self.chkOne.toggled, self.chkBorder.toggled, self.chkRound.toggled, self.chkPreview.toggled,
                    self.sldBorder.valueChanged, self.sldRound.valueChanged, self.spW.valueChanged, self.spH.valueChanged,
                    self.cmbCrop.currentIndexChanged, self.cmbSize.currentIndexChanged, self.cmbPage.currentIndexChanged,
                    self.cmbPlace.currentIndexChanged):
            sig.connect(self._changed)
        self._loading = False
        self._paint()

    # ------------------------------------------------------------------ settings (saved at every change)
    def _combo_set(self, cmb, key, default):
        i = cmb.findData(self.st.value(key, default))
        cmb.setCurrentIndex(max(0, i))

    def _load(self):
        """Also called when the window is shown again: the Scan window / Border tab may have changed the shared settings."""
        was = self._loading
        self._loading = True
        try:
            self._combo_set(self.cmbCrop, "idup/crop", "check")
            self._combo_set(self.cmbSize, "idup/size", "id1")
            self.spW.setValue(float(self.st.value("idup/w", idscan.ID1_MM[0]) or idscan.ID1_MM[0]))
            self.spH.setValue(float(self.st.value("idup/h", idscan.ID1_MM[1]) or idscan.ID1_MM[1]))
            self._combo_set(self.cmbPage, "scan/page", "A4")
            self._combo_set(self.cmbPlace, "scan/place", "center")
            self.chkOne.setChecked(self.st.value("idup/one", False, type=bool))
            self.chkPreview.setChecked(self.st.value("scan/preview", True, type=bool))
            self.chkBorder.setChecked(self.st.value("scan/border_on", True, type=bool))
            self._bcolor = str(self.st.value("scan/border_color", idscan.BORDER_COLOR, type=str) or idscan.BORDER_COLOR)
            self.sldBorder.setValue(max(1, min(40, int(self.st.value("scan/border_w", idscan.BORDER_W) or idscan.BORDER_W))))
            self.chkRound.setChecked(self.st.value("scan/round_on", True, type=bool))
            self.sldRound.setValue(max(5, min(idscan.ROUND_MAX, int(self.st.value("scan/round_pct", idscan.ROUND_PCT) or idscan.ROUND_PCT))))
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
        s.setValue("idup/w", float(self.spW.value()))
        s.setValue("idup/h", float(self.spH.value()))
        s.setValue("idup/one", self.chkOne.isChecked())
        s.setValue("scan/page", self.cmbPage.currentData())
        s.setValue("scan/place", self.cmbPlace.currentData())
        s.setValue("scan/preview", self.chkPreview.isChecked())
        s.setValue("scan/border_on", self.chkBorder.isChecked())
        s.setValue("scan/border_color", self._bcolor)
        s.setValue("scan/border_w", int(self.sldBorder.value()))
        s.setValue("scan/round_on", self.chkRound.isChecked())
        s.setValue("scan/round_pct", int(self.sldRound.value()))

    def _paint(self):
        custom = self.cmbSize.currentData() == "custom"
        self.spW.setEnabled(custom)
        self.spH.setEnabled(custom)
        if not custom:
            self.spW.blockSignals(True)
            self.spH.blockSignals(True)
            self.spW.setValue(idscan.ID1_MM[0])
            self.spH.setValue(idscan.ID1_MM[1])
            self.spW.blockSignals(False)
            self.spH.blockSignals(False)
        one = self.chkOne.isChecked()
        self.btnBack.setEnabled(not one)
        self.btnFront.setText("Picture ..." if one else "Front ...")
        self.lblFront.setText(os.path.basename(self._paths["front"]) or "(not chosen)")
        self.lblBack.setText("(both sides are in the picture above)" if one else (os.path.basename(self._paths["back"]) or "(not chosen: only the front is put on the page)"))
        on = self.chkBorder.isChecked()
        self.btnBorderColor.setEnabled(on)
        self.sldBorder.setEnabled(on)
        self.btnBorderColor.setStyleSheet("background: %s; border-radius: 6px; border: 1px solid #4a5268;" % self._bcolor)
        self.lblBorderW.setText(str(self.sldBorder.value()))
        self.sldRound.setEnabled(self.chkRound.isChecked())
        self.lblRoundV.setText("%d%%" % self.sldRound.value())
        self.sampleRound.set_pct(self.sldRound.value() if self.chkRound.isChecked() else 0)

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
            return (max(self.spW.value(), self.spH.value()), min(self.spW.value(), self.spH.value()))
        return idscan.ID1_MM

    # ------------------------------------------------------------------ show / files
    def show_front(self):
        if not self.isVisible():
            self._load()
            self._paint()
        self.show()
        self.raise_()
        self.activateWindow()

    def _choose(self, side):
        start = str(self.st.value("idup/dir", "", type=str) or "")
        if not start or not os.path.isdir(start):
            start = ""
        p, _ = QFileDialog.getOpenFileName(self, "Choose the picture" if self.chkOne.isChecked() else "Choose the %s picture" % side.upper(),
                                           start, PICS)
        if not p:
            return
        self._paths[side] = p
        self.st.setValue("idup/dir", os.path.dirname(p))
        self._paint()

    def _files(self):
        """Dropped / picked files can also be given from outside (e.g. drag and drop): [front, back]."""
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
        self._bg = jobs.run_background(work, done, lambda m: self._fail(m), self._prog)
