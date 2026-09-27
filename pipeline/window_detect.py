"""Small vineyards the tile-wide detector misses: the same detector on 25.6 m windows over the whole mosaic.

The detector estimates ONE row direction and spacing per 51.2 m tile. A small vineyard (a strip of young vines, a
grassy plot) in a tile that is mostly meadow, scrub or field does not dominate that estimate, so the tile keeps no rows
(or, before v3, rows in the wrong direction). Here the same detector (pipeline.baseline, all its filters: periodicity,
row support, tree filter, forbidden zones, >= 3 rows) runs on 1024 px windows with a 512 px stride. A window row is kept
only where the existing annotations do not explain it (farther than half a row spacing + 1 m from every existing row),
and the pieces found by overlapping windows are merged into one polyline per physical row. A tile gets new rows only
if at least MIN_ROWS parallel ones survive. Canopies come from the detector (deduplicated across windows); inter-rows
are rebuilt between neighbouring new rows. Tiles emptied by the model veto (checked visually: meadow, scrub, gardens)
are not searched. False rows on this scale came from yards and gardens, road verges and scrub, so a new row must be
found by >= 2 overlapping windows (a tile's best by >= 3), lie > 8 m from any roof / paved yard (unsaturated or red-tile
patches >= 20 m2) and not run along an organiser passage.

Usage (called by pipeline.blocks before pipeline.row_extend):  add(data, tiles_dir, workers) -> (data, Counter)
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.tiles import Tile, open_tile  # noqa: E402

RES = 0.025
WIN, STRIDE = 512, 256       # px: 12.8 m windows, half overlapping (a 15 m strip of young vines dominates them)
FOOT = 1.0                   # m beyond half a row spacing around existing rows: already explained
KEEP_OUT = 0.8               # a window row is new if at least this share of it lies outside the explained area
MIN_LEN = 3.0                # m, shortest new row piece
ANG, GAP = 4.0, 4.0         # merge window pieces of one row: angle (deg), along-row gap (m) ...
LAT_SHARE = 0.35             # ... and lateral offset (share of the row spacing)
MIN_WINDOWS = 2              # a new row must be found by at least 2 overlapping windows (noise shows up in one)
MIN_ROWS = 3                 # parallel new rows needed in a tile
DEDUP_IOU = 0.3              # canopies found by two windows
DEBUG = []                   # filled with per-tile candidates when a caller sets it to a non-empty list
BUILD_M2, BUILD_DIST = 20.0, 8.0     # roofs / paved yards of at least 20 m2; no new row within 8 m of one
ROAD_DIST, ROAD_SHARE = 1.5, 0.3     # no new row with 30 % of its length within 1.5 m of an organiser passage
MAX_WINDOWS_MIN = 3                  # a tile's new rows need one found by at least 3 windows


@dataclass(frozen=True)
class WinTile(Tile):
    img: object = None

    def read(self):
        return self.img


def _unit(g: LineString) -> np.ndarray:
    c = np.asarray(g.coords)
    u = c[-1] - c[0]
    u = u / (np.hypot(*u) + 1e-9)
    return u if (u[0] > 0 or (u[0] == 0 and u[1] > 0)) else -u


def _merge(rows: list[tuple[LineString, int]], period_px: float) -> list[tuple[LineString, int]]:
    """Collinear pieces of one row (px), found by different windows -> one line spanning all of them (gaps inside a
    row do not split it); returns (line, number of distinct windows that found it). Pieces within LAT_SHARE of a row
    spacing sideways are the same row (per-window fits of one row differ by a few decimetres)."""
    lat_tol = LAT_SHARE * period_px
    rows = sorted(rows, key=lambda r: -r[0].length)
    groups = []                                   # [ref unit, ref point, [pieces], {windows}]
    for g, w in rows:
        u = _unit(g)
        c = np.asarray(g.coords)
        placed = False
        for grp in groups:
            u0, p0, pieces, wins = grp
            if abs(u @ u0) < math.cos(math.radians(ANG)):
                continue
            n0 = np.array([-u0[1], u0[0]])
            offs = [float(np.mean((np.asarray(q.coords) - p0) @ n0)) for q in pieces]
            if abs(float(np.mean((c - p0) @ n0)) - np.mean(offs)) > lat_tol:
                continue
            t = sorted(((c - p0) @ u0).tolist())
            spans = [sorted(((np.asarray(q.coords) - p0) @ u0).tolist()) for q in pieces]
            if min(max(0.0, t[0] - sp[-1], sp[0] - t[-1]) for sp in spans) > GAP / RES:
                continue
            pieces.append(g)
            wins.add(w)
            placed = True
            break
        if not placed:
            groups.append([u, c[0], [g], {w}])
    out = []
    for u0, p0, pieces, wins in groups:
        n0 = np.array([-u0[1], u0[0]])
        pts = np.vstack([np.asarray(q.coords) for q in pieces])
        t = (pts - p0) @ u0
        w = np.array([q.length for q in pieces for _ in q.coords])
        off = float(np.average((pts - p0) @ n0, weights=w))
        a, b = p0 + u0 * t.min() + n0 * off, p0 + u0 * t.max() + n0 * off
        out.append((LineString([tuple(a), tuple(b)]), len(wins)))
    return out


def _period_px(rows: list[LineString]) -> float:
    d = []
    for i, a in enumerate(rows):
        ua = _unit(a)
        na = np.array([-ua[1], ua[0]])
        m = np.asarray(a.interpolate(0.5, normalized=True).coords[0])
        best = None
        for j, b in enumerate(rows):
            if i != j and abs(_unit(b) @ ua) >= math.cos(math.radians(8)):
                off = abs((np.asarray(b.interpolate(b.project(Point(m))).coords[0]) - m) @ na)
                if 1.2 / RES < off < 4.5 / RES and (best is None or off < best):
                    best = off
        if best is not None:
            d.append(best)
    return float(np.median(d)) if d else 2.6 / RES


def buildings_px(img: np.ndarray) -> Polygon:
    """Roofs and paved yards (px): unsaturated bright surfaces (metal / concrete) and red tiles, as compact patches
    >= BUILD_M2. Vineyards, meadows and bare soil are more saturated (beige, brown, green) and are not picked up."""
    import cv2
    f = img.astype(np.int16)
    mx, mn = f.max(2), f.min(2)
    sat = (mx - mn) / np.maximum(mx, 1)
    m = (((sat < 0.10) & (mx > 95)) | ((f[..., 0] > 120) & (f[..., 0] > 1.35 * f[..., 1]) & (f[..., 0] > 1.35 * f[..., 2])))
    small = (cv2.resize(m.astype(np.uint8), (512, 512), interpolation=cv2.INTER_AREA) > 0).astype(np.uint8)   # 10 cm
    small = cv2.morphologyEx(small, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    small = cv2.morphologyEx(small, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(small, 8)
    polys = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] * 0.01 < BUILD_M2:
            continue
        cs, _ = cv2.findContours((lab == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        hull = cv2.contourArea(cv2.convexHull(c))
        if hull > 0 and stats[i, cv2.CC_STAT_AREA] / hull >= 0.6 and len(c) >= 3:
            polys.append(Polygon(c[:, 0, :] * 4.0).buffer(0))
    return unary_union(polys) if polys else Polygon()


def _init():
    import cv2
    cv2.setNumThreads(1)


def search_tile(job):
    """One tile: window detections not explained by the existing rows -> (tile name, new rows px, canopies px, stats)."""
    path, existing, forbidden_wkb, passages_wkb = (tuple(job) + (None,))[:4]
    from shapely import affinity, wkb
    from pipeline import baseline as B
    t = open_tile(path)
    img = t.read()
    p = B.P()
    forbidden = wkb.loads(forbidden_wkb) if forbidden_wkb else None
    road = Polygon()
    if passages_wkb:
        loc = wkb.loads(passages_wkb).intersection(box(*t.bounds).buffer(5))
        if not loc.is_empty:                           # UTM -> this tile's pixels
            road = affinity.affine_transform(loc, [1 / t.res, 0, 0, -1 / t.res, -t.x0 / t.res, t.y0 / t.res])
    ex = [LineString(c) for c in existing]
    per_ex = _period_px(ex) if len(ex) >= 2 else 2.6 / RES
    explained = unary_union([g.buffer(per_ex / 2 + FOOT / RES) for g in ex]) if ex else Polygon()
    st = Counter()
    pieces, cans, periods = [], [], []
    wid = 0
    for oy in range(0, t.height - WIN + 1, STRIDE):
        for ox in range(0, t.width - WIN + 1, STRIDE):
            crop = img[oy:oy + WIN, ox:ox + WIN]
            if (crop.sum(2) == 0).mean() > 0.6:
                continue
            wt = WinTile(name=t.name, path=t.path, row=t.row, col=t.col, x0=t.x0 + ox * t.res, y0=t.y0 - oy * t.res,
                         res=t.res, width=WIN, height=WIN, img=crop)
            wid += 1
            objs = B.process_tile(wt, p)
            if forbidden is not None:
                objs = B.drop_forbidden(objs, wt, forbidden, p)
            st["windows"] += 1
            if not objs:
                continue
            st["windows with rows"] += 1
            shift = np.array([ox, oy], float)
            for o in objs:
                pts = np.asarray(o["points"], float) + shift
                if o["label"] == "row":
                    g = LineString(pts)
                    out = g.difference(explained) if not explained.is_empty else g
                    if out.length < KEEP_OUT * g.length:
                        continue
                    parts = [q for q in getattr(out, "geoms", [out]) if q.geom_type == "LineString"]
                    q = max(parts, key=lambda q: q.length, default=None)
                    if q is not None and q.length * RES >= MIN_LEN:
                        pieces.append((q, wid))
                elif o["label"] == "vineyard" and len(pts) >= 3:
                    cp = Polygon(pts)
                    if cp.is_valid and not explained.contains(cp.centroid):
                        cans.append(cp)
    per_px = _period_px([g for g, _ in pieces]) if len(pieces) >= 2 else 2.6 / RES
    merged = _merge(pieces, per_px)
    st["row candidates"] += len(merged)
    rows = [(g, nw) for g, nw in merged if nw >= MIN_WINDOWS]
    st["candidates found by one window only (dropped)"] += len(merged) - len(rows)
    if rows:                                       # not in yards or along roads
        built = buildings_px(img)
        if not built.is_empty:
            near = built.buffer(BUILD_DIST / RES)
            n0 = len(rows)
            rows = [(g, nw) for g, nw in rows if not g.intersects(near)]
            st["candidates next to a building (dropped)"] += n0 - len(rows)
        if not road.is_empty:
            rz = road.buffer(ROAD_DIST / RES)
            n0 = len(rows)
            rows = [(g, nw) for g, nw in rows if g.intersection(rz).length < ROAD_SHARE * g.length]
            st["candidates along a road (dropped)"] += n0 - len(rows)
    if rows and max(nw for _, nw in rows) < MAX_WINDOWS_MIN:
        st["tiles whose candidates are all weak (dropped)"] += 1
        rows = []
    # grow the vineyard from its strong rows (>= 3 windows): a weaker row stays only as the neighbour of a kept row
    # (<= 1.6 row spacings sideways, parallel, overlapping along the row); isolated weak lines in scrub go
    kept = [g for g, nw in rows if nw >= MAX_WINDOWS_MIN]
    weak = [g for g, nw in rows if nw < MAX_WINDOWS_MIN]
    grew = True
    while grew and weak:
        grew = False
        for g in list(weak):
            u = _unit(g)
            c = np.asarray(g.coords)
            for k in kept:
                uk = _unit(k)
                if abs(u @ uk) < math.cos(math.radians(8)):
                    continue
                ck = np.asarray(k.coords)
                nk = np.array([-uk[1], uk[0]])
                lat = abs(float(np.mean((c - ck[0]) @ nk)))
                ta, tb = sorted(((c - ck[0]) @ uk).tolist()), sorted(((ck - ck[0]) @ uk).tolist())
                if lat <= 1.6 * per_px and min(ta[-1], tb[-1]) - max(ta[0], tb[0]) > 0:
                    kept.append(g)
                    weak.remove(g)
                    grew = True
                    break
    st["weak candidates away from the vineyard (dropped)"] += len(weak)
    rows = kept
    if DEBUG:
        DEBUG.append((t.name, [(np.asarray(g.coords).tolist(), nw) for g, nw in merged], per_px))
    rows = [g.intersection(box(0, 0, 2047.5, 2047.5)) for g in rows]
    rows = [g for g in rows if g.geom_type == "LineString" and g.length * RES >= MIN_LEN]
    # a vineyard: >= MIN_ROWS parallel new rows (the dominant direction)
    if rows:
        ref = max(rows, key=lambda g: g.length)
        par = [g for g in rows if abs(_unit(g) @ _unit(ref)) >= math.cos(math.radians(8))]
        rows = par if len(par) >= MIN_ROWS else []
    if not rows:
        return t.name, [], [], st
    band = unary_union([g.buffer(0.45 / RES, cap_style="flat") for g in rows])
    cans = [c for c in cans if band.contains(c.centroid)]
    keep = []
    if cans:
        tree = STRtree(cans)
        taken = set()
        for i in sorted(range(len(cans)), key=lambda i: -cans[i].area):
            if i in taken:
                continue
            keep.append(cans[i])
            for j in tree.query(cans[i]):
                j = int(j)
                if j != i and j not in taken:
                    inter = cans[i].intersection(cans[j]).area
                    if inter / max(cans[i].union(cans[j]).area, 1e-9) >= DEDUP_IOU:
                        taken.add(j)
    st["new rows"] += len(rows)
    st["new canopies"] += len(keep)
    return (t.name, [np.asarray(g.coords).tolist() for g in rows],
            [np.asarray(c.exterior.coords)[:-1].tolist() for c in keep], st)


def add(data: dict[str, list[dict]], tiles_dir: Path, workers: int = 0):
    from shapely import wkb
    from pipeline.baseline import load_forbidden
    from pipeline.row_extend import add_rows
    veto_p = C.OUT / "veto_yolo11.json"
    veto = set(json.loads(veto_p.read_text())) if veto_p.exists() else set()
    forbidden = load_forbidden()
    fb = wkb.dumps(forbidden) if forbidden is not None else None
    from pipeline.blocks import load_passages
    pas = load_passages()
    pb = wkb.dumps(pas) if not pas.is_empty else None
    jobs = []
    for pth in sorted(Path(tiles_dir).glob("siret3_r*_c*.tif")):
        if pth.name in veto:
            continue
        ex = [o["points"] for o in data.get(pth.name, []) if o["label"] == "row" and len(o["points"]) > 1]
        jobs.append((str(pth), ex, fb, pb))
    st = Counter()
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with mp.get_context("spawn").Pool(workers, initializer=_init) as pool:
        results = pool.map(search_tile, jobs, chunksize=2)
    for name, rows, cans, s in results:
        st.update(s)
        if not rows:
            continue
        st["tiles with new rows"] += 1
        t = open_tile(Path(tiles_dir) / name)
        objs = data.setdefault(name, [])
        for c in cans:
            objs.append({"label": "vineyard", "type": "polygon", "points": [[float(x), float(y)] for x, y in c],
                         "attrs": {"vineyard_id": ""}})
        lines = [LineString(t.px_to_utm(np.asarray(r))) for r in rows]
        per = _period_px([LineString(r) for r in rows]) * RES
        add_rows(data, name, t, [({"vineyard_id": "", "row_id": "", "row_structure": "regular"}, g, 0.0, _unit(g), per)
                                  for g in lines], st, canopies=False, known_cans=[Polygon(c) for c in cans])
    return data, st
