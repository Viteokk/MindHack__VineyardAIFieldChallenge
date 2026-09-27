"""Web app checks for the QA report: the site in a real (headless) Chromium on a phone, a tablet and a desktop.

The static site (web/) is served locally by a threaded HTTP server, opened with Playwright, and the user flows the
field staff depend on are exercised: pages load without JavaScript errors or missing files, nothing scrolls sideways
on a 375 px phone / 768 px tablet / 1440 px desktop, sign-in opens the app in its role, the three languages translate
the interface without freezing it, a zone can be drawn freehand / edited / extended / cut (mouse and touch), a walking
route is computed in the browser, navigation works when location is refused, and the load time and data volume.
Screenshots of each device go to docs/qa/web_*.png.

Registered into pipeline/qa.py by register(check, res, grade, evid_dir); skipped (WARN) when Playwright or a Chromium
build is not available: `pip install playwright && playwright install chromium`.
"""
from __future__ import annotations

import atexit
import functools
import http.server
import json
import re
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = C.ROOT / "web"
DEVICES = {"phone": (375, 812), "tablet": (768, 1024), "desktop": (1440, 900)}
APP_VIEWS = ["azi", "plantatie", "zona", "harta", "rapoarte"]
SESSION = {"role": "inspector", "name": "Inspector demo", "org": "DEMO", "email": "inspector@fieldplanner.demo", "via": "qa"}
EXPECTED_404 = ("/api/",)          # the live-mode probe: the static site has no API and says so


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256             # a browser opens many connections at once; the default backlog (5) resets them


class Web:
    """Local server + one headless Chromium, shared by the checks, closed at exit."""
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        handler = functools.partial(_Quiet, directory=str(WEB))
        self.httpd = _Server(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self._launch()
        self.requests, self.failed, self.errors = [], [], []
        atexit.register(self.close)

    def _launch(self):
        try:
            return self.pw.chromium.launch()
        except Exception:
            # a Chromium build already on disk (another Playwright version installed it)
            for cache in (Path.home() / "Library/Caches/ms-playwright", Path.home() / ".cache/ms-playwright"):
                for d in sorted(cache.glob("chromium_headless_shell-*"), reverse=True):
                    exe = next(iter(d.glob("*/chrome-headless-shell")), None)
                    if exe:
                        return self.pw.chromium.launch(executable_path=str(exe))
            raise

    def close(self):
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass
        self.httpd.shutdown()

    def page(self, device="desktop", lang="ro", session=True, touch=None, init="", intro=False):
        w, h = DEVICES[device]
        ctx = self.browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1,
                                       has_touch=device != "desktop" if touch is None else touch,
                                       is_mobile=device == "phone")
        store = {"fpm.lang": json.dumps(lang)}
        if session:
            store.update({"fpm.session": json.dumps(SESSION), "fpm.role": json.dumps(SESSION["role"])})
        ctx.add_init_script("try{" + "".join(f"localStorage.setItem({json.dumps(k)},{json.dumps(v)});" for k, v in store.items())
                            + ("" if intro else "sessionStorage.setItem('fpm.introDone','1');")   # the map intro only in W11
                            + "}catch(e){}" + init)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"{pg.url.split('/')[-1][:40]}: {e}"))
        pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text
              and self.errors.append(f"{pg.url.split('/')[-1][:40]}: {m.text[:160]}"))
        pg.on("requestfinished", lambda r: self.requests.append(r.url))
        pg.on("response", lambda r: r.status >= 400 and not any(x in r.url for x in EXPECTED_404)
              and self.failed.append(f"{r.status} {r.url.replace(self.base, '')}"))
        pg.on("requestfailed", lambda r: not any(x in r.url for x in EXPECTED_404) and "ERR_ABORTED" not in str(r.failure)
              and self.failed.append(f"failed {r.url.replace(self.base, '')}: {r.failure}"))   # aborted = the test left the page
        return ctx, pg

    def open_app(self, pg, view="harta"):
        pg.goto(self.base + f"app.html#{view}", wait_until="load")
        pg.wait_for_function("() => { const l = document.querySelector('#loader'); return !l || l.hidden || getComputedStyle(l).display === 'none' || getComputedStyle(l).opacity === '0'; }", timeout=30000)
        pg.wait_for_timeout(600)


OVERFLOW_JS = """() => {
  const W = innerWidth, vis = e => { const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden' && !e.closest('[hidden]'); };
  const bad = [...document.querySelectorAll('body *')].filter(e => {
    if (e.closest('.leaflet-pane, .leaflet-control-container')) return false;
    const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && vis(e) && (r.right > W + 1 || r.left < -1); });
  return {scroll: document.documentElement.scrollWidth - W, bad: bad.slice(0, 5).map(e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + '.' + [...e.classList].slice(0, 2).join('.'))};
}"""


def register(check, res, grade, evid: Path):
    def web_or_skip(fn):
        @functools.wraps(fn)
        def run():
            try:
                w = Web.get()
            except Exception as ex:
                return res("WARN", f"not run: {type(ex).__name__}", "", "Playwright / Chromium not available here: "
                           "`pip install playwright && playwright install chromium`, then rerun `python -m pipeline.qa`.")
            return fn(w)
        return run

    @check("W1", "web", "Pages load without JavaScript errors (landing, sign-in, app)")
    @web_or_skip
    def w1(w):
        n0 = len(w.errors)
        for url in ("index.html", "login.html"):
            ctx, pg = w.page()
            pg.goto(w.base + url, wait_until="load")
            pg.wait_for_timeout(800)
            ctx.close()
        ctx, pg = w.page()
        w.open_app(pg, "harta")
        for v in APP_VIEWS:
            pg.evaluate(f"location.hash = '{v}'")
            pg.wait_for_timeout(500)
        ctx.close()
        errs = w.errors[n0:]
        return res("PASS" if not errs else "FAIL", f"{len(errs)} errors on 3 pages and {len(APP_VIEWS)} app views", "0",
                   "Uncaught exceptions and console errors, desktop Chromium. Missing files are counted in W10.", errs[:6])

    @check("W2", "web", "Phone, tablet and desktop: nothing scrolls sideways, every page and app view fits")
    @web_or_skip
    def w3(w):
        issues, shots = [], []
        evid.mkdir(parents=True, exist_ok=True)
        for dev in DEVICES:
            for url in ("index.html", "login.html"):
                ctx, pg = w.page(dev)
                pg.goto(w.base + url, wait_until="load")
                pg.wait_for_timeout(700)
                o = pg.evaluate(OVERFLOW_JS)
                if o["scroll"] > 1 or o["bad"]:
                    issues.append(f"{dev} {url}: scroll +{o['scroll']} px {o['bad']}")
                if url == "index.html" and dev != "desktop":
                    p = evid / f"web_{dev}_landing.png"
                    pg.screenshot(path=str(p))
                    shots.append(p.relative_to(C.ROOT).as_posix())
                ctx.close()
            ctx, pg = w.page(dev)
            w.open_app(pg, "azi")
            for v in APP_VIEWS:
                pg.evaluate(f"location.hash = '{v}'")
                pg.wait_for_timeout(450)
                o = pg.evaluate(OVERFLOW_JS)
                if o["scroll"] > 1 or o["bad"]:
                    issues.append(f"{dev} app#{v}: scroll +{o['scroll']} px {o['bad']}")
            pg.evaluate("location.hash = 'harta'")
            pg.wait_for_timeout(900)
            p = evid / f"web_{dev}_app.png"
            pg.screenshot(path=str(p))
            shots.append(p.relative_to(C.ROOT).as_posix())
            ctx.close()
        r = res("PASS" if not issues else "FAIL", f"{3 * (2 + len(APP_VIEWS))} page × device combinations, {len(issues)} with overflow",
                "0", "Viewports 375 × 812 (phone, touch), 768 × 1024 (tablet, touch), 1440 × 900 (desktop): the page width "
                "never exceeds the screen and no visible element sticks out of it (map imagery excluded).", issues[:6])
        r["images"] = shots
        return r

    @check("W3", "web", "Sign-in with the demo account opens the app in the account's role")
    @web_or_skip
    def w4(w):
        ctx, pg = w.page(session=False)
        pg.goto(w.base + "login.html", wait_until="load")
        t = time.time()
        pg.click("#go")
        pg.wait_for_url(re.compile(r"app\.html"), timeout=15000)
        w.open_app(pg, pg.url.split("#")[-1] if "#" in pg.url else "azi")
        dt = time.time() - t
        role = pg.evaluate("(() => { try { return JSON.parse(localStorage.getItem('fpm.session')).role; } catch (e) { return null; } })()")
        head = pg.inner_text("#p-head") if pg.query_selector("#p-head") else ""
        ctx.close()
        ok = role == "inspector"
        return res("PASS" if ok else "FAIL", f"login → app in {dt:.1f} s, role {role}, first page “{head.splitlines()[-1] if head else '?'}”",
                   "role = inspector", "The demo credentials are prefilled by the page and never leave the browser.")

    @check("W4", "web", "Romanian, Russian and English: the interface translates and stays responsive")
    @web_or_skip
    def w5(w):
        expect = {"ro": "Hartă", "ru": "Карта", "en": "Map"}
        out, worst, bad = [], 0.0, []
        for lang, word in expect.items():
            ctx, pg = w.page(lang=lang)
            w.open_app(pg, "harta")
            lat = []
            for v in APP_VIEWS:
                pg.evaluate(f"location.hash = '{v}'")
                pg.wait_for_timeout(300)
                t = time.time()
                pg.evaluate("1")                       # a frozen page (translation loop) would not answer
                lat.append((time.time() - t) * 1000)
            rail = pg.inner_text("body")
            ok = word in rail
            worst = max(worst, max(lat))
            out.append(f"{lang}: “{word}” {'shown' if ok else 'MISSING'}, max {max(lat):.0f} ms")
            if not ok:
                bad.append(lang)
            ctx.close()
        st = "PASS" if not bad and worst < 1000 else "FAIL"
        return res(st, "; ".join(out), "labels translated, page answers < 1 s",
                   "Each language loaded from the user's choice; the page must answer a script call after every view "
                   "change (catches a translation loop that once froze the English interface).")

    def stroke(pg, cx, cy, rx, ry):
        pg.mouse.move(cx + rx, cy)
        pg.mouse.down()
        import math
        for a in range(0, 361, 12):
            pg.mouse.move(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)), steps=2)
        pg.mouse.up()
        pg.wait_for_timeout(700)

    @check("W5", "web", "Work zone: freehand drawing, corner editing, add and exclude a shape (mouse and touch)")
    @web_or_skip
    def w6(w):
        steps = []
        ctx, pg = w.page()
        w.open_app(pg, "zona")
        if pg.query_selector('[data-act="z-clear"]'):
            pg.click('[data-act="z-clear"]')
        pg.click('[data-zmode="free"]')
        box = pg.query_selector("#map").bounding_box()
        cx, cy = box["x"] + box["width"] * 0.62, box["y"] + box["height"] * 0.5
        stroke(pg, cx, cy, 120, 90)
        steps.append(("freehand zone", "zona=poly:" in pg.url))
        pg.click('[data-act="z-edit"]')
        pg.wait_for_timeout(300)
        steps.append(("corner handles", pg.evaluate("document.querySelectorAll('.zv').length") > 6))
        pg.click('#drawbar [data-act="z-edit"]')
        pg.wait_for_timeout(300)
        pg.click('[data-act="z-add"]')
        stroke(pg, cx + 300, cy, 70, 60)
        steps.append(("add a shape", "|" in pg.url))
        pg.click('[data-act="z-cut"]')
        stroke(pg, cx, cy, 30, 25)
        steps.append(("exclude a part", "~" in pg.url))
        ctx.close()
        # phone: corners by touch, finished from the floating bar
        ctx, pg = w.page("phone")
        w.open_app(pg, "zona")
        if pg.query_selector('[data-act="z-clear"]'):
            pg.click('[data-act="z-clear"]')
        pg.click('[data-zmode="free"]')
        pg.wait_for_timeout(400)
        for x, y in ((110, 260), (270, 250), (250, 430)):
            pg.touchscreen.tap(x, y)
            pg.wait_for_timeout(150)
        pg.click('#drawbar [data-act="z-finish"]')
        pg.wait_for_timeout(700)
        steps.append(("phone: corners by touch + Gata", "zona=poly:" in pg.url))
        ctx.close()
        ok = all(s for _, s in steps)
        return res("PASS" if ok else "FAIL", ", ".join(f"{n} {'✓' if s else '✗'}" for n, s in steps), "all steps",
                   "Mouse on a desktop, touch taps on a 375 px phone (the panel folds away while drawing; the floating bar "
                   "keeps Anulează / Gata on screen).")

    @check("W6", "web", "Walking route computed in the browser for a chosen block")
    @web_or_skip
    def w7(w):
        rep = json.loads((WEB / "data" / "blocks_report.json").read_text())["blocks"]
        b = max((x for x in rep if 3 <= x.get("gaps", 0) <= 25), key=lambda x: x["gaps"], default=rep[0])
        ctx, pg = w.page()
        w.open_app(pg, f"zona=bloc:{b['vineyard_id']}")
        pg.wait_for_timeout(1500)
        t = time.time()
        pg.click('[data-act="pl-run"]')
        try:
            pg.wait_for_selector('[data-act="gpx"][data-route="live"]', timeout=120000)
            ok = True
        except Exception:
            ok = False
        dt = time.time() - t
        txt = pg.inner_text("#p-body") if ok else ""
        ctx.close()
        m = re.search(r"([\d.,]+)\s*(km|m)\b", txt)
        return res("PASS" if ok else "FAIL", f"block {b['vineyard_id']} ({b['gaps']} gaps): route in {dt:.1f} s"
                   + (f", {m.group(1)} {m.group(2)}" if m else ""), "computed, < 120 s",
                   "`web/router.js` in a Web Worker: 0.5 m grid, rows as walls, TSP; the same rules as `pipeline/route.py`.")

    @check("W7", "web", "Field navigation works when the phone refuses location")
    @web_or_skip
    def w8(w):
        deny = ("navigator.permissions && (navigator.permissions.query = () => Promise.resolve({state: 'denied'}));"
                "Object.defineProperty(navigator, 'geolocation', {value: {getCurrentPosition: (s, e) => e && e({code: 1, message: 'User denied Geolocation'}),"
                "watchPosition: (s, e) => { setTimeout(() => e && e({code: 1, message: 'User denied Geolocation'}), 50); return 1; }, clearWatch: () => {}}});")
        ctx, pg = w.page("phone", init=deny)
        w.open_app(pg, "traseu")
        pg.click('[data-act="nav-start"]')
        pg.wait_for_timeout(900)
        toast = pg.inner_text("#toast") if pg.is_visible("#toast") else ""
        nav = pg.inner_text("#nav") if pg.is_visible("#nav") else ""
        ctx.close()
        ok = "Accesul la locație este blocat" in toast and "următoarea țintă" in nav.lower()     # the label is shown in capitals
        return res("PASS" if ok else "FAIL", f"message: “{toast[:60]}…”; navigation panel {'shown' if nav else 'missing'}",
                   "clear message + target-by-target navigation", "Location refused (permission denied) on a phone: the app "
                   "explains how to allow it and keeps guiding target by target without the distance.")

    @check("W8", "web", "Load time and data downloaded to open the map (desktop, local server)")
    @web_or_skip
    def w9(w):
        ctx, pg = w.page()
        t = time.time()
        w.open_app(pg, "harta")
        dt = time.time() - t - 0.6
        mb = pg.evaluate("(performance.getEntriesByType('resource').reduce((s, r) => s + (r.encodedBodySize || 0), 0)"
                         " + (performance.getEntriesByType('navigation')[0]?.encodedBodySize || 0)) / 1e6")
        n = pg.evaluate("performance.getEntriesByType('resource').length")
        ctx.close()
        st = "PASS" if dt < 8 and mb < 45 else "WARN" if dt < 15 and mb < 70 else "FAIL"
        return res(st, f"map ready in {dt:.1f} s, {mb:.1f} MB in {n} files", "< 8 s, < 45 MB (WARN < 15 s, < 70 MB)",
                   "Everything the map needs to open (orthophoto mosaic, the village imagery in view, all annotation layers); "
                   "the high-resolution tiles and the rest of the village load only when zoomed in or panned to.")

    @check("W9", "web", "Buttons have a name and are big enough to tap on a phone")
    @web_or_skip
    def w10(w):
        js = """() => { const vis = e => { const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && !e.closest('[hidden]') && r.width > 0 && r.height > 0; };
          const bs = [...document.querySelectorAll('button, a.btn, [role=button]')].filter(vis);
          const noname = bs.filter(b => !(b.textContent.trim() || b.getAttribute('aria-label') || b.getAttribute('title'))).map(b => b.outerHTML.slice(0, 70));
          const small = bs.filter(b => { const r = b.getBoundingClientRect(); return Math.min(r.width, r.height) < 24; }).map(b => (b.textContent.trim() || b.getAttribute('aria-label') || '').slice(0, 24));
          return {n: bs.length, noname, small}; }"""
        found = {"n": 0, "noname": [], "small": []}
        for url in ("index.html", "login.html", "app.html#harta", "app.html#zona"):
            ctx, pg = w.page("phone")
            if url.startswith("app"):
                w.open_app(pg, url.split("#")[1])
            else:
                pg.goto(w.base + url, wait_until="load")
                pg.wait_for_timeout(600)
            r = pg.evaluate(js)
            found["n"] += r["n"]
            found["noname"] += [f"{url}: {x}" for x in r["noname"]]
            found["small"] += [f"{url}: {x}" for x in r["small"]]
            ctx.close()
        st = "PASS" if not found["noname"] and len(found["small"]) <= 3 else "WARN" if not found["noname"] else "FAIL"
        return res(st, f"{found['n']} visible buttons on a phone: {len(found['noname'])} without a name, {len(found['small'])} smaller than 24 px",
                   "0 without a name; ≤ 3 small", "Name = visible text, aria-label or title (screen readers, tooltips).",
                   (found["noname"] + found["small"])[:6])

    @check("W11", "web", "Entering the app: the whole of Moldova first, then a smooth flight to the flown area of Sireți")
    @web_or_skip
    def w11(w):
        fps = ("window.__f=[];(function f(t){if(window.__f.length<2000){window.__f.push(t);requestAnimationFrame(f);}})"
               "(performance.now());")
        ctx, pg = w.page(intro=True, init=fps)
        pg.goto(w.base + "app.html#inspector", wait_until="load")
        pg.wait_for_function("() => !document.querySelector('#loader')", timeout=30000)
        pg.wait_for_timeout(300)
        s0 = pg.inner_text("#sb-txt")                       # the scale bar: tens of km over Moldova
        pg.wait_for_timeout(7000)
        s1 = pg.inner_text("#sb-txt")                       # ... metres over the flown area
        f = pg.evaluate("window.__f")
        ctx.close()
        iv = sorted(b - a for a, b in zip(f, f[1:]) if b - a < 1000)
        import statistics
        med, p95 = (statistics.median(iv), iv[int(0.95 * (len(iv) - 1))]) if iv else (0, 0)
        km = lambda t: float(t.split()[0]) * (1000 if "km" in t else 1)
        ok = km(s0) >= 10000 and km(s1) <= 1000 and p95 <= 50
        return res("PASS" if ok else "FAIL", f"scale {s0} → {s1}; frames every {med:.0f} ms (p95 {p95:.0f} ms)",
                   "country → flown area; p95 frame ≤ 50 ms", "Once per session, after sign-in: the country and its "
                   "districts, the imagery of the flown area preloaded, then a 3 s flight drawn sharp at every frame. "
                   "Links to a zone, parcel or block open straight on it; reduced motion skips it; a touch stops it.")

    @check("W10", "web", "Every file the pages ask for exists (no 404, no failed request)")
    @web_or_skip
    def w2(w):
        n = len(w.requests)
        return res("PASS" if not w.failed else "FAIL", f"{n} requests, {len(w.failed)} failed", "0 failed",
                   "All requests of the other web checks. `/api/status` is the live-mode probe (answered only by "
                   "`python -m pipeline.serve`) and is expected to be missing on the static site.", w.failed[:6])
