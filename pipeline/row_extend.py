"""Rows that stop on a tile edge continue into the neighbouring tile as long as the vines do (whole mosaic).

The detector works tile by tile. Where a vineyard only fills a corner of a tile, or its inter-rows are grassy, the row
pattern of that tile is too weak and the tile keeps no rows, although the rows of the tile next to it run up to the
shared edge and the vines carry on (to the road, to the end of the block). Here every row end lying on a tile edge with
no continuing segment across it is followed into the neighbouring tile along its own axis:

  contrast   greenness (ExG, continuous) on the axis (+-0.25 m) minus on the two mid-lines half a row spacing away,
             smoothed over 1.5 m; the row carries on while the contrast stays at least KEEP x the contrast of the same
             row over its last 10 m in its own tile (so a grassy vineyard is measured against itself)
  stops      a stretch of STOP metres without that contrast (the row end), an organiser passage (rows stop at the
             road), the black no-data border, an existing row of the neighbouring tile on the same axis
  pieces     the continuation gets the row's IDs (blocks re-links it), canopies from the green pixels of its vine band
             (skipped where the band is uniformly green: grass, not plants) and inter-rows between continued
             neighbouring rows, with their cover. Up to 3 passes (a row can cross a whole empty tile).

Usage (called by pipeline.blocks before pipeline.row_ends):  extend(data, tiles_dir, passages_utm) -> (data, Counter)
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import LineString, Point, Polygon, box
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.tiles import all_tiles, open_tile  # noqa: E402

RES = 0.025
STEP = 0.1           # m between samples
WIN = 1.5            # m smoothing
REF_LEN = 10.0       # m of the row in its own tile that give its reference contrast
KEEP = 0.45          # the continuation keeps at least this share of the reference contrast ...
S_MIN = 0.02         # ... and at least this absolute contrast (ExG units)
STOP = 1.5           # m without contrast end the row
MIN_EXT = 1.0        # m, shorter continuations are not added
EDGE = 0.6           # m, a row end this close to a tile edge lies on it
ON_BAND, MID_BAND = 0.25, 0.10
CANOPY_HALF = 0.30   # m, vine band (as the detector)
UNIFORM = 0.80       # band green share above which the band is grass, not separate plants: no canopies
MIN_CANOPY = 0.12    # m2
TILE_BOX = box(0, 0, 2047.5, 2047.5)   # new geometry stays inside the tile (vertices on its last pixel at most)


@lru_cache(maxsize=6)
def exg(path: str) -> np.ndarray:
    f = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    s = f.sum(2)
    e = (2 * f[..., 1] - f[..., 0] - f[..., 2]) / (s + 1e-6)
    e[s == 0] = np.nan                                   # the black no-data border
    return e


def _sample(e: np.ndarray, px: np.ndarray) -> np.ndarray:
    h, w = e.shape
    x, y = np.round(px[..., 0]).astype(int), np.round(px[..., 1]).astype(int)
    ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    v = np.full(ok.shape, np.nan, np.float32)
    v[ok] = e[y[ok], x[ok]]
    return v


def contrast(tile, e, p0, u, length, period):
    """Along the ray p0 + t u (UTM), t in [0, length]: t, smoothed on-minus-mid contrast, on-axis value (nan = no data)."""
    t = np.arange(0.0, max(length, STEP) + 1e-9, STEP)
    n = np.array([-u[1], u[0]])
    pts = p0[None, :] + t[:, None] * u[None, :]

    def band(center, half):
        offs = center + np.arange(-half, half + 1e-9, 0.05)
        q = pts[:, None, :] + offs[None, :, None] * n[None, None, :]
        v = _sample(e, tile.utm_to_px(q.reshape(-1, 2)).reshape(q.shape))
        return np.nanmean(v, axis=1) if np.isfinite(v).any() else np.full(len(t), np.nan)

    with np.errstate(all="ignore"):
        on = band(0.0, ON_BAND)
        mid = np.nanmean(np.stack([band(-period / 2, MID_BAND), band(period / 2, MID_BAND)]), axis=0)
        s = on - mid
        k = max(1, int(round(WIN / STEP)))
        good = np.isfinite(s)
        movsum = lambda x: np.convolve(x, np.ones(k), "full")[(k - 1) // 2:(k - 1) // 2 + len(x)]   # centred, len(x)
        num = movsum(np.where(good, s, 0.0))
        den = movsum(good.astype(float))
        sm = np.where(den > 0.5 * k, num / np.maximum(den, 1), np.nan)
    return t, sm, on


def _tile_period(lines_utm) -> float:
    d = []
    for i, a in enumerate(lines_utm):
        ca = np.asarray(a.coords)
        ua = (ca[-1] - ca[0]) / (np.hypot(*(ca[-1] - ca[0])) + 1e-9)
        na = np.array([-ua[1], ua[0]])
        m = np.asarray(a.interpolate(0.5, normalized=True).coords[0])
        best = None
        for j, b in enumerate(lines_utm):
            if i == j:
                continue
            cb = np.asarray(b.coords)
            ub = (cb[-1] - cb[0]) / (np.hypot(*(cb[-1] - cb[0])) + 1e-9)
            if abs(ua @ ub) < math.cos(math.radians(8)):
                continue
            off = abs((np.asarray(b.interpolate(b.project(Point(m))).coords[0]) - m) @ na)
            if 1.2 < off < 4.5 and (best is None or off < best):
                best = off
        if best is not None:
            d.append(best)
    return float(np.median(d)) if d else 2.6


def _canopies(tile, e, line_utm, exg_crown=0.12):
    """Canopy polygons (px) of the vine band around a continued row; [] where the band is uniformly green."""
    import cv2
    band = line_utm.buffer(CANOPY_HALF, cap_style="flat")
    poly_px = tile.utm_to_px(np.asarray(band.exterior.coords))
    h, w = e.shape
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.round(poly_px).astype(np.int32)], 1)
    inb = mask.astype(bool) & np.isfinite(e)
    if inb.sum() < 50:
        return [], 0.0
    crown = inb & (np.nan_to_num(e, nan=-1) > exg_crown)
    share = float(crown.sum() / inb.sum())
    if share > UNIFORM:
        return [], share
    m = crown.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    m = cv2.dilate(m, np.ones((3, 3), np.uint8)) & mask
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cs:
        c = cv2.approxPolyDP(c, 1.0, True)[:, 0, :]
        if len(c) < 3:
            continue
        p = Polygon(c)
        if not p.is_valid:
            p = p.buffer(0)
            if p.geom_type != "Polygon":
                continue
        if p.area * RES * RES >= MIN_CANOPY:
            out.append(p)
    return out, share


def _structure(line_len_m, canopies_px, line_px):
    """regular / disrupted from the longest stretch of the continued row without canopy (>= 5.5 m, as the detector)."""
    if not canopies_px:
        return "regular" if line_len_m < 5.5 else "disrupted"
    iv = sorted((line_px.project(Point(p.exterior.coords[0])), line_px.project(p.centroid)) for p in canopies_px)
    pos = sorted(b for _, b in iv)
    gaps = np.diff([0.0] + pos + [line_px.length]) * RES
    return "disrupted" if gaps.max() >= 5.5 else "regular"


def extend(data: dict[str, list[dict]], tiles_dir: Path, passages_utm=None, passes: int = 3):
    tiles = {t.name: t for t in all_tiles(Path(tiles_dir))} if Path(tiles_dir).exists() else {}
    st = Counter()
    pas_prep = prep(passages_utm) if passages_utm is not None and not passages_utm.is_empty else None

    def tile_at(x, y):
        for n, t in tiles.items():
            b = t.bounds
            if b[0] <= x < b[2] and b[1] <= y < b[3]:
                return n
        return None

    def path(n):
        p = Path(tiles_dir) / n
        return str(p if p.exists() else C.EXAMPLES / "images" / n)

    done = set()                                           # row ends already followed (tile, object index, which end)
    for it in range(passes):
        rows = []                                          # (tile, obj index, utm line)
        for n, objs in data.items():
            if n not in tiles:
                continue
            for k, o in enumerate(objs):
                if o["label"] == "row" and o["type"] == "polyline" and len(o["points"]) > 1:
                    rows.append((n, k, LineString(tiles[n].px_to_utm(o["points"]))))
        by_tile = defaultdict(list)
        for i, (n, _, _) in enumerate(rows):
            by_tile[n].append(i)
        period = {n: _tile_period([rows[i][2] for i in ix]) for n, ix in by_tile.items()}
        new_rows = defaultdict(list)                       # neighbour tile -> [(source attrs, utm line, lateral key)]
        for n, k, g in rows:
            c = np.asarray(g.coords)
            b = tiles[n].bounds
            for w, (p, q) in enumerate(((c[0], c[1]), (c[-1], c[-2]))):
                if (n, k, w) in done:
                    continue
                done.add((n, k, w))
                if min(p[0] - b[0], b[2] - p[0], p[1] - b[1], b[3] - p[1]) > EDGE:
                    continue
                if np.hypot(*(p - q)) < 1e-6:
                    continue
                u = (p - q) / np.hypot(*(p - q))
                nb = tile_at(*(p + u * 0.8))
                if nb is None or nb == n:
                    continue
                nrm = np.array([-u[1], u[0]])
                # an existing segment of the neighbour on this axis: continued already, or the extension stops at it
                # (lateral tolerance: half a row spacing: per-tile fits of one row can be ~1 m apart at the seam, the
                # next row is a whole spacing away)
                cap = 51.2 * 1.5
                continued = False
                lat_tol = 0.5 * period.get(n, 2.6)
                for j in by_tile.get(nb, []):
                    cj = np.asarray(rows[j][2].coords)
                    uj = (cj[-1] - cj[0]) / (np.hypot(*(cj[-1] - cj[0])) + 1e-9)
                    if abs(uj @ u) < math.cos(math.radians(12)):
                        continue
                    for e_ in (cj[0], cj[-1]):
                        along, lat = (e_ - p) @ u, abs((e_ - p) @ nrm)
                        if lat <= lat_tol and -0.5 <= along <= 3.0:
                            continued = True
                        elif lat <= lat_tol and along > 3.0:
                            cap = min(cap, along - 0.2)
                if continued:
                    continue
                tb = tiles[nb].bounds
                # distance to the far side of the neighbour tile along u
                ts = [((hi if u[i_] > 0 else lo) - p[i_]) / u[i_]          # exit side only: p lies on the entry side
                      for i_, (lo, hi) in enumerate(((tb[0], tb[2]), (tb[1], tb[3]))) if abs(u[i_]) > 1e-9]
                length = min(min(ts) if ts else 0.0, cap)
                if length < MIN_EXT:
                    continue
                per = period.get(n, 2.6)
                e_src, e_nb = exg(path(n)), exg(path(nb))
                # reference contrast: the last REF_LEN m of this row in its own tile (walking back from the end)
                _, s_ref, _ = contrast(tiles[n], e_src, p, -u, min(REF_LEN, g.length), per)
                ref = np.nanmedian(s_ref) if np.isfinite(s_ref).any() else np.nan
                if not np.isfinite(ref) or ref < S_MIN:
                    st["ends skipped: row without contrast in its own tile"] += 1
                    continue
                thr = max(S_MIN, KEEP * ref)
                t, s, on = contrast(tiles[nb], e_nb, p, u, length, per)
                pts = p[None, :] + t[:, None] * u[None, :]
                good = np.isfinite(s) & (s >= thr) & np.isfinite(on)
                if pas_prep is not None:                   # rows stop at the road
                    on_road = np.array([pas_prep.contains(Point(x, y)) for x, y in pts[:: 5]])
                    road_idx = np.flatnonzero(np.repeat(on_road, 5)[: len(t)])
                    if len(road_idx):
                        good[road_idx[0]:] = False
                # walk until STOP metres without contrast (or the black border): the row ends at its last supported sample
                bad_run, run, end = int(round(STOP / STEP)), 0, len(t)
                for i_ in range(len(t)):
                    if not np.isfinite(on[i_]):
                        end = i_
                        break
                    run = 0 if good[i_] else run + 1
                    if run >= bad_run:
                        end = i_
                        break
                sup = np.flatnonzero(good[:end])
                if not len(sup):
                    st["ends checked: no continuation"] += 1
                    continue
                ext = float(t[sup[-1]])
                if ext < MIN_EXT:
                    st["ends checked: no continuation"] += 1
                    continue
                line = LineString([tuple(p), tuple(p + u * ext)])
                lat_key = 0.0                                  # set per neighbour tile below (common origin / normal)
                o = data[n][k]
                new_rows[nb].append((dict(o["attrs"]), line, lat_key, u, per))
                st["rows continued"] += 1
                st["m of row added"] += ext
        if not new_rows:
            break
        for nb, items in new_rows.items():
            # one continuation per axis: continued from both sides into the same tile, the longer one stays
            items.sort(key=lambda r: -r[1].length)
            kept = []
            for r in items:
                c = np.asarray(r[1].coords)
                u_, lat_tol = r[3], 0.5 * r[4]
                n_ = np.array([-u_[1], u_[0]])
                dup = False
                for q in kept:
                    cq = np.asarray(q[1].coords)
                    if abs(q[3] @ u_) < math.cos(math.radians(12)):
                        continue
                    lat = abs((cq[0] - c[0]) @ n_)
                    ta = sorted(((cq - c[0]) @ u_).tolist())
                    tb_ = sorted(((c - c[0]) @ u_).tolist())
                    if lat <= lat_tol and min(ta[-1], tb_[-1]) - max(ta[0], tb_[0]) > 1.0:
                        dup = True
                        break
                if dup:
                    st["duplicate continuations dropped"] += 1
                    st["rows continued"] -= 1
                    st["m of row added"] -= r[1].length
                else:
                    kept.append(r)
            items = kept
            add_rows(data, nb, tiles[nb], items, st)
        st["passes"] = it + 1
    return data, st


def add_rows(data, name, t, items, st, canopies=True, known_cans=()):
    """Add rows (UTM lines) to tile `name`: the row polylines (clipped to the tile), canopies from their vine band
    (canopies=True; else known_cans, px polygons already added, give the row_structure) and inter-rows between
    neighbouring added rows (same direction, 0.6-1.6 row spacings apart), with their cover.
    items: [(attrs, utm line, _, unit direction, row spacing m)]."""
    import cv2
    e = exg(str(t.path))
    objs = data.setdefault(name, [])
    placed = []                                            # (utm line, attrs, spacing)
    for attrs, line, _, u, per in items:
        line_px = LineString(t.utm_to_px(np.asarray(line.coords))).intersection(TILE_BOX)
        if line_px.is_empty or line_px.geom_type != "LineString" or line_px.length * RES < MIN_EXT:
            continue
        line = LineString(t.px_to_utm(np.asarray(line_px.coords)))
        if canopies:
            cans, share = _canopies(t, e, line)
            cans = [q for q in (c.intersection(TILE_BOX) for c in cans)
                    if q.geom_type == "Polygon" and q.area * RES * RES >= MIN_CANOPY]
            for cp in cans:
                objs.append({"label": "vineyard", "type": "polygon",
                             "points": [[float(x), float(y)] for x, y in np.asarray(cp.exterior.coords)[:-1]],
                             "attrs": {"vineyard_id": attrs.get("vineyard_id", "")}})
            st["canopies added"] += len(cans)
            if share > UNIFORM:
                st["added rows on uniform green (no canopies)"] += 1
        else:
            band = line_px.buffer(CANOPY_HALF / RES * 1.5, cap_style="flat")
            cans = [c for c in known_cans if band.contains(c.centroid)]
        a = dict(attrs)
        a["row_structure"] = _structure(line.length, cans, line_px)
        objs.append({"label": "row", "type": "polyline", "points": [[float(x), float(y)] for x, y in line_px.coords],
                     "attrs": a})
        placed.append((line, attrs, per))
    if len(placed) < 2:
        return
    # one frame for the whole tile: direction of the longest added row, lateral position along its normal
    ref = max(placed, key=lambda r: r[0].length)[0]
    c0 = np.asarray(ref.coords)
    u0 = (c0[-1] - c0[0]) / (np.hypot(*(c0[-1] - c0[0])) + 1e-9)
    n0 = np.array([-u0[1], u0[0]])
    org = np.array([t.bounds[0], t.bounds[1]])
    rows = []
    for line, attrs, per in placed:
        c = np.asarray(line.coords)
        u = (c[-1] - c[0]) / (np.hypot(*(c[-1] - c[0])) + 1e-9)
        if abs(u @ u0) < math.cos(math.radians(8)):
            continue
        tt = (c - org) @ u0
        rows.append((float(np.mean((c - org) @ n0)), float(tt.min()), float(tt.max()), attrs, per))
    rows.sort(key=lambda r: r[0])
    for (la, a0, a1, aa, per), (lb, b0, b1, _, _) in zip(rows, rows[1:]):
        gap = lb - la
        if not (0.6 * per <= gap <= 1.6 * per):
            continue
        s0, s1 = max(a0, b0), min(a1, b1)
        if s1 - s0 < MIN_EXT:
            continue
        h = CANOPY_HALF
        quad = [org + u0 * s0 + n0 * (la + h), org + u0 * s1 + n0 * (la + h),
                org + u0 * s1 + n0 * (lb - h), org + u0 * s0 + n0 * (lb - h)]
        poly = Polygon(t.utm_to_px(np.asarray(quad))).intersection(TILE_BOX)
        if poly.geom_type != "Polygon" or poly.area * RES * RES < 0.5:
            continue
        mask = np.zeros(e.shape, np.uint8)
        cv2.fillPoly(mask, [np.round(np.asarray(poly.exterior.coords)).astype(np.int32)], 1)
        vals = e[mask.astype(bool)]
        vals = vals[np.isfinite(vals)]
        frac = float((vals > 0.08).mean()) if len(vals) else 0.0
        cover = "bare_soil" if frac < 0.25 else ("vegetation" if frac > 0.75 else "mixed")
        objs.append({"label": "interrow_area", "type": "polygon",
                     "points": [[float(x), float(y)] for x, y in np.asarray(poly.exterior.coords)[:-1]],
                     "attrs": {"vineyard_id": aa.get("vineyard_id", ""), "interrow_cover": cover}})
        st["inter-rows added"] += 1
