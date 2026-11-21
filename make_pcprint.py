"""Builds imageFixer3-main/pcprint/ from PrintMerged-main: the SAME print screen (index.html) and the SAME server code,
only the parts that make no sense inside Image Fixer (phone link, internet tunnel, own auto update) are taken out."""
import os, sys, shutil
src = sys.argv[1]
dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pcprint")
os.makedirs(os.path.join(dst, "static"), exist_ok=True)
shutil.copy(os.path.join(src, "printing.py"), os.path.join(dst, "printing.py"))
shutil.copy(os.path.join(src, "static", "index.html"), os.path.join(dst, "static", "index.html"))

s = open(os.path.join(src, "server.py"), encoding="utf-8").read()

def cut(s, a, b, new=""):
    i = s.index(a); j = s.index(b, i)
    return s[:i] + new + s[j:]

def rep(s, a, b):
    assert s.count(a) == 1, a
    return s.replace(a, b)

s = rep(s, "import printing\nimport updatemgr\n", "from . import printing\n")
s = rep(s, "app.register_blueprint(updatemgr.bp)   # auto update routes (/api/update...), PC window only\n", "")
s = rep(s, 'updatemgr.HOOKS["busy"] = lambda: any(j.get("state") == "run" for j in list(JOBS.values()))\nupdatemgr.HOOKS["files"] = lambda: len(FILES)\n', "")
s = rep(s, '''def res(p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, p)''', '''def res(p):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), p)''')
s = rep(s, '''    try:
        tunnel_stop()
    except Exception:
        pass
    if _UP["dir"]:''', '''    if _UP["dir"]:''')
# phone / internet link / PIN: not needed inside Image Fixer (the server only listens on this PC)
new_guard = '''def origin():
    return "pc"       # inside Image Fixer only this PC's own window can reach the server (it listens on 127.0.0.1 only)


def host_ok():
    """Only an IP address or 'localhost' is a valid Host. A malicious web page reaches this PC under ITS OWN domain
    name, so any other name is refused."""
    raw = (request.host or "").strip().lower()
    host = raw[1:raw.index("]")] if raw.startswith("[") and "]" in raw else raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@app.before_request
def guard():
    if not host_ok():
        return "bad host", 403          # DNS rebinding: a web page cannot reach this program under its own domain name
    if request.remote_addr not in LOCAL:
        return "forbidden", 403


'''
s = cut(s, "PLOCK2 = threading.Lock()", '@app.post("/api/login")', new_guard)
s = cut(s, '@app.post("/api/login")', "# ------------------------------------------------------------------ files",
        '''@app.post("/api/login")
def login():
    return jsonify(ok=True)          # the page still asks once; no PIN inside Image Fixer


''')
# page numbers: Image Fixer's Numbers tab <-> this page (one setting). Pages Image Fixer did not choose start unticked.
s = rep(s, '@app.get("/api/settings")\ndef settings_get():', '''EMBED_UNTICK = []      # file ids that start unticked (the other open pictures of Image Fixer: they only keep the numbering right)


@app.get("/api/untick")
def untick():
    return jsonify(list(EMBED_UNTICK))


def push_num(num):
    """Image Fixer changed the page numbers: write them where the page reads them. The page follows (like a change
    made on the other side)."""
    num = {k: num[k] for k in ("on", "pos", "align", "size", "start", "bold", "margin") if k in num}
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        try:
            with open(SET_FILE, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}
        if old.get("num") == num:
            return
        old["num"] = num
        with open(SET_FILE, "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        SREV["n"] += 1
    except Exception:
        pass


@app.get("/api/settings")
def settings_get():''')
s = rep(s, '''        old.update(d)   # keys written by the other side survive
        with open(SET_FILE, "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        SREV["n"] += 1
''', '''        old.update(d)   # keys written by the other side survive
        with open(SET_FILE, "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        SREV["n"] += 1
        if isinstance(d.get("num"), dict) and HOOKS.get("num"):
            HOOKS["num"](d["num"])        # Image Fixer follows (its Numbers tab)
''')
# index: same page, only the phone / update buttons are hidden (they belong to the stand-alone PC Print program)
s = rep(s, '''    page = "index.html" if origin() == "pc" else "phone.html"
    r = send_file(res("static/" + page))
    r.headers["Cache-Control"] = "no-store"
    return r''', '''    with open(res("static/index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("</head>", EMBED_HEAD + "</head>", 1)
    r = app.response_class(html, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r''')
s = rep(s, '@app.get("/")\ndef index():', '''# Inside Image Fixer: no phone link and no PC Print auto update (Image Fixer has its own)
EMBED_HEAD = ("<script>window.EMBED=1</script>"
              "<style>#bph,#phpanel,#upddd{display:none!important}</style>")


@app.get("/")
def index():''')
# faster print screen: printer list / paper sizes are kept (see drv_cached), Esc / Back / F11 reach the Image Fixer window
s = rep(s, '@app.get("/api/untick")\ndef untick():', '@app.post("/api/embed")\ndef embed_cmd():\n    """The page asks the Image Fixer window to close or to go full screen (Esc, Back, F11)."""\n    d = request.get_json(silent=True) or {}\n    h = HOOKS.get("embed")\n    if h and d.get("cmd") in ("close", "full"):\n        h(d["cmd"])\n    return jsonify(ok=True)\n\n\n' + '@app.get("/api/untick")\ndef untick():')
s = rep(s, '@app.get("/api/printers")\ndef printers():', 'DRV_CACHE = {}       # key -> (time, value): reading the printer list / paper sizes from the driver is slow\nDRV_BUSY = set()\n\n\ndef drv_cached(key, fn, ttl=60.0):\n    """The last answer comes back at once; when it is older than ttl it is read again in the background, so the NEXT\n    opening of the print screen has fresh data.  Only the very first reading waits for the driver."""\n    hit = DRV_CACHE.get(key)\n    if hit:\n        if time.time() - hit[0] > ttl and key not in DRV_BUSY:\n            DRV_BUSY.add(key)\n\n            def again():\n                try:\n                    with DRV_LOCK:\n                        v = fn()\n                    if not (isinstance(v, dict) and v.get("error")):\n                        DRV_CACHE[key] = (time.time(), v)\n                except Exception:\n                    DRV_CACHE.pop(key, None)\n                finally:\n                    DRV_BUSY.discard(key)\n            threading.Thread(target=again, daemon=True).start()\n        return hit[1]\n    v = fn()\n    if not (isinstance(v, dict) and v.get("error")):\n        DRV_CACHE[key] = (time.time(), v)\n    return v\n\n\ndef warm_printers():\n    """Reads the printer list and the paper sizes of the first / default printer now (background thread)."""\n    try:\n        with DRV_LOCK:\n            lst = drv_cached("*", printing.list_printers)\n            names = []\n            for p in lst:\n                if p.get("default") and p["name"] not in names:\n                    names.append(p["name"])\n            if lst and lst[0]["name"] not in names:\n                names.append(lst[0]["name"])\n            for n in names[:2]:\n                drv_cached("c:" + n, lambda n=n: printing.caps(n))\n    except Exception:\n        pass\n\n\n' + '@app.get("/api/printers")\ndef printers():')
s = rep(s, "        return jsonify(printing.list_printers())", '        return jsonify(drv_cached("*", printing.list_printers))')
s = rep(s, '        return jsonify(printing.caps(request.args.get("name", "")))',
        '        pn = request.args.get("name", "")\n        return jsonify(drv_cached("c:" + pn, lambda: printing.caps(pn)))')
s = rep(s, "                data = printing.driver_dialog(printer_name, base)\n",
        "                data = printing.driver_dialog(printer_name, base)\n                DRV_CACHE.clear()          # the driver settings may have changed: read them again next time\n")
open(os.path.join(dst, "server.py"), "w", encoding="utf-8").write(s)
open(os.path.join(dst, "__init__.py"), "w").write('"""The PrintMerged / PC Print print screen, running inside Image Fixer (see printweb.py)."""\n')

# index.html: do not start the auto update checks / start-with-windows lookup inside Image Fixer
p = os.path.join(dst, "static", "index.html")
h = open(p, encoding="utf-8").read()
h = rep(h, "(async()=>{initAuto();upLoad();svLoad();", "(async()=>{if(!window.EMBED){initAuto();upLoad()}svLoad();")
# Image Fixer opens straight on the preview page (step 2); "Back to pages" still goes to the page list
h = rep(h, "await refresh(true);setInterval(", "await refresh(true);if(window.EMBED){try{const u=await (await api('/api/untick')).json();FILES.forEach(f=>{if(u.includes(f.id))for(let p=0;p<f.pages;p++)DESEL.add(key(f.id,p))});renderPages()}catch{}if(FILES.length)go(2)}setInterval(")
# Image Fixer window: Esc / Back go straight back to Image Fixer (the page list is of no use there), same keys as Image Fixer
h = rep(h, "async function api(url,opt){return fetch(url,opt)}\n", "async function api(url,opt){return fetch(url,opt)}\nfunction embedCmd(c){try{fetch('/api/embed',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd:c})})}catch{}}   // Image Fixer window: close / full screen\n")
h = rep(h, "$('#close').onclick=()=>go(1);", "$('#close').onclick=()=>window.EMBED?embedCmd('close'):go(1);")
h = rep(h, """    if(!$('#numpanel').hidden)$('#numpanel').hidden=true;else if(TF.on&&TF.sel){TF.sel=null;tfSelChanged()}else if(STEP===2)go(1);
  }
});
""", "    if(PC.open)return;   // the perspective-crop window handles its own Escape\n    const t=e.target;\n    if(window.EMBED&&t&&/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)){t.blur();return}\n    if(!$('#numpanel').hidden)$('#numpanel').hidden=true;else if(TF.on&&TF.sel){TF.sel=null;tfSelChanged()}else if(window.EMBED)embedCmd('close');else if(STEP===2)go(1);\n  }\n});\ndocument.addEventListener('keydown',e=>{   // Image Fixer window: the same important keys as in Image Fixer\n  if(!window.EMBED||PC.open)return;\n  if(e.key==='F11'){e.preventDefault();embedCmd('full');return}\n  if(!(e.ctrlKey||e.metaKey)||e.altKey||e.shiftKey)return;\n  const k=e.key.toLowerCase();\n  if(k==='s'){e.preventDefault();$('#bsave').click()}          // Ctrl+S = Save PDF\n  else if(k==='o'){e.preventDefault();$('#add').click()}       // Ctrl+O = Add files\n  else if(k==='w'){e.preventDefault();embedCmd('close')}       // Ctrl+W = back to Image Fixer\n});\n")
h = rep(h, "await refresh(true);if(window.EMBED){try{const u=",
        "await refresh(true);if(window.EMBED){{const b=$('#close');b.title='Back to Image Fixer (Esc)';const l=b.querySelector('.lbl2');if(l)l.textContent='Back'}try{const u=")
open(p, "w", encoding="utf-8").write(h)
print("ok", len(s.splitlines()), "lines in server.py")
