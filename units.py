"""Units: ONE choice (px / mm / cm / inch) for every size box in the program.

  * the choice lives in QSettings ("units/unit"), the dpi used to turn pixels into real size in "units/dpi"
  * UnitSpin is a number box that shows its value in the chosen unit.  Inside it always keeps millimetres, so changing the
    unit never changes the size, only how it is written.
  * every UnitSpin (and every UnitPicker) follows the choice live: change it in one place and all open boxes change.
"""
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox

MM_PER_INCH = 25.4
UNITS = (("px", "px"), ("mm", "mm"), ("cm", "cm"), ("in", "inch"))      # (key, label)
DECIMALS = {"px": 0, "mm": 1, "cm": 2, "in": 3}
STEP = {"px": 1.0, "mm": 0.5, "cm": 0.1, "in": 0.01}
DEFAULT_UNIT = "mm"
DEFAULT_DPI = 300
MIN_DPI, MAX_DPI = 30, 1200


def mm_to_unit(mm, unit, dpi):
    if unit == "px":
        return mm / MM_PER_INCH * dpi
    if unit == "cm":
        return mm / 10.0
    if unit == "in":
        return mm / MM_PER_INCH
    return float(mm)


def unit_to_mm(v, unit, dpi):
    if unit == "px":
        return v / float(dpi) * MM_PER_INCH
    if unit == "cm":
        return v * 10.0
    if unit == "in":
        return v * MM_PER_INCH
    return float(v)


def mm_to_px(mm, dpi):
    return int(round(mm_to_unit(mm, "px", dpi)))


def px_to_mm(px, dpi):
    return unit_to_mm(px, "px", dpi)


def fmt(mm, unit=None, dpi=None):
    """'85.6 mm', '3.37 inch', '1011 px' ... for messages."""
    u = unit or get_unit()
    d = dpi or get_dpi()
    v = mm_to_unit(mm, u, d)
    return "%.*f %s" % (DECIMALS[u], v, dict(UNITS)[u])


class UnitStore(QObject):
    """Tells every open box when the unit or the dpi changes."""
    changed = Signal()
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = UnitStore()
        return cls._inst


def _settings():
    return QSettings("ImageFixer", "ImageFixer")          # the same file as every other setting of the program


def get_unit():
    u = _settings().value("units/unit", DEFAULT_UNIT, type=str)
    return u if u in DECIMALS else DEFAULT_UNIT


def get_dpi():
    try:
        d = int(float(_settings().value("units/dpi", DEFAULT_DPI)))
    except (TypeError, ValueError):
        d = DEFAULT_DPI
    return max(MIN_DPI, min(MAX_DPI, d))


def set_unit(u):
    if u in DECIMALS:
        _settings().setValue("units/unit", u)           # always tell the boxes: a menu may have saved the value already
        UnitStore.get().changed.emit()


def set_dpi(d):
    d = max(MIN_DPI, min(MAX_DPI, int(d)))
    _settings().setValue("units/dpi", d)
    UnitStore.get().changed.emit()


class UnitSpin(QDoubleSpinBox):
    """A size box that shows the chosen unit.  mm() / set_mm() are the real size.
    own_dpi: use this dpi for px instead of the global one (e.g. a page that is drawn at 200 dpi).
    min_mm / max_mm: the limits, whatever the unit is."""

    def __init__(self, mm=85.6, min_mm=1.0, max_mm=5000.0, own_dpi=None, parent=None):
        super().__init__(parent)
        self._mm = float(mm)
        self._min_mm, self._max_mm = float(min_mm), float(max_mm)
        self._own_dpi = own_dpi
        self._busy = False
        self.setKeyboardTracking(False)
        self.setAccelerated(True)
        self._refresh()
        self.valueChanged.connect(self._typed)
        UnitStore.get().changed.connect(self._refresh)

    def dpi(self):
        return self._own_dpi or get_dpi()

    def set_own_dpi(self, dpi):
        self._own_dpi = dpi
        self._refresh()

    def mm(self):
        return max(self._min_mm, min(self._max_mm, self._mm))

    def px(self):
        return mm_to_px(self.mm(), self.dpi())

    def set_mm(self, mm):
        self._mm = max(self._min_mm, min(self._max_mm, float(mm)))
        self._refresh()

    def set_px(self, px):
        self.set_mm(px_to_mm(px, self.dpi()))

    def set_limits_mm(self, lo, hi):
        self._min_mm, self._max_mm = float(lo), float(hi)
        self._refresh()

    def _typed(self, v):
        if self._busy:
            return
        # What the person typed is the truth; mm is kept as exact as the unit allows (px stays whole).
        self._mm = max(self._min_mm, min(self._max_mm, unit_to_mm(v, get_unit(), self.dpi())))

    def _refresh(self, *_):
        u = get_unit()
        d = self.dpi()
        self._busy = True
        try:
            # the box itself has no tight limit: the person may type any number, it is clamped when read (see mm()).
            lo, hi = mm_to_unit(self._min_mm, u, d), mm_to_unit(self._max_mm, u, d)
            self.setRange(0.0, max(hi, 1.0) * 1.0)
            self.setDecimals(DECIMALS[u])
            self.setSingleStep(STEP[u])
            self.setSuffix(" " + dict(UNITS)[u])
            self.setToolTip("%s\nRange: %s to %s" % (self.property("tip") or "", fmt(self._min_mm, u, d), fmt(self._max_mm, u, d)))
            self.setValue(round(mm_to_unit(self._mm, u, d), DECIMALS[u]))
        finally:
            self._busy = False

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._refresh()                                         # a too small / too big number snaps into the range


class UnitPicker(QComboBox):
    """px | mm | cm | inch.  Changing it changes the unit of every UnitSpin in the program."""

    def __init__(self, parent=None):
        super().__init__(parent)
        for key, label in UNITS:
            self.addItem(label, key)
        self.setToolTip("The unit of every size in the program")
        self._sync()
        self.currentIndexChanged.connect(lambda _i: set_unit(self.currentData()))
        UnitStore.get().changed.connect(self._sync)

    def _sync(self):
        i = self.findData(get_unit())
        if i >= 0 and i != self.currentIndex():
            self.blockSignals(True)
            self.setCurrentIndex(i)
            self.blockSignals(False)
