"""Image Fixer's print screen = the PC Print screen of the PrintMerged program, shown inside an Image Fixer window.

It is the SAME page (pcprint/static/index.html) and the SAME code behind it (pcprint/server.py, pcprint/printing.py):
printer + saved printer profiles, Printer Properties, paper, orientation, colour, copies, scaling, many pages on one
sheet, page numbers, Transform, perspective crop, Save PDF.  Nothing is drawn twice in another way.

How it works: a small local web server (this PC only, 127.0.0.1) runs inside Image Fixer; this window shows its page
with Qt's web engine.  The pictures are the files Image Fixer made with all edits (see MainWindow._export_for_print).
Settings, profiles and the save folder are kept in %APPDATA%\\PCPrint (the same place as the PC Print program).
"""
import os
import threading
from concurrent.futures import Future

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QVBoxLayout


PORT = 53187                      # always the same port when it is free, so the page remembers its own small settings
PRINT_W, PRINT_H = 920, 640      # size the print window opens with (it can still be maximised or dragged bigger)
FILTER = "PDF and images (*.pdf *.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff)"
_state = {"server": None, "http": None, "gui": None, "profile": None, "bridge": None, "dlg": None}


class _OnGui(QObject):
    """Lets the server's threads ask the window thread for something (a file window)."""
    _call = Signal(object)

    def __init__(self):
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job):
        fn, fut = job
        try:
            r = fn()
            if fut is not None:
                fut.set_result(r)
        except Exception as e:                    # noqa: BLE001 - handed back to the asking thread
            if fut is not None:
                fut.set_exception(e)

    def post(self, fn):
        """Same, but does not wait for the answer (Esc / Back: the page must not wait while the window closes)."""
        self._call.emit((fn, None))

    def ask(self, fn):
        fut = Future()
        self._call.emit((fn, fut))
        return fut.result()


def _start_server():
    """Starts the local server once (kept until Image Fixer closes). Returns the server module."""
    if _state["server"] is not None:
        return _state["server"]
    from werkzeug.serving import make_server
    from pcprint import server
    http = None
    for port in (PORT, 0):
        try:
            http = make_server("127.0.0.1", port, server.app, threaded=True)
            break
        except OSError:
            continue
    if http is None:
        raise OSError("no free port")
    threading.Thread(target=http.serve_forever, daemon=True).start()
    _state["server"], _state["http"] = server, http
    return server


def _profile():
    """The web engine's own folder (page memory like panel width), apart from Image Fixer's other data."""
    if _state["profile"] is None:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ImageFixer", "printscreen")
        p = QWebEngineProfile("ImageFixerPrint")
        try:
            os.makedirs(base, exist_ok=True)
            p.setPersistentStoragePath(base)
            p.setCachePath(os.path.join(base, "cache"))
            p.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        except Exception:
            pass
        _state["profile"] = p
    return _state["profile"]


def _forget_files(server):
    """Empties the file list of the print screen (the list is never kept between two openings)."""
    with server.SLOCK:
        for fid in list(server.FILES):
            server.drop_file(fid)
        server.ORDER.clear()
        server.GONE.clear()
        server.bump()


class PrintScreen(QDialog):
    def __init__(self, parent, settings, server):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
                         | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.settings = settings
        self.server = server
        self.setWindowTitle("PC Print")
        self.setWindowIcon(parent.windowIcon())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(_profile(), self.view))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        lay.addWidget(self.view)
        self.view.page().setBackgroundColor(QColor("#12151f"))        # no white flash while the page loads
        self.setMinimumSize(640, 460)
        self._place()

    def _place(self):
        """Small window in the middle of Image Fixer's screen.  A size the user chose earlier is kept, but never
        'maximised' (the old setting made the window open big every time); the window button / F11 still go full screen."""
        self.setWindowState(self.windowState() & ~(Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen))   # does not show the window
        screen = (self.parent().screen() if self.parent() is not None else QApplication.primaryScreen()).availableGeometry()
        geo = self.settings.value("printscreen_geometry2")
        ok = False
        if geo is not None:
            try:
                ok = self.restoreGeometry(geo)
            except Exception:
                ok = False
        if not ok:
            w, h = min(PRINT_W, screen.width()), min(PRINT_H, screen.height())
            self.resize(w, h)
            self.move(screen.x() + (screen.width() - w) // 2, screen.y() + (screen.height() - h) // 2)

    def command(self, cmd):
        """Esc / Back / F11 of the page."""
        if cmd == "close":
            self.reject()
        elif cmd == "full":
            if self.isMaximized() or self.isFullScreen():
                self.showNormal()
            else:
                self.showMaximized()

    def done(self, r):
        try:
            if not (self.isMaximized() or self.isFullScreen()):        # only the normal size is remembered
                self.settings.setValue("printscreen_geometry2", self.saveGeometry())
        except Exception:
            pass
        _forget_files(self.server)
        self.view.setUrl(QUrl("about:blank"))
        super().done(r)


def prewarm(win, settings, engine=True):
    """A few seconds after Image Fixer started (nothing is shown): starts the small local server, reads the printer list
    and paper sizes from the driver (background thread) and, with engine=True, starts the web engine with the print
    window hidden.  Then Ctrl+P only has to load the page.  Never raises."""
    try:
        server = _start_server()
        threading.Thread(target=server.warm_printers, daemon=True).start()
        if _state["gui"] is None:
            _state["gui"] = _OnGui()
        if engine and _state["dlg"] is None:
            dlg = PrintScreen(win, settings, server)
            _state["dlg"] = dlg
            dlg.view.setUrl(QUrl("about:blank"))                 # starts the engine's own process; the window stays hidden
    except Exception:
        try:
            import traceback
            from app import log_error
            log_error("PC Print screen could not be prepared:\n" + traceback.format_exc())
        except Exception:
            pass


def open_print_screen(win, settings, files, ticked=None):
    """Shows the PC Print screen with these picture files (all open pictures, in order, so each page keeps its own
    number). ticked = the files that start ticked (None = all). Returns False when it cannot start (the caller then
    uses the older Qt print screen)."""
    try:
        server = _start_server()
    except Exception as e:                                       # a missing library, no free port ...
        try:
            import traceback
            from app import log_error
            log_error("PC Print screen could not start:\n" + traceback.format_exc())
        except Exception:
            pass
        QMessageBox.warning(win, "Print", "The new print screen could not start (%s).\nThe older print screen opens instead." % e)
        return False
    if _state["gui"] is None:
        _state["gui"] = _OnGui()                                 # made here = lives in the window thread
    gui = _state["gui"]
    dlg = _state["dlg"]
    if dlg is None:
        dlg = _state["dlg"] = PrintScreen(win, settings, server)   # kept for the next Ctrl+P (the engine stays warm)
    else:
        dlg._place()
    # the file windows of the page (Add files, Choose folder) are Qt windows of this program
    server.HOOKS["pick"] = lambda: list(gui.ask(lambda: QFileDialog.getOpenFileNames(dlg, "Add files", "", FILTER)[0]))
    server.HOOKS["embed"] = lambda cmd: gui.post(lambda: dlg.command(cmd))     # Esc / Back / F11 of the page
    server.HOOKS["pickdir"] = lambda: gui.ask(lambda: QFileDialog.getExistingDirectory(dlg, "Choose the save folder"))
    # the print screen keeps its OWN page number setting (saved by the page itself); it is not linked to the Numbers tab
    _forget_files(server)
    server.register(files, sort=False)                           # in the order of Image Fixer's pictures
    keep = {os.path.abspath(f) for f in (files if ticked is None else ticked)}
    server.EMBED_UNTICK[:] = [fid for fid in server.ORDER if os.path.abspath(server.FILES[fid]["path"]) not in keep]
    dlg.view.setUrl(QUrl("http://127.0.0.1:%d/" % _state["http"].server_port))
    QTimer.singleShot(0, dlg.view.setFocus)
    dlg.exec()
    server.HOOKS["embed"] = None
    threading.Thread(target=server.warm_printers, daemon=True).start()   # fresh printer data for the next opening
    return True
