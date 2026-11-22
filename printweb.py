"""Image Fixer's print screen = the PC Print screen of the PrintMerged program, shown inside an Image Fixer window.

It is the SAME page (pcprint/static/index.html) and the SAME code behind it (pcprint/server.py, pcprint/printing.py):
printer + saved printer profiles, Printer Properties, paper, orientation, colour, copies, scaling, many pages on one
sheet, page numbers, Transform, perspective crop, Save PDF.  Nothing is drawn twice in another way.

How it works: a small local web server (this PC only, 127.0.0.1) runs inside Image Fixer; this window shows its page
with Qt's web engine.  The pictures are the files Image Fixer made with all edits (see MainWindow._export_for_print).
Settings, profiles and the save folder are kept in %APPDATA%\\PCPrint (the same place as the PC Print program).
"""
import json
import os
import shutil
import threading
import uuid
from concurrent.futures import Future

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QVBoxLayout


PORT = 53187                      # always the same port when it is free, so the page remembers its own small settings
PRINT_W, PRINT_H = 920, 640      # size the print window opens with (it can still be maximised or dragged bigger)
FILTER = "PDF and images (*.pdf *.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff)"
_state = {"server": None, "http": None, "gui": None, "profile": None, "bridge": None, "dlg": None,
          "map": {}, "src": {}, "forks": {}, "live": False, "quit": False}
# "map" = {picture uid of Image Fixer: file id of the print screen}.  "live" = the print page is open (also while it is
# minimised) and holds a session (the pictures, their order, Transform / crop work, ticks).  The session lasts until the
# print screen is closed (Esc / Back / the cross) or the picture is removed from Image Fixer.
# "src" = {uid: the Image Fixer file it was made from}.  "forks" = {file id: uid}: an older version of a picture that
# was changed on the print screen (Transform) and then edited again in Image Fixer - it stays, the new version is added.
SESSION_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ImageFixer", "printsession")


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
    """Empties the file list of the print screen and ends the session."""
    _state["map"].clear()
    _state["src"].clear()
    _state["forks"].clear()
    _state["live"] = False
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
        """Esc / Back / the cross: the print screen is closed and EVERYTHING on it is cleared (pictures, Transform work,
        ticks).  Only the minus button keeps it: a minimised print screen stays as it is until it is opened again."""
        try:
            if not (self.isMaximized() or self.isFullScreen() or self.isMinimized()):   # only the normal size is remembered
                self.settings.setValue("printscreen_geometry2", self.saveGeometry())
        except Exception:
            pass
        _forget_files(self.server)
        _clear_session_dir()
        self.server.HOOKS["embed"] = None
        self.view.setUrl(QUrl("about:blank"))
        super().done(r)
        threading.Thread(target=self.server.warm_printers, daemon=True).start()   # fresh printer data for the next opening


def shutdown():
    """Image Fixer is closing: the hidden print window, its web page and the small local server are closed here, in the
    right order, so the web engine never has to be torn down by the interpreter at the very end (that can hang)."""
    dlg, _state["dlg"] = _state.get("dlg"), None
    _state["quit"] = True
    try:
        if dlg is not None:
            dlg.view.setUrl(QUrl("about:blank"))
            dlg.close()
    except Exception:
        pass
    _clear_session_dir()
    http, _state["http"] = _state.get("http"), None
    if http is not None:
        threading.Thread(target=http.shutdown, daemon=True).start()


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


def _fid_for(server, path):
    ap = os.path.abspath(path)
    with server.SLOCK:
        return next((i for i in server.ORDER if i in server.FILES and os.path.abspath(server.FILES[i]["path"]) == ap), None)


def _own(path):
    """The edited copy of a picture lives in Image Fixer's export folder and is deleted when the picture is edited again
    or removed.  The print screen gets its own link to such a file (no extra disk space), so it stays usable.
    A picture that was never edited is its own file and is used as it is."""
    try:
        from card import EXPORT_DIR
        ap = os.path.abspath(path)
        if os.path.dirname(ap) != os.path.abspath(EXPORT_DIR):
            return path
        folder = os.path.join(SESSION_DIR, uuid.uuid4().hex[:10])       # own folder: the file keeps its name
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, os.path.basename(ap))
        try:
            os.link(ap, dst)
        except OSError:
            shutil.copy2(ap, dst)
        return dst
    except Exception:
        return path


def _clear_session_dir():
    shutil.rmtree(SESSION_DIR, ignore_errors=True)


_TOUCHED_JS = ("(function(){try{var o=[];for(var k in LAY){var L=LAY[k];if(!L||!L.touched)continue;o.push(k.split(':')[0]);"
               "(L.items||[]).forEach(function(i){o.push(i.f)})}return JSON.stringify(o)}catch(e){return '[]'}})()")


def _touched_files(dlg, timeout_ms=1500):
    """File ids that have Transform work on the print page (moved / resized / turned / copied pictures).  The page is
    asked and waited for a moment.  No answer = None (then nothing is replaced: the old version is kept, never lost)."""
    from PySide6.QtCore import QEventLoop
    got = {"v": None}
    loop = QEventLoop()

    def cb(v):
        got["v"] = v
        loop.quit()
    try:
        try:
            dlg.view.page().runJavaScript(_TOUCHED_JS, cb)
        except TypeError:
            dlg.view.page().runJavaScript(_TOUCHED_JS, 0, cb)
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return set(json.loads(got["v"])) if got["v"] else None
    except Exception:
        return None


def _sync_session(server, dlg, pairs, live):
    """The print screen was used before and still holds its pictures (with their Transform / crop work).
    pairs = [(picture uid, file)] being printed now; live = uids of every picture that is still open in Image Fixer.
      - a picture that was removed from Image Fixer goes from the print screen too
      - a picture that was edited again gets its new file in the same place (its page numbers stay) ...
      - ... but if it has Transform work on the print screen, that version stays and the new one is ADDED at the end
      - a new picture is added at the END, so its page number follows the others
    Returns True when the session still has pictures."""
    smap, src, forks = _state["map"], _state["src"], _state["forks"]
    with server.SLOCK:
        for uid in list(smap):
            fid = smap[uid]
            if fid not in server.FILES:                 # it was removed on the print screen itself
                smap.pop(uid)
                src.pop(uid, None)
            elif uid not in live:
                server.drop_file(fid)
                smap.pop(uid)
                src.pop(uid, None)
        for fid, uid in list(forks.items()):
            if fid not in server.FILES:
                forks.pop(fid)
            elif uid not in live:
                server.drop_file(fid)
                forks.pop(fid)
    changed = [(uid, os.path.abspath(p)) for uid, p in pairs if uid in smap and src.get(uid) != os.path.abspath(p)]
    touched = _touched_files(dlg) if changed else set()
    for uid, path in pairs:
        ap = os.path.abspath(path)
        fid = smap.get(uid)
        if fid is not None and src.get(uid) == ap:
            continue                                    # the same picture, nothing changed
        own = _own(ap)
        if fid is not None and touched is not None and fid not in touched:
            try:
                nf = server.open_file(own)              # new details (size, shape) for the edited picture ...
            except Exception:
                continue
            with server.SLOCK:
                new = server.FILES.pop(nf, None)
                cur = server.FILES.get(fid)
                if new and cur is not None:             # ... go into the old entry: same place, same print-screen settings
                    for k in ("name", "kind", "pages", "lands", "asps", "path"):
                        cur[k] = new[k]
            src[uid] = ap
        else:                                           # a new picture, or one whose Transform work must stay as it is
            if fid is not None:
                forks[fid] = uid
                smap.pop(uid, None)
            server.register([own], sort=False)
            nf = _fid_for(server, own)
            if nf:
                smap[uid] = nf
                src[uid] = ap
    with server.SLOCK:
        server.bump()
        return bool(server.ORDER)


def open_print_screen(win, settings, files, ticked=None, uids=None, live=None):
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
    elif not dlg.isVisible():
        dlg._place()                                             # (an open / minimised print screen keeps its size and place)
    # the file windows of the page (Add files, Choose folder) are Qt windows of this program
    server.HOOKS["pick"] = lambda: list(gui.ask(lambda: QFileDialog.getOpenFileNames(dlg, "Add files", "", FILTER)[0]))
    server.HOOKS["embed"] = lambda cmd: gui.post(lambda: dlg.command(cmd))     # Esc / Back / F11 of the page
    server.HOOKS["pickdir"] = lambda: gui.ask(lambda: QFileDialog.getExistingDirectory(dlg, "Choose the save folder"))
    # the print screen keeps its OWN page number setting (saved by the page itself); it is not linked to the Numbers tab
    reuse = False
    if uids is not None and live is not None and dlg.isVisible() and _state["live"] and _state["map"]:
        reuse = _sync_session(server, dlg, list(zip(uids, files)), set(live))
    if reuse:
        server.EMBED_UNTICK[:] = []                              # the page is still loaded: ticks, order and Transform work stay as they are
        QTimer.singleShot(60, lambda: dlg.view.page().runJavaScript("try{refresh(true)}catch(e){}"))
    else:
        _forget_files(server)
        _clear_session_dir()
        owned = [_own(f) for f in files] if uids is not None else list(files)
        server.register(owned, sort=False)                       # in the order of Image Fixer's pictures
        keep = {os.path.abspath(f) for f in (owned if ticked is None else [o for o, f in zip(owned, files) if f in ticked])}
        server.EMBED_UNTICK[:] = [fid for fid in server.ORDER if os.path.abspath(server.FILES[fid]["path"]) not in keep]
        if uids is not None:
            for uid, f, o in zip(uids, files, owned):
                fid = _fid_for(server, o)
                if fid:
                    _state["map"][uid] = fid
                    _state["src"][uid] = os.path.abspath(f)
        dlg.view.setUrl(QUrl("http://127.0.0.1:%d/" % _state["http"].server_port))
        _state["live"] = True
    QTimer.singleShot(0, dlg.view.setFocus)
    dlg.setModal(False)                                          # Image Fixer stays usable while the print screen is minimised
    dlg.show()
    dlg.setWindowState((dlg.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)   # brings a minimised one back
    dlg.raise_()
    dlg.activateWindow()
    return True
