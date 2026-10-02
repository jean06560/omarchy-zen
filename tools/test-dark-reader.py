#!/usr/bin/env python3
"""Behavioural test of the Omarchy -> Dark Reader sync, on a throwaway Zen.

Starts a headless Zen with a temporary profile (your real profile and your
running Zen are never touched), installs Dark Reader from your profile's .xpi,
loads live/darkreader/OmarchyDR.sys.mjs, feeds it a light then a dark then a light
custom-zen.css, and checks the computed colours of a real web page.

Needs: geckodriver (pacman -S geckodriver) and Dark Reader installed in your
Zen profile (its .xpi is reused). Run it after a Zen update: it tells you
whether the Gecko internals the Dark Reader sync relies on still work.

    python3 tools/test-dark-reader.py      # exit 0 = PASS
"""
import http.server, json, os, shutil, socketserver, subprocess, sys, tempfile
import threading, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "live", "darkreader")
ZEN_BIN = os.environ.get("ZEN_BIN", "/opt/zen-browser-bin/zen-bin")
GD_PORT = int(os.environ.get("GD_PORT", "4444"))
WWW_PORT = int(os.environ.get("WWW_PORT", "8099"))
BASE = f"http://127.0.0.1:{GD_PORT}"

LIGHT = ("light", "#ecd3a1", "#3c4747")
DARK = ("dark", "#1a1b26", "#c0caf5")


def rgb(h):
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def req(method, path, body=None):
    r = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(r, timeout=90))["value"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.load(e)["value"])


def real_xpi():
    zen_root = os.environ.get("ZEN_CONFIG_DIR") or os.path.expanduser(
        "~/.zen" if os.path.isdir(os.path.expanduser("~/.zen")) else "~/.config/zen")
    rel = [l.split("=", 1)[1].strip() for l in open(os.path.join(zen_root, "installs.ini"))
           if l.startswith("Default=")][0]
    p = os.path.join(zen_root, rel, "extensions", "addon@darkreader.org.xpi")
    if not os.path.exists(p):
        sys.exit(f"Dark Reader .xpi not found: {p}")
    return p


def css(scheme, bg, fg):
    return (f":root {{ --custom-zen-color-scheme: {scheme}; --custom-zen-bg: {bg}; "
            f"--custom-zen-fg: {fg}; --custom-zen-selection-bg: {fg}; }}")


def main():
    if not shutil.which("geckodriver"):
        sys.exit("geckodriver missing: omarchy pkg add geckodriver")
    tmp = tempfile.mkdtemp(prefix="zen-dr-lab-")
    prof = os.path.join(tmp, "prof")
    www = os.path.join(tmp, "www")
    os.makedirs(os.path.join(prof, "chrome", "omarchy-dr"))
    os.makedirs(www)
    for f in os.listdir(SRC):
        shutil.copy(os.path.join(SRC, f), os.path.join(prof, "chrome", "omarchy-dr", f))
    open(os.path.join(www, "index.html"), "w").write(
        '<html><body style="background:#fff;color:#000"><h1>test</h1><p>hello</p></body></html>')

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(s, *a, **k): super().__init__(*a, directory=www, **k)
        def log_message(s, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", WWW_PORT), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    gd = subprocess.Popen(["geckodriver", "--port", str(GD_PORT), "--allow-system-access"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sid = None
    failures = []
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(BASE + "/status", timeout=1); break
            except Exception:
                time.sleep(0.25)
        v = req("POST", "/session", {"capabilities": {"alwaysMatch": {
            "browserName": "firefox",
            "moz:firefoxOptions": {"binary": ZEN_BIN, "args": ["-headless", "-no-remote", "-profile", prof]}}}})
        sid = v["sessionId"]
        S = f"/session/{sid}"
        req("POST", S + "/moz/addon/install", {"path": real_xpi(), "temporary": True})
        print("Zen", v["capabilities"]["browserVersion"], "- Dark Reader installed")

        def ctx(c): req("POST", S + "/moz/context", {"context": c})
        def ex(script, args=None): return req("POST", S + "/execute/sync", {"script": script, "args": args or []})

        ctx("chrome")
        ex("""const dir = Services.dirsvc.get("ProfD", Ci.nsIFile); dir.append("chrome"); dir.append("omarchy-dr");
              Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
                .setSubstitution("omarchy-dr", Services.io.newFileURI(dir));""")
        sync = 'ChromeUtils.importESModule("resource://omarchy-dr/OmarchyDR.sys.mjs").sync(arguments[0]); return 1;'
        debug = 'return JSON.stringify(ChromeUtils.importESModule("resource://omarchy-dr/OmarchyDR.sys.mjs").debug)'

        ex(sync, [css(*LIGHT)])
        for _ in range(40):                       # module retries while Dark Reader starts
            time.sleep(0.5)
            if json.loads(ex(debug))["last"] == "ok":
                break
        print("module state:", ex(debug))

        # A fresh tab (the initial headless tab is not processed by Dark Reader).
        ctx("content")
        req("POST", S + "/window", {"handle": req("POST", S + "/window/new", {"type": "tab"})["handle"]})
        req("POST", S + "/url", {"url": f"http://127.0.0.1:{WWW_PORT}/"})

        def colors():
            return ex("return getComputedStyle(document.body).backgroundColor + ' | ' + getComputedStyle(document.body).color")

        def expect(name, theme):
            want = f"{rgb(theme[1])} | {rgb(theme[2])}"
            got = None
            for _ in range(30):
                time.sleep(0.5)
                got = colors()
                if got == want:
                    print(f"PASS {name}: {got}"); return
            failures.append(name)
            print(f"FAIL {name}: got {got}, want {want}")

        expect("light (initial)", LIGHT)
        for name, theme in [("dark (live swap)", DARK), ("back to light (live swap)", LIGHT)]:
            ctx("chrome"); ex(sync, [css(*theme)]); ctx("content")
            expect(name, theme)
    except Exception as e:
        failures.append(f"exception: {e}")
        print("ERROR:", e)
    finally:
        try:
            if sid: req("DELETE", f"/session/{sid}")
        except Exception:
            pass
        gd.terminate()
        httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT:", "PASS" if not failures else "FAIL " + ", ".join(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
