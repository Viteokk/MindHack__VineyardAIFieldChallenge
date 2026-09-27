"""Rows and inter-rows stop at the road and at the edge of the imagery (whole mosaic, after detection).

The detector ends a row where the vegetation along its axis ends, so a row can run across a track, poke into the road at
the end of the block, or carry on past the road into a garden. Rules 4.1 / 4.4 / 5.1 / 7: a row runs from its first to
its last vine, rows and inter-rows stop at the edge of the planting, roads and the black no-data border are not annotated.

Only evidence the two reference tiles confirm is used (per tile, in pixels):
  road crossing   a row running through an organiser passage (<= CUT_MAX on it) is cut only where the passage separates
                  two blocks (the planting is not connected around it); the cut follows the bare track on the row when
                  it shows within SEARCH of the passage. Inside one block it is a gap and the row runs on (reference
                  tile r006_c004 keeps its rows through a grass strip that is an organiser passage).
  end on a road   the part of a row end lying on a passage is removed when it is bare ground.
  no data         row ends on the black border outside the imagery are removed.
  garden pieces   a piece left beyond a cut road is dropped when it is not on a vine row (vegetation on the line no
                  higher than between the lines), with the canopies drawn along it.
  inter-rows      cut with their rows, and clipped to the stretch where both neighbouring rows exist; none remain
                  outside the outermost row.
Vegetation-only trimming of row ends is NOT used: young vines with a few leaves read as bare ground and grassy
inter-rows as meadow, and on the reference tiles it cut rows that run to the tile edge.

Usage (called by pipeline.blocks):  tidy(data, tiles_dir, passages_utm) -> (new data, Counter of changes)
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from shapely import affinity
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import substring, unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402
from pipeline.row_extend import exg as exg_cont  # noqa: E402

RES = 0.025          # m per pixel
STEP = 4             # px between samples along a row (0.1 m)
ON_BAND = 0.25       # m, half-width of the band sampled on the axis
MID_BAND = 0.10      # m, half-width of the bands sampled on the mid-lines (half a row spacing to each side)
BARE_ON = 0.05       # on-axis vegetation share below this = bare ground (0.5 m smoothing)
CUT_MAX = 10.0       # m, longest part of a row on a passage that counts as a crossing (longer: a row along a road)
SEARCH = 5.0         # m, the track is looked for this far on each side of the passage (OSM lines are a few m off)
GAP_MIN = 2.0        # m, shortest bare stretch that is a track
SUP_RATIO, SUP_DIFF = 1.3, 0.08   # a piece is on vines when on/mid >= 1.3 and on - mid >= 0.08 (as the detector)
MIN_PIECE = 1.0      # m, shorter pieces are dropped
CLIP_TOL = 0.2       # m, margin around the outline of an inter-row's two rows
CLIP_MIN_M2 = 1.5    # m2, smaller clips are ignored (corners where the two rows end on different tile edges)
CANOPY_BAND = 0.45   # m, canopies within this distance of a dropped piece (and of no kept row) go with it
PERIOD_DEFAULT = 2.6  # m
EDGE_PX = 24         # px (0.6 m): a row end this close to the tile border lies on the tile edge
KEEP_END = 0.30      # interior row ends: trimmed where the contrast stays below this share of the row's own (p75) ...
C_MIN = 0.02         # ... and below this absolute contrast floor (continuous ExG)
CORE_MIN = 0.04      # rows weaker than this overall are left as they are
TRIM_END = 3.0       # m, shortest interior end stretch that is removed
RUN_MIN = 2.0        # m, the kept part of a row starts / ends with at least this much continuous row pattern
PIECE_KEEP = 0.40    # a piece left beyond a cut road keeps this share of the main piece's contrast, else it goes


@lru_cache(maxsize=4)
def masks(path: str) -> tuple[np.ndarray, np.ndarray]:
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    s = img.sum(2)
    veg = (2 * img[..., 1] - img[..., 0] - img[..., 2]) / (s + 1e-6) > 0.10
    return veg, s > 0                                       # the no-data border is pure black; shadows are not


def _unit(line: LineString) -> np.ndarray:
    c = np.asarray(line.coords)
    u = c[-1] - c[0]
    return u / (np.hypot(*u) + 1e-9)


def tile_period(rows: list[LineString]) -> float:
    """Median distance (px) between neighbouring parallel rows of a tile."""
    d = []
    for i, a in enumerate(rows):
        ua, best = _unit(a), None
        n = np.array([-ua[1], ua[0]])
        ca = np.asarray(a.interpolate(0.5, normalized=True).coords[0])
        for j, b in enumerate(rows):
            if i == j or abs(ua @ _unit(b)) < math.cos(math.radians(8)):
                continue
            off = abs((np.asarray(b.interpolate(b.project(Point(ca))).coords[0]) - ca) @ n)
            if off * RES > 1.2 and (best is None or off < best):
                best = off
        if best is not None and best * RES < 4.5:
            d.append(best)
    return float(np.median(d)) if d else PERIOD_DEFAULT / RES


def _sample(mask: np.ndarray, pts: np.ndarray):
    h, w = mask.shape
    x, y = np.round(pts[..., 0]).astype(int), np.round(pts[..., 1]).astype(int)
    ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    v = np.zeros(ok.shape, bool)
    v[ok] = mask[y[ok], x[ok]]
    return v, ok


def profile(line: LineString, veg, valid, period: float):
    """Samples along a row: distance (px), on-axis vegetation share, mid-line share (nan: none), valid axis point."""
    s = np.arange(0.0, line.length + 1e-9, STEP)
    p = np.array([line.interpolate(v).coords[0] for v in s])
    u = _unit(line)
    n = np.array([-u[1], u[0]])

    def band(center, half):
        offs = center + np.arange(-half, half + 1e-9, 2.0)
        pts = p[:, None, :] + offs[None, :, None] * n[None, None, :]
        v, ok = _sample(veg, pts)
        g, _ = _sample(valid, pts)
        ok &= g
        return (v & ok).sum(1).astype(float), ok.sum(1).astype(float)

    on_v, on_n = band(0.0, ON_BAND / RES)
    a_v, a_n = band(-period / 2, MID_BAND / RES)
    b_v, b_n = band(+period / 2, MID_BAND / RES)
    ax, inside = _sample(valid, p)
    ax |= ~inside                                           # past the tile edge is not the black border
    return s, on_v, on_n, a_v + b_v, a_n + b_n, ax


def _smooth(v, n, win_m):
    k = max(1, int(round(win_m / (STEP * RES))))
    ker = np.ones(k)
    return np.convolve(v, ker, "same") / np.maximum(np.convolve(n, ker, "same"), 1)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs of True."""
    m = np.concatenate([[False], mask, [False]]).astype(int)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def on_vines(line: LineString, veg, valid, period) -> bool:
    s, on_v, on_n, mid_v, mid_n, _ = profile(line, veg, valid, period)
    if on_n.sum() == 0:
        return False
    won = on_v.sum() / on_n.sum()
    wmid = mid_v.sum() / mid_n.sum() if mid_n.sum() > 0 else 0.0
    return won / max(wmid, 0.02) >= SUP_RATIO and won - wmid >= SUP_DIFF


def _passage_parts(line: LineString, pas_px, prep_p):
    if pas_px is None or not prep_p.intersects(line):
        return []
    inside = line.intersection(pas_px)
    out = []
    for p in getattr(inside, "geoms", [inside]):
        if p.geom_type == "LineString" and p.length > 0:
            a, b = sorted((line.project(Point(p.coords[0])), line.project(Point(p.coords[-1]))))
            out.append((a, b))
    return out


def _block_at(pt: Point, blocks_px):
    hits = [i for i, b in enumerate(blocks_px) if b.contains(pt)]
    if hits:
        return hits[0]
    near = [(b.distance(pt), i) for i, b in enumerate(blocks_px)]
    d, i = min(near, default=(None, None))
    return i if d is not None and d * RES <= 1.0 else None


def fix_row(line: LineString, veg, valid, period, pas_px, prep_p, blocks_px=()):
    """(pieces, removed stretches as (a, b) px along the row, reasons Counter)."""
    L = line.length
    why = Counter()
    if L < STEP * 2:
        return [line], [], why
    s, on_v, on_n, _, _, ax = profile(line, veg, valid, period)
    bare = _smooth(on_v, on_n, 0.5) < BARE_ON
    cut = []                                                # (a, b) px to remove
    for a, b in _passage_parts(line, pas_px, prep_p):
        if (b - a) * RES > CUT_MAX:
            continue                                       # a row along a road: left alone
        end0, end1 = a <= STEP, b >= L - STEP
        if end0 or end1:                                   # the row ends on the road: remove the bare part on it
            i0, i1 = np.searchsorted(s, a), np.searchsorted(s, b)
            seg = bare[i0:max(i1, i0 + 1)]
            if len(seg) and seg.mean() >= 0.8:
                cut.append((0.0, b) if end0 else (a, L))
                why["end on a road"] += 1
            continue
        # crossing: cut only where the passage separates two blocks (the planting on each side is not connected
        # around the passage); otherwise it is a gap in the row (grass strip, missing vines) and the row runs on
        pa = line.interpolate(max(0.0, a - 1.5 / RES))
        pb = line.interpolate(min(L, b + 1.5 / RES))
        ba, bb = _block_at(pa, blocks_px), _block_at(pb, blocks_px)
        if ba is None or bb is None or ba == bb:
            why["passage inside one block (row kept whole)"] += 1
            continue
        lo, hi = max(0.0, a - SEARCH / RES), min(L, b + SEARCH / RES)   # cut on the track itself when it shows
        i0, i1 = np.searchsorted(s, lo), np.searchsorted(s, hi)
        runs = [(i0 + x, i0 + y) for x, y in _runs(bare[i0:i1])]
        runs = [(x, y) for x, y in runs if (s[min(y, len(s) - 1)] - s[x]) * RES >= GAP_MIN]
        if runs:
            x, y = max(runs, key=lambda r: r[1] - r[0])
            cut.append((s[x], s[min(y, len(s) - 1)]))
        else:
            cut.append((a, b))
        why["cut at a road between two blocks"] += 1
    # the black border outside the imagery
    for x, y in _runs(~ax):
        if (x == 0 or y == len(s)) and (y - x) * STEP * RES >= 0.5:
            cut.append((s[x], s[min(y, len(s) - 1)] if y < len(s) else L))
            why["end on no-data"] += 1
    if not cut:
        return [line], [], why
    cut.sort()
    keep, pos = [], 0.0
    for a, b in cut + [(L, L)]:
        if a > pos:
            keep.append((pos, a))
        pos = max(pos, b)
    pieces = [substring(line, a, b) for a, b in keep if (b - a) * RES >= MIN_PIECE]
    return pieces, cut, why


def nodata_px(valid: np.ndarray, f: int = 4):
    """The black border outside the imagery as polygons (px), None when the tile is fully covered."""
    if valid.all():
        return None
    import cv2
    h, w = valid.shape
    small = (~valid[: h // f * f, : w // f * f]).reshape(h // f, f, w // f, f).mean((1, 3)) > 0.5
    if small.mean() < 0.001:
        return None
    cs, _ = cv2.findContours(small.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys = [Polygon(c[:, 0, :] * f) for c in cs if len(c) >= 3]
    polys = [make_valid(q).buffer(0) for q in polys if q.area * RES * RES > 0.5]
    return unary_union(polys) if polys else None


def contrast_px(e: np.ndarray, line: LineString, period: float, win_m: float = 1.5):
    """Along a row (px): sample distances and greenness (continuous ExG) on the axis minus on the two mid-lines,
    smoothed over win_m (nan where there is no imagery)."""
    s = np.arange(0.0, line.length + 1e-9, STEP)
    p = np.array([line.interpolate(v).coords[0] for v in s])
    u = _unit(line)
    n = np.array([-u[1], u[0]])

    def band(c0, half):
        offs = c0 + np.arange(-half, half + 1e-9, 2.0)
        q = p[:, None, :] + offs[None, :, None] * n[None, None, :]
        x, y = np.round(q[..., 0]).astype(int), np.round(q[..., 1]).astype(int)
        h, w = e.shape
        ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
        v = np.full(ok.shape, np.nan, np.float32)
        v[ok] = e[y[ok], x[ok]]
        return np.nanmean(v, axis=1)

    with np.errstate(all="ignore"):
        c = band(0.0, ON_BAND / RES) - np.nanmean(np.stack([band(-period / 2, MID_BAND / RES),
                                                            band(period / 2, MID_BAND / RES)]), axis=0)
        k = max(1, int(round(win_m / (STEP * RES))))
        g = np.isfinite(c)
        ms = lambda x: np.convolve(x, np.ones(k), "full")[(k - 1) // 2:(k - 1) // 2 + len(x)]
        num, den = ms(np.where(g, c, 0.0)), ms(g.astype(float))
        return s, np.where(den > 0.5 * k, num / np.maximum(den, 1), np.nan)


def trim_interior(line: LineString, e: np.ndarray, period: float):
    """Remove the stretch at a row end INSIDE the tile where the row pattern is gone (grass verge, scrub, yard, roof).
    Ends on the tile edge are kept: there the vineyard carries on into the next tile (both reference tiles: every row
    runs to the tile edge, and trimming there cut real rows). The contrast is measured against the row itself, so a
    grassy vineyard is not trimmed for being less contrasted. Returns (line or None, metres removed)."""
    c0, c1 = np.asarray(line.coords[0]), np.asarray(line.coords[-1])
    edge = lambda q: min(q[0], 2048 - q[0], q[1], 2048 - q[1]) <= EDGE_PX
    if edge(c0) and edge(c1):
        return line, 0.0
    s, c = contrast_px(e, line, period)
    if np.isfinite(c).sum() < 20:
        return line, 0.0
    core = np.nanpercentile(c, 75)
    if core < CORE_MIN:
        return line, 0.0                                   # a weak row: its ends cannot be judged
    good = np.isfinite(c) & (c >= max(C_MIN, KEEP_END * core))
    run = int(round(RUN_MIN / (STEP * RES)))
    runs = [(a, b) for a, b in _runs(good) if b - a >= run]
    if not runs:
        return line, 0.0
    L = line.length
    t0 = s[runs[0][0]] if not edge(c0) else 0.0
    t1 = L - s[min(runs[-1][1], len(s) - 1)] if not edge(c1) else 0.0
    t0 = t0 if t0 * RES >= TRIM_END else 0.0
    t1 = t1 if t1 * RES >= TRIM_END else 0.0
    if not t0 and not t1:
        return line, 0.0
    if (L - t0 - t1) * RES < MIN_PIECE:
        return None, L * RES
    return substring(line, t0, L - t1), (t0 + t1) * RES


def _interval(g, origin, u):
    c = np.asarray(g.exterior.coords if g.geom_type == "Polygon" else g.coords)
    t = (c - origin) @ u
    return float(t.min()), float(t.max())


def clip_interrow(poly: Polygon, rows: list[LineString], period: float, tile_box):
    """The part of an inter-row where both neighbouring rows exist (None: a side has no row); `poly` if unchanged."""
    par = [r for r in rows if r.length > 0]
    if not par:
        return None
    o = np.asarray(poly.centroid.coords[0])
    near = min(par, key=lambda r: r.distance(Point(o)))
    u = _unit(near)
    par = [r for r in par if abs(_unit(r) @ u) >= math.cos(math.radians(10))]
    n = np.array([-u[1], u[0]])
    lo, hi = _interval(poly, o, u)
    best = {}
    for r in par:
        a, b = _interval(r, o, u)
        ov = min(b, hi) - max(a, lo)
        if ov <= 0:
            continue
        off = (np.asarray(r.interpolate(r.project(Point(o))).coords[0]) - o) @ n
        if not (0.2 * period <= abs(off) <= 0.9 * period):
            continue
        side = bool(off > 0)
        if side not in best or ov > best[side][0]:
            best[side] = (ov, a, b, r)
    if len(best) < 2:
        return None
    # the outline of the two rows: where both reach the tile edge nothing is lost, where they stop (a road, the imagery
    # edge) the inter-row stops with them
    hull = MultiPoint(list(best[True][3].coords) + list(best[False][3].coords)).convex_hull.buffer(CLIP_TOL / RES)
    if hull.contains(poly):
        return poly
    g = make_valid(poly.intersection(hull)).intersection(tile_box)
    parts = [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]
    if not parts:
        return None
    q = max(parts, key=lambda p: p.area)
    return poly if q.area >= poly.area - (CLIP_MIN_M2 / RES / RES) else q   # tile-corner slivers are not worth it


def _to_px(geom_utm, tile):
    """UTM geometry -> this tile's pixel frame (affine)."""
    (x0, y0), (x1, y1) = tile.utm_to_px(np.array([[0.0, 0.0], [1.0, 0.0]]))
    (x2, y2), = tile.utm_to_px(np.array([[0.0, 1.0]]))
    a, d = x1 - x0, y1 - y0
    b, e = x2 - x0, y2 - y0
    return affinity.affine_transform(geom_utm, [a, b, d, e, x0, y0])


def tidy(data: dict[str, list[dict]], tiles_dir: Path, passages_utm=None, blocks_utm=()) -> tuple[dict[str, list[dict]], Counter]:
    """blocks_utm: the block polygons (pipeline.blocks.build_blocks over the detector's rows, before any cut)."""
    st = Counter()
    out = {}
    for name, objs in data.items():
        rows_i = [k for k, o in enumerate(objs) if o["label"] == "row" and o["type"] == "polyline" and len(o["points"]) > 1]
        if not rows_i:
            out[name] = objs
            continue
        p = tiles_dir / name
        p = p if p.exists() else C.EXAMPLES / "images" / name
        tile = open_tile(p)
        veg, valid = masks(str(p))
        tile_box = box(0, 0, 2048, 2048)
        pas_px = None
        if passages_utm is not None and not passages_utm.is_empty:
            loc = passages_utm.intersection(box(*tile.bounds).buffer(5))
            pas_px = _to_px(loc, tile) if not loc.is_empty else None
        prep_p = prep(pas_px) if pas_px is not None else None
        tb = box(*tile.bounds).buffer(10)
        blocks_px = [_to_px(b, tile) for b in blocks_utm if b.intersects(tb)]
        lines = {k: LineString(objs[k]["points"]) for k in rows_i}
        period = tile_period(list(lines.values()))
        new_rows, dropped, changed = {}, [], []
        e = exg_cont(str(p))
        for k, g in lines.items():
            g0 = g
            g, removed = trim_interior(g, e, period)
            if removed:
                st["interior row ends trimmed (grass, scrub, yard)"] += 1
                st["m of row removed"] += removed
                changed.append(g0)
                if g is None:
                    st["rows dropped: no row pattern left"] += 1
                    dropped.append(g0)
                    new_rows[k] = []
                    continue
                removed_parts = [x for x in (g0.difference(g.buffer(0.5)),) if not x.is_empty]
                dropped += [x for rp in removed_parts for x in getattr(rp, "geoms", [rp]) if x.geom_type == "LineString"]
            pieces, cut, why = fix_row(g, veg, valid, period, pas_px, prep_p, blocks_px)
            st.update(why)
            if cut:
                changed.append(g)
                if len(pieces) > 1:                       # a piece beyond a road must itself be on vines, as much as its row
                    main = max(pieces, key=lambda q: q.length)
                    _, cm = contrast_px(e, main, period)
                    core = np.nanpercentile(cm, 75) if np.isfinite(cm).any() else 0.0
                    def rel_ok(q):
                        if q is main:
                            return True
                        _, cq = contrast_px(e, q, period)
                        return np.isfinite(cq).any() and np.nanmedian(cq) >= PIECE_KEEP * core
                    ok = [q for q in pieces if on_vines(q, veg, valid, period) and rel_ok(q)]
                    st["pieces not on vines dropped"] += len(pieces) - len(ok)
                    dropped += [q for q in pieces if q not in ok]
                    pieces = ok or [max(pieces, key=lambda q: q.length)]
                st["m of row removed"] += (g.length - sum(q.length for q in pieces)) * RES
            new_rows[k] = pieces
        kept = [q for ps in new_rows.values() for q in ps]
        gone = unary_union([q.buffer(CANOPY_BAND / RES, cap_style="flat") for q in dropped]) if dropped else None
        near_kept = unary_union([q.buffer(CANOPY_BAND / RES) for q in kept]) if kept else Polygon()
        touched = unary_union([q.buffer(1.5 * period) for q in changed]) if changed else None
        nodata = nodata_px(valid)
        new = []
        for k, o in enumerate(objs):
            if k in lines:
                for q in new_rows[k]:
                    new.append(dict(o, points=[[float(x), float(y)] for x, y in q.coords], attrs=dict(o["attrs"])))
                continue
            if o["label"] == "vineyard" and gone is not None and o["type"] == "polygon" and len(o["points"]) >= 3:
                c = Polygon(o["points"]).centroid
                if gone.contains(c) and not near_kept.contains(c):
                    st["canopies dropped"] += 1
                    continue
            if o["label"] == "interrow_area" and o["type"] == "polygon" and len(o["points"]) >= 3:
                g = Polygon(o["points"])
                g = g if g.is_valid else make_valid(g)
                polys = [x for x in getattr(g, "geoms", [g]) if x.geom_type == "Polygon"]
                if not polys:
                    new.append(o)
                    continue
                g = max(polys, key=lambda x: x.area)
                if nodata is not None and nodata.intersects(g):   # nothing on the black border (rule 7)
                    rest = make_valid(g.difference(nodata))
                    parts = [x for x in getattr(rest, "geoms", [rest]) if x.geom_type == "Polygon"]
                    st["inter-rows cut at the imagery edge"] += 1
                    st["m2 of inter-row removed"] += (g.area - rest.area) * RES * RES
                    if not parts:
                        continue
                    g = max(parts, key=lambda x: x.area)
                # cut where its rows were cut at a road (only next to rows that were cut)
                pieces_ir = [g]
                if touched is not None and touched.intersects(g) and pas_px is not None and prep_p.intersects(g):
                    rest = g.difference(pas_px)
                    parts = [x for x in getattr(rest, "geoms", [rest]) if x.geom_type == "Polygon" and x.area * RES * RES >= 1.0]
                    if len(parts) >= 2 and (g.area - rest.area) * RES * RES <= 30.0:
                        pieces_ir = parts
                out_parts = []
                for q in pieces_ir:
                    nq = clip_interrow(q, kept, period, tile_box)
                    if nq is None or nq.area * RES * RES < 0.5:
                        st["inter-rows dropped"] += 1
                        st["m2 of inter-row removed"] += q.area * RES * RES
                        continue
                    if nq is not q:
                        st["inter-rows clipped"] += 1
                    st["m2 of inter-row removed"] += (q.area - nq.area) * RES * RES
                    out_parts.append(nq)
                if len(out_parts) == 1 and out_parts[0] is g and g.equals_exact(Polygon(o["points"]), 1e-6):
                    new.append(o)
                    continue
                for q in out_parts:
                    new.append(dict(o, points=[[float(x), float(y)] for x, y in np.asarray(q.exterior.coords)[:-1]],
                                    attrs=dict(o["attrs"])))
                continue
            new.append(o)
        out[name] = new
    return out, st
