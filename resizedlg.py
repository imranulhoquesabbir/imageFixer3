"""Resize the picture: type the size in px, mm, cm or inch (the unit is the program-wide one, see units.py).

  * Width and height follow each other (lock = the shape stays); untick the lock to stretch.
  * mm / cm / inch need a dpi (how many pixels make one inch).  Default = the global dpi (Settings > Units).
  * "Percent" buttons for the quick jobs (50 %, 200 % ...), a "Paper" list (A0-A8, B, Letter, Legal, photo, ID ...).
  * the estimated file size (JPEG / PNG) is shown live, measured on a sample of the real picture.
Returns (width px, height px, dpi) through run(), or None when cancelled.
"""
import io

from PIL import Image
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout)

import core
import units

PERCENTS = (25, 50, 75, 150, 200)
# (label, long mm, short mm).  Groups are separated by None.
PAPERS = (
    ("A0", 1189.0, 841.0), ("A1", 841.0, 594.0), ("A2", 594.0, 420.0), ("A3", 420.0, 297.0), ("A4", 297.0, 210.0),
    ("A5", 210.0, 148.0), ("A6", 148.0, 105.0), ("A7", 105.0, 74.0), ("A8", 74.0, 52.0),
    None,
    ("B3", 500.0, 353.0), ("B4", 353.0, 250.0), ("B5", 250.0, 176.0), ("B6", 176.0, 125.0),
    None,
    ("Letter", 279.4, 215.9), ("Legal", 355.6, 215.9), ("Tabloid / Ledger", 431.8, 279.4), ("Executive", 266.7, 184.2),
    ("Half letter", 215.9, 139.7),
    None,
    ("Photo 3R  (3.5 x 5 in)", 127.0, 89.0), ("Photo 4R  (4 x 6 in)", 152.4, 101.6), ("Photo 5R  (5 x 7 in)", 177.8, 127.0),
    ("Photo 6R  (6 x 8 in)", 203.2, 152.4), ("Photo 8R  (8 x 10 in)", 254.0, 203.2), ("Photo 10R  (10 x 12 in)", 304.8, 254.0),
    None,
    ("ID card", 85.6, 54.0), ("Passport photo  (45 x 35 mm)", 45.0, 35.0), ("US passport  (2 x 2 in)", 50.8, 50.8),
    ("Stamp photo  (40 x 30 mm)", 40.0, 30.0), ("Visa photo  (50 x 40 mm)", 50.0, 40.0), ("Business card", 90.0, 50.0),
)


def _sample_bytes_per_px(img):
    """How many bytes one pixel costs when the picture is saved (JPEG q98 4:4:4, PNG), measured on a small sample of the
    REAL picture, so a photo and a flat scan give very different answers.  Returns (jpeg, png) or None."""
    try:
        im = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        side = 384
        if im.width > side or im.height > side:           # the middle of the picture, 1:1 (resizing would change the detail)
            l, t = (im.width - side) // 2, (im.height - side) // 2
            im = im.crop((max(0, l), max(0, t), min(im.width, l + side), min(im.height, t + side)))
        n = float(im.width * im.height)
        out = []
        for fmt, kw in (("JPEG", dict(quality=98, subsampling=0, optimize=True)), ("PNG", dict(compress_level=6))):
            b = io.BytesIO()
            im.save(b, fmt, **kw)
            out.append(b.tell() / n)
        return out[0], out[1]
    except Exception:
        return None


def fmt_bytes(n):
    n = float(n)
    if n < 1024 * 1024:
        return "%.0f KB" % max(1.0, n / 1024.0)
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / 1024.0 / 1024.0)
    return "%.2f GB" % (n / 1024.0 ** 3)


class ResizeDialog(QDialog):
    def __init__(self, size, parent=None, dpi=None, image=None):
        super().__init__(parent)
        self.setWindowTitle("Resize picture")
        self._w0, self._h0 = int(size[0]), int(size[1])
        self._ratio = self._w0 / float(self._h0)
        self._dpi = int(dpi or units.get_dpi())
        self._busy = False
        self.result_value = None
        self._bpp = _sample_bytes_per_px(image) if image is not None else None   # (jpeg, png) bytes per pixel
        root = QVBoxLayout(self)
        root.setSpacing(10)
        self.lblNow = QLabel("")
        self.lblNow.setWordWrap(True)
        root.addWidget(self.lblNow)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        mx = units.px_to_mm(core.MAX_UPSCALE_SIDE, self._dpi)
        mn = units.px_to_mm(1, self._dpi)
        self.spW = units.UnitSpin(0, mn, mx, own_dpi=self._dpi)
        self.spH = units.UnitSpin(0, mn, mx, own_dpi=self._dpi)
        self.spW.set_px(self._w0)
        self.spH.set_px(self._h0)
        self.cmbUnit = units.UnitPicker()
        self.chkLock = QCheckBox("Keep the shape (no stretching)")
        self.chkLock.setChecked(True)
        grid.addWidget(QLabel("Width"), 0, 0)
        grid.addWidget(self.spW, 0, 1)
        grid.addWidget(self.cmbUnit, 0, 2)
        grid.addWidget(QLabel("Height"), 1, 0)
        grid.addWidget(self.spH, 1, 1)
        grid.addWidget(self.chkLock, 2, 1, 1, 2)
        self.spDpi = QSpinBox()
        self.spDpi.setRange(units.MIN_DPI, units.MAX_DPI)
        self.spDpi.setValue(self._dpi)
        self.spDpi.setSuffix(" dpi")
        self.spDpi.setKeyboardTracking(False)
        self.spDpi.setToolTip("Pixels per inch. It decides how many pixels a size in mm / cm / inch makes, and it is written into the saved file.")
        grid.addWidget(QLabel("Resolution"), 3, 0)
        grid.addWidget(self.spDpi, 3, 1)
        root.addLayout(grid)

        row = QHBoxLayout()
        row.addWidget(QLabel("Quick"))
        for p in PERCENTS:
            b = QPushButton("%d%%" % p)
            b.clicked.connect(lambda _=False, p=p: self._percent(p))
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Paper"))
        self.cmbPaper = QComboBox()
        self.cmbPaper.addItem("Choose a paper size...", None)
        for p in PAPERS:
            if p is None:
                self.cmbPaper.insertSeparator(self.cmbPaper.count())
                continue
            label, lo, sh = p
            self.cmbPaper.addItem("%s   %g x %g mm" % (label, lo, sh), (lo, sh))
        self.cmbPaper.setToolTip("The long side matches the picture's long side. With the lock on, the picture fits INSIDE the paper.")
        self.cmbPaper.setMaxVisibleItems(20)
        self.cmbPaper.activated.connect(self._paper_picked)
        row2.addWidget(self.cmbPaper, 1)
        root.addLayout(row2)

        self.lblNew = QLabel("")
        self.lblNew.setWordWrap(True)
        self.lblNew.setStyleSheet("font-weight: 600;")
        root.addWidget(self.lblNew)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btnCancel = QPushButton("Cancel")
        self.btnOk = QPushButton("Resize")
        self.btnOk.setProperty("accent", True)
        self.btnOk.setDefault(True)
        btns.addWidget(self.btnCancel)
        btns.addWidget(self.btnOk)
        root.addLayout(btns)
        self.setMinimumWidth(420)

        self.spW.valueChanged.connect(lambda _v: self._edited("w"))
        self.spH.valueChanged.connect(lambda _v: self._edited("h"))
        self.spDpi.valueChanged.connect(self._dpi_changed)
        self.chkLock.toggled.connect(lambda _c: self._edited("w"))
        units.UnitStore.get().changed.connect(self._summary)
        self.btnCancel.clicked.connect(self.reject)
        self.btnOk.clicked.connect(self._ok)
        self._summary()

    # ------------------------------------------------------------------ helpers
    def _px(self):
        return max(1, self.spW.px()), max(1, self.spH.px())

    def _set_px(self, w, h):
        self._busy = True
        try:
            self.spW.set_px(w)
            self.spH.set_px(h)
        finally:
            self._busy = False
        self._summary()

    def _edited(self, which):
        if self._busy:
            return
        if self.chkLock.isChecked():
            self._busy = True
            try:
                if which == "w":
                    self.spH.set_px(max(1, round(self.spW.px() / self._ratio)))
                else:
                    self.spW.set_px(max(1, round(self.spH.px() * self._ratio)))
            finally:
                self._busy = False
        self._summary()

    def _dpi_changed(self, d):
        """A new dpi keeps the PIXELS the same and changes the real size (mm / inch) - like the Image Size box of an editor."""
        w, h = self._px()
        self._dpi = int(d)
        self.spW.set_own_dpi(self._dpi)
        self.spH.set_own_dpi(self._dpi)
        self.spW.set_limits_mm(units.px_to_mm(1, self._dpi), units.px_to_mm(core.MAX_UPSCALE_SIDE, self._dpi))
        self.spH.set_limits_mm(units.px_to_mm(1, self._dpi), units.px_to_mm(core.MAX_UPSCALE_SIDE, self._dpi))
        self._set_px(w, h)

    def _percent(self, p):
        self._set_px(max(1, round(self._w0 * p / 100.0)), max(1, round(self._h0 * p / 100.0)))

    def _paper_picked(self, i):
        v = self.cmbPaper.itemData(i)
        if v:
            self._paper(v[0], v[1])

    def _paper(self, lo, sh):
        long_side_is_w = self._w0 >= self._h0
        w_mm, h_mm = (lo, sh) if long_side_is_w else (sh, lo)
        if self.chkLock.isChecked():           # fit INSIDE the paper size, the shape of the picture stays
            k = min(units.mm_to_px(w_mm, self._dpi) / float(self._w0), units.mm_to_px(h_mm, self._dpi) / float(self._h0))
            self._set_px(max(1, round(self._w0 * k)), max(1, round(self._h0 * k)))
        else:
            self._set_px(units.mm_to_px(w_mm, self._dpi), units.mm_to_px(h_mm, self._dpi))

    def _summary(self, *_):
        w, h = self._px()
        wm, hm = units.px_to_mm(w, self._dpi), units.px_to_mm(h, self._dpi)
        self.lblNow.setText("Now: %d x %d px   (%s x %s at %d dpi)" % (
            self._w0, self._h0, units.fmt(units.px_to_mm(self._w0, self._dpi), "mm", self._dpi),
            units.fmt(units.px_to_mm(self._h0, self._dpi), "mm", self._dpi), self._dpi))
        pct = 100.0 * w / self._w0
        self.lblNew.setText("New: %d x %d px   =   %s x %s   (%s x %s)   %.0f%%" % (
            w, h, units.fmt(wm, "mm", self._dpi), units.fmt(hm, "mm", self._dpi),
            units.fmt(wm, "in", self._dpi), units.fmt(hm, "in", self._dpi), pct))
        if self._bpp:
            self.lblNew.setText(self.lblNew.text() + "\nFile size (estimate):  JPEG ~%s    PNG ~%s    (now: JPEG ~%s, PNG ~%s)" % (
                fmt_bytes(w * h * self._bpp[0]), fmt_bytes(w * h * self._bpp[1]),
                fmt_bytes(self._w0 * self._h0 * self._bpp[0]), fmt_bytes(self._w0 * self._h0 * self._bpp[1])))
        too_big = max(w, h) > core.MAX_UPSCALE_SIDE
        self.btnOk.setEnabled(not too_big)
        if too_big:
            self.lblNew.setText(self.lblNew.text() + "\nToo big. The limit is %d px on the long side." % core.MAX_UPSCALE_SIDE)

    def _ok(self):
        w, h = self._px()
        self.result_value = (w, h, self._dpi)
        self.accept()

    def run(self):
        if self.exec():
            return self.result_value
        return None
