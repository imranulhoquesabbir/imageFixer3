"""Freeze finder.  A small timer in the window thread beats every 50 ms; a watcher thread notices when the beat stops for
more than STALL_S seconds and writes what the window thread is doing right then (the Python stack) to stall.log.
Cost: almost nothing.  Delete stall.log (or this file's start() call in app.py) when it is no longer needed."""
import os
import sys
import threading
import time
import traceback

STALL_S = 0.35
_beat = {"t": time.monotonic()}


def start(log_dir):
    from PySide6.QtCore import QTimer
    main_id = threading.get_ident()
    path = os.path.join(log_dir, "stall.log")
    try:
        os.makedirs(log_dir, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.remove(path)
    except Exception:
        pass

    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: _beat.__setitem__("t", time.monotonic()))
    timer.start()
    start.timer = timer                             # keep it alive

    def watch():
        reported = 0.0
        while True:
            time.sleep(0.1)
            t = _beat["t"]
            late = time.monotonic() - t
            if late > STALL_S and t != reported:
                reported = t
                frame = sys._current_frames().get(main_id)
                if frame is None:
                    continue
                stack = "".join(traceback.format_stack(frame)[-14:])
                others = [f for i, f in sys._current_frames().items() if i not in (main_id, threading.get_ident())]
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write("%s  window frozen for >%.2f s  (%d other threads busy)\n%s%s\n"
                                % (time.strftime("%Y-%m-%d %H:%M:%S"), late, len(others), stack, "-" * 60))
                except Exception:
                    pass
    threading.Thread(target=watch, name="stallwatch", daemon=True).start()
