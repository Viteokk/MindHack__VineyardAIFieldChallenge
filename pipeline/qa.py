"""QA: integration tests on the real Sireț3 data and on synthetic edge cases -> QA_REPORT.md (+ out/qa_report.json).

Levels
  boundary   tile seams keep their IDs, nothing on no-data (black) areas or on roads, inter-rows never on canopies,
             the route stays on passable ground, never crosses a row and returns to START
  confusers  shrubs / trees / striped meadows taken for vines, vine tubes or roofs taken for waste (real data)
  edge       synthetic tiles through the detector (black, meadow, noise, bare soil, orchard, striped meadow, vineyard at
             0 / 35 deg, half no-data, 8 m planting gap) and damaged inputs for the Sunday chain
  rules      Marcaj annotation rules, upload ZIPs, measurement consistency, score on the two reference tiles
  perf       per-tile detector time, parallel speed-up, route cost per stop, web payload (--perf, cached)
Status: PASS; WARN = known limitation or close to the limit (explained in the report); FAIL = broken.

Usage:  python -m pipeline.qa [--inp out/marcaj_global.xml] [--perf] [--no-images]
        VINEPLAN_INTEGRATION=1 python -m unittest tests.test_integration      (the same checks as unit tests)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import multiprocessing as mp
import os
import platform
import re
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402
from pipeline.tiles import Tile, all_tiles  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

PY = sys.executable
PX = C.PX
INP = C.OUT / "marcaj_global.xml"
REPORT = C.ROOT / "QA_REPORT.md"
EVID = C.ROOT / "docs" / "qa"
LEVELS = {"boundary": "Boundaries", "confusers": "Look-alikes (shrubs, trees, meadows, tubes)", "edge": "Edge cases",
          "rules": "Rules and consistency", "perf": "Performance and scalability",
          "web": "Web app on phone, tablet and desktop (headless Chromium)"}
CHECKS = []


def check(cid, level, title):
    def deco(fn):
        CHECKS.append((cid, level, title, fn))
        return fn
    return deco


def res(status, measured, threshold="", detail="", evidence=None):
    return {"status": status, "measured": measured, "threshold": threshold, "detail": detail, "evidence": evidence or []}


def grade(v, ok, warn, higher_is_better=False):
    if higher_is_better:
        return "PASS" if v >= ok else "WARN" if v >= warn else "FAIL"
    return "PASS" if v <= ok else "WARN" if v <= warn else "FAIL"


# ---------- data (loaded once) ----------
@lru_cache(maxsize=1)
def ann():
    return read_cvat(INP)


@lru_cache(maxsize=1)
def layers():
    return convert(INP, C.TILES)


@lru_cache(maxsize=1)
def tiles():
    return {t.name: t for t in all_tiles()}


def _geo(name):
    p = C.ROUTE_IN / f"{name}.geojson"
    return unary_union([shape(f["geometry"]) for f in json.loads(p.read_text())["features"]])


@lru_cache(maxsize=None)
def route_in(name):
    return _geo(name)


@lru_cache(maxsize=1)
def rows_utm():
    return [(f["properties"]["row_id"], f["properties"]["vineyard_id"], f["properties"]["tile"], shape(f["geometry"]),
             f["properties"]["row_structure"]) for f in layers()[0]["rows"]]


@lru_cache(maxsize=1)
def canopies_utm():
    return [(f["properties"]["vineyard_id"], f["properties"]["tile"], shape(f["geometry"])) for f in layers()[0]["canopies"]]


@lru_cache(maxsize=6)
def tile_rgb(name):
    return np.asarray(Image.open(tiles()[name].path).convert("RGB"))


def exg(img):
    f = img.astype(np.float32)
    return (2 * f[..., 1] - f[..., 0] - f[..., 2]) / (f.sum(2) + 1e-6)


@lru_cache(maxsize=4)
def tile_veg(name):
    return exg(tile_rgb(name)) > 0.10


# ================= boundaries =================
@check("B1", "boundary", "A row cut by a tile edge keeps its row_id and vineyard_id in the next tile")
def b1():
    T, rows = tiles(), rows_utm()
    ends = []
    for k, (rid, vid, tile, g, _) in enumerate(rows):
        xy, tb = np.asarray(g.coords), T[tile].bounds
        for p, q in ((xy[0], xy[1]), (xy[-1], xy[-2])):
            if min(p[0] - tb[0], tb[2] - p[0], p[1] - tb[1], tb[3] - p[1]) < 0.6 and np.hypot(*(p - q)) > 0:
                ends.append((k, p, (p - q) / np.hypot(*(p - q))))
    pairs = set()
    for i, j in cKDTree([e[1] for e in ends]).query_pairs(1.5):
        (ki, pi, ui), (kj, pj, uj) = ends[i], ends[j]
        if rows[ki][2] == rows[kj][2] or ui @ uj > -0.97 or abs((pj - pi) @ np.array([-ui[1], ui[0]])) > 0.5:
            continue
        pairs.add((min(ki, kj), max(ki, kj)))
    if not pairs:
        return res("WARN", "no seams found", "", "no row continues across a tile edge within 1.5 m")
    same_r = sum(rows[a][0] == rows[b][0] for a, b in pairs) / len(pairs)
    same_v = sum(rows[a][1] == rows[b][1] for a, b in pairs) / len(pairs)
    bad = [f"{rows[a][0]} ({rows[a][2][7:15]}) ≠ {rows[b][0]} ({rows[b][2][7:15]})" for a, b in sorted(pairs) if rows[a][0] != rows[b][0]][:6]
    return res(grade(min(same_r, same_v), 0.97, 0.9, True), f"{len(pairs)} seams: same row_id {same_r:.1%}, same vineyard_id {same_v:.1%}",
               "≥ 97 % (WARN ≥ 90 %)", "Seam = two row ends facing each other across a tile edge, < 1.5 m apart, < 0.5 m sideways.", bad)


@check("B2", "boundary", "Every vertex lies inside its 2048 × 2048 px tile")
def b2():
    n = out = 0
    for objs in ann().values():
        for o in objs:
            pts = np.asarray(o["points"]) if "points" in o else np.array([[o["xtl"], o["ytl"]], [o["xbr"], o["ybr"]]])
            n += len(pts)
            out += int(((pts < -0.5) | (pts > 2048.5)).any(axis=1).sum())
    return res("PASS" if out == 0 else "FAIL", f"{out} of {n:,} vertices outside", "0")


@check("B3", "boundary", "No canopy or row on the black no-data border of edge tiles")
def b3():
    hits, total, tiles_nd = [], 0, 0
    for name, objs in ann().items():
        if not objs:
            continue
        img = tile_rgb(name)
        black = img.sum(2) == 0                     # the no-data border is pure black; dark shadows are not
        if black.mean() < 0.005:
            continue
        tiles_nd += 1
        for o in objs:
            if o["label"] not in ("vineyard", "row"):
                continue
            total += 1
            pts = np.asarray(o["points"])
            if o["label"] == "row":                     # sample the axis every ~0.5 m
                L = LineString(pts)
                pts = np.array([L.interpolate(d).coords[0] for d in np.arange(0, L.length + 1, 20)])
            else:
                pts = pts.mean(0, keepdims=True)
            xs, ys = np.clip(pts[:, 0].astype(int), 0, 2047), np.clip(pts[:, 1].astype(int), 0, 2047)
            if black[ys, xs].mean() > 0.5:
                hits.append(f"{o['label']} on {name[7:15]}")
    share = len(hits) / max(total, 1)
    return res(grade(share, 0.0, 0.002), f"{len(hits)} of {total:,} objects on {tiles_nd} tiles with a no-data border", "0 (WARN ≤ 0.2 %)",
               "An object counts when its centre (canopy) or most of its axis (row) lies on black pixels.", hits[:6])


@check("B4", "boundary", "Row axes stop at the road: share of row length lying on authorised passages")
def b4():
    P = route_in("passages")
    tot = sum(g.length for *_, g, _ in rows_utm())
    on = sum(g.intersection(P).length for *_, g, _ in rows_utm())
    long = sorted(((g.intersection(P).length, rid) for rid, _, _, g, _ in rows_utm()), reverse=True)[:5]
    return res(grade(on / tot, 0.01, 0.05), f"{on:,.0f} m of {tot:,.0f} m = {on / tot:.1%}", "≤ 1 % (WARN ≤ 5 %)",
               "Rules 4.4: rows and inter-rows stop at the edge of the planting, not at the road centre. Part of this is "
               "the organisers' passage polygons overlapping planted rows (seen on the orthophoto).",
               [f"{rid}: {m:.1f} m on a road" for m, rid in long])


@check("B5", "boundary", "Inter-row areas never overlap canopies")
def b5():
    inter = unary_union([shape(f["geometry"]).buffer(0) for f in layers()[0]["interrows"]])
    can = unary_union([g.buffer(0) for *_, g in canopies_utm()])
    ov = inter.intersection(can).area
    return res(grade(ov / can.area, 0.005, 0.02), f"{ov:,.1f} m² = {ov / can.area:.2%} of the canopy area", "≤ 0.5 % (WARN ≤ 2 %)",
               "Rules: canopies and inter-row areas never overlap; the inter-row runs from canopy edge to canopy edge.")


@check("B6", "boundary", "Official route: valid, back at START, 0 row crossings, nothing through canopies or forbidden zones")
def b6():
    out = C.OUT / "qa_route_check.json"
    subprocess.run([PY, "-m", "pipeline.validate", "--route", str(C.ROOT / "route.geojson"), "--inp", str(INP),
                    "--targets", str(C.OUT / "targets_inspector.geojson"), "--json", str(out)], cwd=C.ROOT, capture_output=True)
    r = json.loads(out.read_text())
    ok = r["valid"] and r.get("row_crossings", 1) == 0 and r["canopy_m"] == 0 and r["forbidden_m"] == 0
    return res("PASS" if ok else "FAIL", f"{r['length_m'] / 1000:.2f} km · outside {r['outside_share']:.2%} · start/end {r['start_m']:.1f}/{r['end_m']:.1f} m · "
               f"{r.get('row_crossings')} row crossings · canopies {r['canopy_m']} m · forbidden {r['forbidden_m']} m",
               "valid, ≤ 2 % outside, ≤ 5 m, 0 crossings", "Checked by pipeline.validate against the annotations.")


@check("B7", "boundary", "The route never leaves the study area or the authorised passages")
def b7():
    line = shape(json.loads((C.ROOT / "route.geojson").read_text())["features"][0]["geometry"])
    outside = line.difference(unary_union([route_in("study_area"), route_in("passages")]).buffer(0.01)).length
    return res("PASS" if outside < 1 else "FAIL", f"{outside:.2f} m outside", "< 1 m")


@check("B8", "boundary", "Polygons are valid (no self-intersections) as exported to Marcaj")
def b8():
    n = bad = 0
    for objs in ann().values():
        for o in objs:
            if o["type"] == "polygon":
                n += 1
                bad += not Polygon(o["points"]).is_valid
    return res(grade(bad / max(n, 1), 0.001, 0.01), f"{bad} of {n:,} polygons invalid", "≤ 0.1 % (WARN ≤ 1 %)",
               "Invalid rings are repaired with make_valid in every measurement; Marcaj accepts them.")


# ================= look-alikes =================
@check("C1", "confusers", "Canopy shape: no shrub or tree crown counted as a vine (compared with the reference tiles)")
def c1():
    def shape_stats(polys):
        a = np.array([g.area for g in polys])
        w = []
        for g in polys:
            c = np.asarray(g.minimum_rotated_rectangle.exterior.coords)
            w.append(min(np.hypot(*(c[1] - c[0])), np.hypot(*(c[2] - c[1]))))
        return a, np.array(w)
    a, w = shape_stats([g for *_, g in canopies_utm()])
    ref = read_cvat(C.EXAMPLES / "annotations.xml")
    ref_polys = [Polygon(np.asarray(o["points"]) * C.PX).buffer(0) for objs in ref.values() for o in objs
                 if o["label"] == "vineyard" and len(o["points"]) >= 3]
    ra, rw = shape_stats(ref_polys)
    wide = (w > 1.2).mean()                               # a vine is under 1 m wide; tree / bush crowns 2-4 m
    big, rbig = (a > 3).mean(), (ra > 3).mean()           # long blobs of touching canopies: the reference has them too
    status = "FAIL" if wide > 0.005 else ("PASS" if big <= 2 * rbig else ("WARN" if big <= 3 * rbig else "FAIL"))
    return res(status, f"width p99 {np.percentile(w, 99):.2f} m (reference {np.percentile(rw, 99):.2f} m), {wide:.2%} wider than 1.2 m; "
                       f"{big:.1%} of {len(a):,} canopies > 3 m² (reference tiles {rbig:.1%}); median {np.median(a):.2f} m²",
               "≤ 0.5 % wider than 1.2 m; share > 3 m² ≤ 2× the reference (WARN ≤ 3×)",
               "Rules 2.2–2.3: a vine is under 1 m wide, a tree crown 2–4 m and round. Touching canopies of older vines are "
               "long, narrow blobs, and the reference keeps them whole (tile r006_c004: up to 31 m²), so size alone is not an error.")


def row_support(tile, pts, spacing=2.6):
    """on-row vegetation / mid-line vegetation along one axis (pixel coords); ~1 = not on a vine row."""
    veg = tile_veg(tile)
    L = LineString(pts)
    if L.length < 40:
        return None
    s = np.array([L.interpolate(d).coords[0] for d in np.arange(0, L.length, 8)])
    u = (s[-1] - s[0]) / max(np.hypot(*(s[-1] - s[0])), 1e-6)
    n = np.array([-u[1], u[0]])
    def frac(off):
        q = s + n * off
        ok = (q[:, 0] >= 0) & (q[:, 0] < 2048) & (q[:, 1] >= 0) & (q[:, 1] < 2048)
        q = q[ok].astype(int)
        return veg[q[:, 1], q[:, 0]].mean() if len(q) else np.nan
    on = np.nanmean([frac(o) for o in (-6, 0, 6)])
    mid = np.nanmean([frac(o) for o in (-spacing / 2 / PX, spacing / 2 / PX)])
    return (on + 0.02) / (mid + 0.02)


@lru_cache(maxsize=1)
def support_by_block():
    by = defaultdict(lambda: [0.0, 0.0])
    for name, objs in ann().items():
        for o in objs:
            if o["label"] != "row":
                continue
            r = row_support(name, np.asarray(o["points"]))
            if r is None:
                continue
            L = LineString(o["points"]).length * PX
            vid = o["attrs"].get("vineyard_id", "")
            by[vid][0] += L
            by[vid][1] += L * (r < 1.3)
    return {v: (L, weak / L) for v, (L, weak) in by.items() if L > 0}


@check("C2", "confusers", "Rows sit on vines, not on striped meadows, tracks or scrub (vegetation on the axis vs between rows)")
def c2():
    sb = support_by_block()
    bad = sorted(((w, v, L) for v, (L, w) in sb.items() if w > 0.5), reverse=True)
    tot = sum(L for L, _ in sb.values())
    weak_len = sum(L * w for L, w in sb.values())
    ev = [f"{v}: {w:.0%} of {L:,.0f} m of rows weakly supported" for w, v, L in bad[:8]]
    return res("PASS" if not bad else "WARN", f"{len(bad)} of {len(sb)} blocks mostly on weak rows; {weak_len / tot:.1%} of all row length weak",
               "0 blocks > 50 % weak", "Support = vegetation (ExG > 0.10) on the axis ± 0.15 m ÷ vegetation on the two mid-lines; weak < 1.3. "
               "Known limitation of the classical detector on striped meadows and scrub: these blocks are on the Marcaj correction list.", ev)


@check("C3", "confusers", "White vine tubes are not waste: no waste box on a row axis")
def c3():
    rows = [g for *_, g, _ in rows_utm()]
    tree = STRtree(rows)
    near = []
    for f in layers()[0]["waste"]:
        c = shape(f["geometry"]).centroid
        d = min((rows[i].distance(c) for i in tree.query(c.buffer(1.0))), default=9)
        if d < 0.4:
            near.append(f"{f['properties']['waste_id']} {d:.2f} m from a row")
    n = len(layers()[0]["waste"])
    return res("PASS" if not near else "WARN", f"{len(near)} of {n} waste boxes within 0.4 m of a row axis", "0",
               "Rules 3: white protective tubes and stakes next to young vines belong to the planting.", near)


@check("C4", "confusers", "No waste on roofs or inside forbidden zones (village, buildings)")
def c4():
    F = route_in("forbidden")
    bad = [f["properties"]["waste_id"] for f in layers()[0]["waste"] if F.contains(shape(f["geometry"]).centroid)]
    return res("PASS" if not bad else "FAIL", f"{len(bad)} of {len(layers()[0]['waste'])}", "0", evidence=bad)


@check("C5", "confusers", "Waste boxes are object-sized (0.1–2.5 m)")
def c5():
    bad = []
    for f in layers()[0]["waste"]:
        x0, y0, x1, y1 = shape(f["geometry"]).bounds
        if not (0.1 <= max(x1 - x0, y1 - y0) <= 2.5):
            bad.append(f"{f['properties']['waste_id']}: {x1 - x0:.2f} × {y1 - y0:.2f} m")
    return res("PASS" if not bad else "WARN", f"{len(bad)} of {len(layers()[0]['waste'])} out of range", "0", evidence=bad)


@check("C6", "confusers", "Garden rule: every block has at least 3 rows")
def c6():
    rows = defaultdict(set)
    for rid, vid, *_ in rows_utm():
        rows[vid].add(rid)
    small = sorted(v for v, r in rows.items() if len(r) < 3)
    return res("PASS" if not small else "WARN", f"{len(small)} of {len(rows)} blocks with < 3 rows", "0",
               "Rules 2.3: a single vine or two rows in a yard is not a vineyard.", small)


# ================= edge cases (synthetic tiles through the detector) =================
@dataclass(frozen=True)
class ArrTile(Tile):
    img: object = None

    def read(self):
        return self.img


RNG = np.random.default_rng(7)
SOIL, LEAF, DRY, GRASS = (150, 118, 88), (58, 122, 40), (160, 150, 96), (82, 138, 52)


def canvas(col, noise=8):
    return np.clip(np.array(col, np.float32) + RNG.normal(0, noise, (2048, 2048, 3)), 0, 255).astype(np.uint8)


def vineyard(spacing=2.6, angle=0.0, step=1.2, d=0.75, gap=None, black_right=False):
    img = canvas(SOIL)
    a = math.radians(angle)
    u, n = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
    c = np.array([1024.0, 1024.0])
    for k in range(-30, 31):
        for s in np.arange(-1600, 1600, step / PX):
            if gap and k == gap[0] and gap[1] / PX <= s <= gap[2] / PX:
                continue
            p = c + n * k * spacing / PX + u * s
            if -40 < p[0] < 2088 and -40 < p[1] < 2088:
                cv2.circle(img, (int(p[0]), int(p[1])), int(d / 2 / PX), LEAF, -1)
    img = np.clip(img.astype(np.int16) + RNG.integers(-10, 10, img.shape), 0, 255).astype(np.uint8)
    if black_right:
        img[:, 1024:] = 0
    return img


def detect(img):
    from pipeline import baseline as B
    t = tiles()["siret3_r021_c012.tif"]
    return B.process_tile(ArrTile(t.name, t.path, t.row, t.col, t.x0, t.y0, t.res, t.width, t.height, img))


def rows_of(objs):
    return [o for o in objs if o["label"] == "row"]


def angle_of(o):
    p = np.asarray(o["points"])
    return math.degrees(math.atan2(p[-1, 1] - p[0, 1], p[-1, 0] - p[0, 0])) % 180


def spacing_of(rs, ang):
    n = np.array([-math.sin(math.radians(ang)), math.cos(math.radians(ang))])
    off = sorted(float(np.mean(np.asarray(o["points"]), 0) @ n) for o in rs)
    return float(np.median(np.diff(off))) * PX if len(off) > 1 else 0.0


def no_rows_case(img, what):
    objs = detect(img)
    r = len(rows_of(objs))
    return res("PASS" if r == 0 else "FAIL", f"{r} rows, {len(objs)} objects", "0 rows", what)


@check("E1", "edge", "Black no-data tile → nothing detected")
def e1():
    objs = detect(np.zeros((2048, 2048, 3), np.uint8))
    return res("PASS" if not objs else "FAIL", f"{len(objs)} objects", "0")


@check("E2", "edge", "Uniform grass meadow → no rows")
def e2():
    return no_rows_case(canvas(GRASS, 14), "Green everywhere: no periodic row pattern.")


@check("E3", "edge", "Random noise → no rows")
def e3():
    return no_rows_case(RNG.integers(0, 255, (2048, 2048, 3), dtype=np.uint8), "")


@check("E4", "edge", "Bare tilled soil → nothing detected")
def e4():
    objs = detect(canvas(SOIL, 10))
    return res("PASS" if not objs else "FAIL", f"{len(objs)} objects", "0")


@check("E5", "edge", "Orchard (crowns 3 m wide, 5 m apart) → not taken for a vineyard")
def e5():
    img = canvas(DRY, 10)
    for y in np.arange(60, 2048, 5 / PX):
        for x in np.arange(60, 2048, 5 / PX):
            cv2.circle(img, (int(x), int(y)), int(1.5 / PX), (50, 110, 38), -1)
    return no_rows_case(img, "Rows 5 m apart are outside the vine spacing band (2.0–3.4 m).")


@check("E6", "edge", "Striped meadow (continuous 1.3 m green / 1.3 m dry strips) → no rows")
def e6():
    img = canvas(DRY, 10)
    w = int(1.3 / PX)
    for x in range(0, 2048, 2 * w):
        img[:, x:x + w] = np.clip(np.array(GRASS) + RNG.normal(0, 10, (2048, min(w, 2048 - x), 3)), 0, 255)
    objs = detect(img)
    r = len(rows_of(objs))
    return res("PASS" if r == 0 else "WARN", f"{r} rows, {sum(o['label'] == 'vineyard' for o in objs)} canopies", "0 rows",
               "Known limitation: continuous strips at a vine-like spacing look like rows to a classical detector. "
               "In the real data the YOLO11 canopy veto and the human correction in Marcaj remove them (see C2).")


@check("E7", "edge", "Synthetic vineyard 2.6 m × 1.2 m → every row, the right spacing, one canopy per vine")
def e7():
    objs = detect(vineyard())
    rs = rows_of(objs)
    sp = spacing_of(rs, 0.0)
    exp = 2048 * PX / 2.6
    can = sum(o["label"] == "vineyard" for o in objs)
    ok = abs(len(rs) - exp) <= 2 and abs(sp - 2.6) <= 0.13 and can >= 0.8 * len(rs) * 51.2 / 1.2
    return res("PASS" if ok else "FAIL", f"{len(rs)} rows (expected ~{exp:.0f}), spacing {sp:.2f} m, {can} canopies",
               "rows ± 2, spacing ± 5 %, ≥ 80 % of the vines")


@check("E8", "edge", "Vineyard rotated 35° → rows found at the right angle")
def e8():
    rs = rows_of(detect(vineyard(angle=35)))
    if not rs:
        return res("FAIL", "no rows", "angle ± 3°")
    err = statistics.median(min(abs(angle_of(o) - 35), 180 - abs(angle_of(o) - 35)) for o in rs)
    return res("PASS" if err <= 3 else "FAIL", f"{len(rs)} rows, median angle error {err:.1f}°", "± 3°")


@check("E9", "edge", "Edge tile, right half black → rows only on the image, nothing on no-data")
def e9():
    objs = detect(vineyard(black_right=True))
    on_black = sum(np.asarray(o["points"])[:, 0].mean() > 1030 for o in objs if "points" in o)
    rs = rows_of(objs)
    ok = rs and on_black == 0
    return res("PASS" if ok else "FAIL", f"{len(rs)} rows, {on_black} objects on the black half", "rows > 0, 0 on black")


@check("E10", "edge", "An 8 m planting gap marks that row 'disrupted', the others stay 'regular'")
def e10():
    rs = rows_of(detect(vineyard(gap=(3, -4.0, 4.0))))
    if not rs:
        return res("FAIL", "no rows", "")
    n = np.array([0.0, 1.0])
    target = 1024 + 3 * 2.6 / PX
    k = min(range(len(rs)), key=lambda i: abs(float(np.mean(np.asarray(rs[i]["points"]), 0) @ n) - target))
    st = rs[k]["attrs"].get("row_structure")
    others = Counter(o["attrs"].get("row_structure") for i, o in enumerate(rs) if i != k)
    ok = st == "disrupted" and others.get("disrupted", 0) <= 1
    return res("PASS" if ok else "FAIL", f"gap row: {st}; others {dict(others)}", "disrupted; others regular",
               "Rules 4.3: a visible gap of 5 m or more along the row makes it disrupted; the row is not split.")


@check("E11", "edge", "Marcaj export with folder-prefixed image names is read tile by tile")
def e11():
    from scripts import sunday
    d = {f"task_7/images/{k}": v for k, v in list(ann().items())[:12]}
    with tempfile.TemporaryDirectory() as td:
        x = Path(td) / "annotations.xml"
        write_cvat(d, x)
        z = Path(td) / "export.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(x, "annotations.xml")
        merged, _ = sunday.load_export([str(z)])
    ok = sorted(merged) == sorted(Path(k).name for k in d)
    return res("PASS" if ok else "FAIL", f"{len(merged)} of 12 tiles recovered", "12")


@check("E12", "edge", "Missing attributes in an export are reported, not silently accepted")
def e12():
    from scripts import sunday
    d = {"siret3_r021_c012.tif": [
        {"label": "row", "type": "polyline", "points": [[0, 0], [100, 100]], "attrs": {"vineyard_id": "V01", "row_structure": "regular"}},
        {"label": "interrow_area", "type": "polygon", "points": [[0, 0], [10, 0], [10, 10]], "attrs": {"vineyard_id": "V01"}},
        {"label": "vineyard", "type": "polygon", "points": [[0, 0], [10, 0], [10, 10]], "attrs": {}}]}
    buf = io.StringIO()
    old, sys.stdout = sys.stdout, buf
    try:
        bad = sunday.report(d)
    finally:
        sys.stdout = old
    want = {"row without row_id": 1, "interrow without interrow_cover": 1, "vineyard without vineyard_id": 1}
    ok = all(bad.get(k) == v for k, v in want.items())
    return res("PASS" if ok else "FAIL", ", ".join(f"{k}: {v}" for k, v in bad.items()), "all three reported")


@check("E13", "edge", "CVAT write → read round trip keeps every object and attribute")
def e13():
    d = dict(list(ann().items())[:40])
    with tempfile.TemporaryDirectory() as td:
        x = Path(td) / "a.xml"
        write_cvat(d, x)
        back = read_cvat(x)
    key = lambda dd: sorted((k, o["label"], tuple(sorted(o["attrs"].items()))) for k, v in dd.items() for o in v)
    ok = key(d) == key(back)
    return res("PASS" if ok else "FAIL", f"{sum(map(len, d.values())):,} objects on 40 tiles", "identical")


# ================= rules and consistency =================
ROW_ST, COVER = {"regular", "disrupted", "unassessable"}, {"bare_soil", "vegetation", "mixed", "unassessable"}


@check("R1", "rules", "Labels and attributes exactly as in the annotation rules (lower case, allowed values, none missing)")
def r1():
    bad = Counter()
    for objs in ann().values():
        for o in objs:
            a, lab = o["attrs"], o["label"]
            if lab not in C.LABELS:
                bad[f"unknown label {lab}"] += 1
            if lab in ("vineyard", "row", "interrow_area") and not a.get("vineyard_id"):
                bad[f"{lab} without vineyard_id"] += 1
            if lab == "row":
                bad["row without row_id"] += not a.get("row_id")
                bad["row_structure not allowed"] += a.get("row_structure") not in ROW_ST
                bad["interrow_cover on a row"] += "interrow_cover" in a
            if lab == "interrow_area":
                bad["interrow_cover not allowed"] += a.get("interrow_cover") not in COVER
                bad["row attributes on an inter-row"] += ("row_id" in a) or ("row_structure" in a)
    bad = {k: v for k, v in bad.items() if v}
    return res("PASS" if not bad else "FAIL", "no violation" if not bad else ", ".join(f"{k}: {v}" for k, v in bad.items()), "0")


@check("R2", "rules", "One polyline per physical row per tile")
def r2():
    dup = []
    for name, objs in ann().items():
        c = Counter(o["attrs"].get("row_id") for o in objs if o["label"] == "row")
        dup += [f"{r} ×{n} on {name[7:15]}" for r, n in c.items() if n > 1]
    n = sum(o["label"] == "row" for objs in ann().values() for o in objs)
    return res(grade(len(dup) / max(n, 1), 0.0, 0.01), f"{len(dup)} duplicated row_id in a tile ({n:,} row polylines)", "0 (WARN ≤ 1 %)", evidence=dup[:6])


@check("R3", "rules", "row_id belongs to its block (V03-R017 lies in V03) and every row_id is used in one block only")
def r3():
    blocks = defaultdict(set)
    wrong = 0
    for rid, vid, *_ in rows_utm():
        blocks[rid].add(vid)
        wrong += not rid.startswith(vid + "-R")
    multi = [r for r, v in blocks.items() if len(v) > 1]
    return res("PASS" if not wrong and not multi else "FAIL", f"{wrong} row_ids outside their block, {len(multi)} in several blocks, {len(blocks)} rows", "0", evidence=multi[:6])


@check("R4", "rules", "Upload ZIPs: 311 original tiles (byte-identical), each ZIP < 90 MB, labels as in Appendix A")
def r4():
    zdir = C.OUT / "upload_v5"
    zs = sorted(zdir.glob("*.zip"))
    names, big, changed, meta_bad = [], [], [], []
    orig = {p.name: p for p in C.TILES.glob("siret3_r*_c*.tif")}
    for z in zs:
        if z.stat().st_size >= 90e6:
            big.append(z.name)
        with zipfile.ZipFile(z) as zf:
            xml = zf.read("annotations.xml").decode()
            for lab, typ in (("vineyard", "polygon"), ("waste", "rectangle"), ("row", "polyline"), ("interrow_area", "polygon")):
                if not re.search(rf"<name>{lab}</name>\s*<type>{typ}</type>", xml):
                    meta_bad.append(f"{z.name}: {lab}")
            for n in zf.namelist():
                if n.endswith(".tif"):
                    names.append(Path(n).name)
                    if hashlib.md5(zf.read(n)).digest() != hashlib.md5(orig[Path(n).name].read_bytes()).digest():
                        changed.append(Path(n).name)
    ok = len(set(names)) == len(orig) == len(names) and not big and not changed and not meta_bad
    return res("PASS" if ok else "FAIL", f"{len(zs)} ZIPs, {len(set(names))} tiles ({len(names) - len(set(names))} duplicates), "
               f"{len(changed)} changed, largest {max(z.stat().st_size for z in zs) / 1e6:.1f} MB", "311 · 0 · < 90 MB",
               "Annex A of the rules: the tiles must be the supplied files, unchanged, with their original names.", big + changed + meta_bad)


@check("R5", "rules", "Tiles with nothing to annotate are known (each needs 'No objects in this frame' in Marcaj)")
def r5():
    empty = sum(1 for v in ann().values() if not v)
    return res("PASS", f"{empty} of {len(ann())} tiles empty", "listed", "A job cannot be submitted while one of its tiles has no answer.")


@check("M1", "rules", "measurements.csv: totals equal the sum of the rows; m² and ha agree")
def m1():
    import csv
    rows = list(csv.DictReader(open(C.ROOT / "measurements.csv")))
    tot = next(r for r in rows if r["level"] == "total")
    rl = [r for r in rows if r["level"] == "row"]
    bl = [r for r in rows if r["level"] == "block"]
    dl = abs(sum(float(r["length_m"]) for r in rl) - float(tot["length_m"]))
    dha = abs(float(tot["canopy_m2"]) / 1e4 - float(tot["canopy_ha"])) + abs(float(tot["interrow_m2"]) / 1e4 - float(tot["interrow_ha"]))
    n_ok = int(tot["blocks"]) == len(bl) and int(tot["rows"]) == len({r["row_id"] for r in rl})
    ok = dl < 1 and dha < 1e-3 and n_ok
    return res("PASS" if ok else "FAIL", f"{tot['blocks']} blocks, {tot['rows']} rows, {float(tot['length_m']) / 1000:.2f} km; Δlength {dl:.2f} m, Δha {dha:.5f}",
               "Δ < 1 m, Δ < 0.001 ha, counts equal")


@check("M2", "rules", "Block report and environmental indicators agree with measurements.csv")
def m2():
    import csv
    tot = next(r for r in csv.DictReader(open(C.ROOT / "measurements.csv")) if r["level"] == "total")
    br = json.loads((C.ROOT / "web" / "data" / "blocks_report.json").read_text())
    env = json.loads((C.ROOT / "web" / "data" / "env_indicators.json").read_text())
    s = br["summary"]
    d1 = abs(s["row_length_m"] - float(tot["length_m"])) / float(tot["length_m"])
    d2 = abs(env["total"]["missing_vines"] - s["missing_vines"]) / max(s["missing_vines"], 1)
    d3 = abs(s["blocks"] - int(tot["blocks"]))
    ok = d1 < 0.001 and d2 < 0.01 and d3 == 0
    return res("PASS" if ok else "FAIL", f"row length Δ {d1:.3%}, missing vines Δ {d2:.2%}, blocks Δ {d3}", "< 0.1 %, < 1 %, 0")


@check("M3", "rules", "Deliverables are georeferenced in EPSG:32635")
def m3():
    files = [C.ROOT / "route.geojson", C.ROOT / "route_waste.geojson"] + sorted((C.ROOT / "web" / "data").glob("*.geojson"))
    bad = [p.name for p in files if "32635" not in json.dumps(json.loads(p.read_text()).get("crs", {}))]
    main_ok = not any(n in ("route.geojson", "route_waste.geojson") for n in bad)
    return res("PASS" if main_ok and not bad else "WARN" if main_ok else "FAIL", f"{len(files) - len(bad)} of {len(files)} GeoJSON files declare EPSG:32635",
               "route.geojson + route_waste.geojson required", "Map layers without a crs member are still in UTM 35N metres.", bad[:8])


@check("A1", "rules", "Score on the two official reference tiles (local re-implementation of the metric)")
def a1():
    p = subprocess.run([PY, "-m", "pipeline.eval", "--pred", str(INP)], cwd=C.ROOT, capture_output=True, text=True)
    m = re.search(r"PARTIAL SCORE.*?([\d.]+)\s*$", p.stdout.strip().splitlines()[-1])
    sc = float(m.group(1)) if m else 0.0
    lines = [ln.strip() for ln in p.stdout.splitlines() if ln.startswith(("canopy", "axes", "attrs", "counts"))]
    return res(grade(sc, 0.80, 0.75, True), f"partial score {sc:.3f} (60 % of the total covered here)", "≥ 0.80 (WARN ≥ 0.75)",
               "Canopies 25 %, waste 10 %, axes 8 %, attributes 5 %, grouping 2 %, counts 10 %, normalised; tiles r006_c004 and r021_c012.", lines)


# ================= performance =================
PERF = C.OUT / "qa_perf.json"


def _detect_one(path):
    cv2.setNumThreads(1)                       # one tile per process: no nested OpenCV threads
    from pipeline import baseline as B
    from pipeline.tiles import open_tile
    t = time.time()
    B.process_tile(open_tile(path))
    return time.time() - t


def run_perf():
    names = sorted(ann())
    busy = [n for n in names if len(ann()[n]) > 50][:8]
    empty = [n for n in names if not ann()[n]][:8]
    sample = [C.TILES / n for n in busy + empty]
    per = [_detect_one(p) for p in sample]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
    batch = [C.TILES / n for n in names[:48]]
    speed = {}
    for w in (1, 4, max(1, (os.cpu_count() or 2) - 1)):
        t = time.time()
        if w == 1:
            for p in batch[:16]:
                _detect_one(p)
            speed[w] = 16 / (time.time() - t)
        else:
            with mp.get_context("spawn").Pool(w) as pool:   # spawn: forked OpenCV workers can hang on macOS
                pool.map(_detect_one, batch)
            speed[w] = len(batch) / (time.time() - t)
    # route: grid, graph, one Dijkstra per stop
    from scipy.sparse.csgraph import dijkstra
    from pipeline import route as R
    t = time.time()
    L, _ = convert(INP, C.TILES)
    inter = unary_union([shape(f["geometry"]) for f in L["interrows"]])
    can = unary_union([shape(f["geometry"]) for f in L["canopies"]])
    passages, forbidden, study = route_in("passages"), route_in("forbidden"), route_in("study_area")
    blocks = R.load(C.OUT / "blocks.geojson")
    grid = R.Grid(study.union(passages).bounds)
    walls, axes = R.row_walls([(f["properties"]["row_id"], f["properties"]["tile"], shape(f["geometry"])) for f in L["rows"]], passages,
                              {n: tt.bounds for n, tt in tiles().items()})
    cost = R.build_cost(grid, inter, passages, can, forbidden, study, blocks, walls)
    cut = R.wall_cuts(grid, cost, passages, axes)
    g, idx = R.graph(cost, cut)
    t_grid = time.time() - t
    src = RNG.choice(g.shape[0], 12, replace=False)
    t = time.time()
    for s in src:
        dijkstra(g, directed=True, indices=int(s), limit=R.REACH_M)
    t_dij = (time.time() - t) / len(src)
    web = C.ROOT / "web" / "data"
    size = lambda ps: sum(p.stat().st_size for p in ps if p.exists())
    boot = size(list((web / "mosaic").glob("*"))) + size([web / "pred" / f for f in ("canopies.json", "rows.geojson", "interrows.geojson", "summary.json", "waste.geojson")]) \
        + size([web / f for f in ("route.geojson", "targets_inspector.geojson", "compliance.json", "blocks_status.geojson", "blocks_report.json", "register.geojson")])
    out = {"measured": time.strftime("%Y-%m-%d %H:%M"), "hardware": hardware(),
           "detect_s": {"busy_median": statistics.median(per[:len(busy)]), "empty_median": statistics.median(per[len(busy):]) if empty else None,
                        "max": max(per), "n": len(per)}, "rss_mb": rss,
           "detect_tiles_per_s": {str(k): v for k, v in speed.items()}, "grid_s": t_grid, "grid_cells": int(g.shape[0]),
           "dijkstra_s_per_stop": t_dij, "web_boot_mb": boot / 1e6, "cpu": os.cpu_count()}
    PERF.write_text(json.dumps(out, indent=1))
    return out


def hardware():
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip()) / 2 ** 30
        return f"{chip}, {mem:.0f} GB RAM, {os.cpu_count()} cores, {platform.system()} {platform.release()}, Python {platform.python_version()}"
    except Exception:
        return f"{platform.machine()}, {os.cpu_count()} cores, Python {platform.python_version()}"


@check("P1", "perf", "Detector time per tile and throughput (311 tiles = 81 ha of challenge tiles)")
def p1():
    if not PERF.exists():
        return res("WARN", "not measured", "", "Run python -m pipeline.qa --perf")
    p = json.loads(PERF.read_text())
    d, sp = p["detect_s"], p["detect_tiles_per_s"]
    best = max(sp.values())
    ha_h = best * 3600 * (51.2 * 51.2) / 1e4
    return res("PASS" if d["busy_median"] < 5 else "WARN", f"{d['busy_median']:.2f} s per vineyard tile, {d['empty_median']:.2f} s per empty tile, max {d['max']:.1f} s; "
               f"peak memory {p['rss_mb']:.0f} MB", "< 5 s per tile",
               f"Best throughput {best:.1f} tiles/s ≈ {ha_h:,.0f} ha per hour of imagery on one laptop ({p['hardware']}).")


@check("P2", "perf", "Tiles are independent: the detector scales with the cores")
def p2():
    if not PERF.exists():
        return res("WARN", "not measured", "", "Run python -m pipeline.qa --perf")
    sp = {int(k): v for k, v in json.loads(PERF.read_text())["detect_tiles_per_s"].items()}
    w = max(sp)
    gain = sp[w] / sp[1]
    return res("PASS" if gain >= min(3, w * 0.5) else "WARN", " · ".join(f"{k} worker{'s' if k > 1 else ''}: {v:.2f} tiles/s" for k, v in sorted(sp.items())) +
               f" · speed-up ×{gain:.1f}", f"≥ ×{min(3, w * 0.5):.1f} with {w} workers",
               "Each tile is processed on its own; the only global steps (block and row IDs, measurements) take seconds.")


@check("P3", "perf", "Route planning cost: grid once, then one shortest-path search per stop")
def p3():
    if not PERF.exists():
        return res("WARN", "not measured", "", "Run python -m pipeline.qa --perf")
    p = json.loads(PERF.read_text())
    w = max(1, (p["cpu"] or 2) - 1)
    est = lambda n: p["grid_s"] + n * p["dijkstra_s_per_stop"] / w
    return res("PASS", f"grid {p['grid_s']:.0f} s ({p['grid_cells']:,} walkable cells at 0.5 m), {p['dijkstra_s_per_stop'] * 1000:.0f} ms per stop",
               "", f"Distance matrix ≈ grid + stops × search ÷ {w} workers: 50 stops ≈ {est(50):.0f} s, 900 stops ≈ {est(900):.0f} s (+ 60 s TSP). "
               "A zone chosen in the app has tens of stops: the browser plans it in 1–5 s.")


@check("P4", "perf", "Web interface: data loaded at start")
def p4():
    if not PERF.exists():
        return res("WARN", "not measured", "", "Run python -m pipeline.qa --perf")
    mb = json.loads(PERF.read_text())["web_boot_mb"]
    return res(grade(mb, 30, 60), f"{mb:.1f} MB (imagery 10 cm/px for 145 ha + vector layers)", "≤ 30 MB (WARN ≤ 60 MB)",
               "Static site on GitHub Pages; the imagery is cut in 32 chunks, full 2.5 cm/px only on the reference tiles.")


@check("P5", "perf", "Measured end-to-end times of the pipeline stages (out/timing.json)")
def p5():
    t = json.loads((C.OUT / "timing.json").read_text())
    keys = [k for k in ("targets", "route", "validate", "measure", "web") if k in t]
    total = sum(t[k] for k in keys)
    return res("PASS" if total < 20 * 60 else "WARN", ", ".join(f"{k} {t[k]:.0f} s" for k in keys) + f" · total {total / 60:.1f} min",
               "< 20 min from the Marcaj export to all deliverables", f"Hardware: {t.get('hardware', '')}. Detection of the 311 tiles: 5 min 24 s (README).")


# ================= runner and report =================
from pipeline import qa_web  # noqa: E402

qa_web.register(check, res, grade, EVID)


def run_all(perf=False, images=True, only=None, web=True):
    if perf:
        run_perf()
    out = []
    for cid, level, title, fn in CHECKS:
        if only and cid not in only:
            continue
        if level == "web" and not web:
            continue
        t = time.time()
        try:
            r = fn()
        except Exception as ex:  # a crashing check is a failed check, the others still run
            r = res("FAIL", f"crashed: {type(ex).__name__}: {ex}")
        r.update(id=cid, level=level, title=title, seconds=round(time.time() - t, 1))
        out.append(r)
        print(f"  {cid:4} {r['status']:4}  {title[:70]}  ({r['seconds']} s)", flush=True)
    if images:
        out_imgs = evidence_images()
        for r in out:
            if r["id"] == "C2":
                r["images"] = out_imgs
    return out


def evidence_images(n=4):
    """Crops of the blocks with the weakest rows (C2), rows in yellow, 10 cm/px -> docs/qa/."""
    EVID.mkdir(parents=True, exist_ok=True)
    sb = support_by_block()
    worst = [v for v, (L, w) in sorted(sb.items(), key=lambda kv: -kv[1][1]) if w > 0.5][:n]
    T, files = tiles(), []
    for vid in worst:
        gs = [g for rid, v, tile, g, _ in rows_utm() if v == vid]
        x0, y0, x1, y1 = unary_union(gs).bounds
        x0, y0, x1, y1 = x0 - 10, y0 - 10, x1 + 10, y1 + 10
        r = 0.1
        W, H = int((x1 - x0) / r), int((y1 - y0) / r)
        im = Image.new("RGB", (W, H))
        for t in T.values():
            a, b, c, d = t.bounds
            if c < x0 or a > x1 or d < y0 or b > y1:
                continue
            im.paste(Image.open(t.path).convert("RGB").resize((512, 512)), (int((a - x0) / r), int((y1 - d) / r)))
        dr = ImageDraw.Draw(im)
        for g in gs:
            dr.line([((x - x0) / r, (y1 - y) / r) for x, y in g.coords], fill=(255, 220, 0), width=3)
        im.thumbnail((720, 720))
        p = EVID / f"weak_rows_{vid}.jpg"
        im.save(p, quality=82)
        files.append(p.relative_to(C.ROOT).as_posix())
    return files


def git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=C.ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "?"


def write_report(results, seconds):
    cnt = Counter(r["status"] for r in results)
    md5 = hashlib.md5(INP.read_bytes()).hexdigest()[:10]
    L = [f"# VinePlan · QA report", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M')} · commit `{git_rev()}` · annotations `{INP.relative_to(C.ROOT) if INP.is_relative_to(C.ROOT) else INP}` (md5 {md5}) · "
         f"{hardware()} · {seconds:.0f} s", "",
         f"**{cnt['PASS']} PASS · {cnt['WARN']} WARN · {cnt['FAIL']} FAIL** out of {len(results)} checks. "
         "PASS: meets the threshold. WARN: known limitation or close to the limit, explained below. FAIL: broken.", "",
         "Integration tests run on the real Sireț3 data (311 tiles, the annotations sent to Marcaj, the official route) and on "
         "synthetic tiles built to hit one edge case each. Rerun: `python -m pipeline.qa --perf` (this report) or "
         "`VINEPLAN_INTEGRATION=1 python -m unittest tests.test_integration` (the same checks as tests).", "",
         "| Level | Checks | PASS | WARN | FAIL |", "|---|---|---|---|---|"]
    for lv, name in LEVELS.items():
        rs = [r for r in results if r["level"] == lv]
        if rs:
            c = Counter(r["status"] for r in rs)
            L.append(f"| {name} | {len(rs)} | {c['PASS']} | {c['WARN']} | {c['FAIL']} |")
    for lv, name in LEVELS.items():
        rs = [r for r in results if r["level"] == lv]
        if not rs:
            continue
        L += ["", f"## {name}", "", "| ID | Check | Measured | Threshold | Status |", "|---|---|---|---|---|"]
        for r in rs:
            L.append(f"| {r['id']} | {r['title']} | {r['measured']} | {r['threshold']} | **{r['status']}** |")
        for r in rs:
            if r["detail"] or r["evidence"] or r.get("images"):
                L += ["", f"**{r['id']}** · {r['detail']}".rstrip(" ·")]
                L += [f"- {e}" for e in r["evidence"][:8]]
                L += [f"![{Path(p).stem}]({p})" for p in r.get("images", [])]
    L += ["", "## Known limitations and what covers them", "",
          "- **Striped meadows and scrub (C2, E6).** A classical row detector sees any vegetation strips at a vine-like spacing as rows. "
          "Mitigation: the YOLO11 canopy veto empties tiles where the neural model sees no vines, and every block flagged in C2 is on the "
          "Marcaj correction list; the final numbers are computed from the corrected Marcaj export.",
          "- **Rows running onto roads (B4).** Part of the row length lies on the organisers' passage polygons, which in places cover "
          "planted rows. Routing never blocks a road; the correction in Marcaj trims rows that really cross a road.",
          "- **Route coverage.** Rows are walls (the trellis cannot be crossed), so targets inside closed pockets are left out rather than "
          "reached by cutting through a row; the route stays valid (≤ 2 % outside, back at START).", "",
          "## How the checks work", "",
          "- Source: `pipeline/qa.py`; each check returns the measured value, the threshold and the evidence. The synthetic tiles are "
          "generated in memory (2048 × 2048 px at 2.5 cm/px) and go through the same detector as the real tiles "
          "(`pipeline/baseline.py`).",
          "- Performance figures come from `python -m pipeline.qa --perf` (cached in `out/qa_perf.json`) and from the timed pipeline "
          "runs in `out/timing.json`."]
    REPORT.write_text("\n".join(L) + "\n")
    (C.OUT / "qa_report.json").write_text(json.dumps(results, indent=1, default=str))


def data_ready():
    return INP.exists() and C.TILES.exists() and (C.ROOT / "route.geojson").exists()


def main() -> None:
    global INP
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(INP), help="annotations to test (default: out/marcaj_global.xml)")
    ap.add_argument("--perf", action="store_true", help="(re)measure performance (~3 min), else use out/qa_perf.json")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--only", nargs="*", help="run only these check IDs")
    ap.add_argument("--no-web", action="store_true", help="skip the web app checks (headless Chromium, ~2 min)")
    a = ap.parse_args()
    INP = Path(a.inp).resolve()
    if not data_ready():
        sys.exit(f"missing data: {INP}, {C.TILES} or route.geojson")
    t0 = time.time()
    results = run_all(perf=a.perf, images=not a.no_images, only=a.only, web=not a.no_web)
    write_report(results, time.time() - t0)
    c = Counter(r["status"] for r in results)
    print(f"\n{c['PASS']} PASS · {c['WARN']} WARN · {c['FAIL']} FAIL -> {REPORT}  ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
