"""Compare two versions of the pre-annotations before replacing the upload set: is the new one better, not worse?

  python scripts/compare_versions.py OLD.xml NEW.xml [--sheet out/compare.jpg] [--top 12]

Prints, for both versions: the score on the two official reference tiles (pipeline.eval, the jury's formulas), the key
QA checks (tile seams, vertices inside the tile, no-data border, valid polygons, canopy shape, garden rule, labels,
one polyline per row, row_id in its block), object counts, blocks / rows / row length; then which tiles changed and a
before / after contact sheet of the most changed tiles (rows yellow, canopies magenta, inter-rows cyan) to look at.
A new version is accepted only if the reference score does not drop and no QA check gets worse.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat  # noqa: E402
from pipeline.tiles import all_tiles  # noqa: E402

PY = sys.executable
CHECKS = ["B1", "B2", "B3", "B8", "C1", "C6", "R1", "R2", "R3"]
RANK = {"PASS": 2, "WARN": 1, "FAIL": 0}


def reference_score(xml: Path) -> float:
    out = subprocess.run([PY, "-m", "pipeline.eval", "--pred", str(xml)], cwd=C.ROOT, capture_output=True, text=True).stdout
    m = re.search(r"PARTIAL SCORE.*?([\d.]+)\s*$", out.strip().splitlines()[-1])
    return float(m.group(1)) if m else float("nan")


def qa(xml: Path) -> dict:
    subprocess.run([PY, "-W", "ignore", "-m", "pipeline.qa", "--inp", str(xml), "--no-images", "--only", *CHECKS],
                   cwd=C.ROOT, capture_output=True, text=True)
    return {c["id"]: (c["status"], c["measured"]) for c in json.loads((C.OUT / "qa_report.json").read_text())}


def stats(data, tiles) -> dict:
    lab = Counter(o["label"] for objs in data.values() for o in objs)
    rows = [(n, o) for n, objs in data.items() for o in objs if o["label"] == "row"]
    length = sum(LineString(tiles[n].px_to_utm(o["points"])).length for n, o in rows)
    return {"canopies": lab["vineyard"], "rows (polylines)": lab["row"], "inter-rows": lab["interrow_area"],
            "waste": lab["waste"], "blocks": len({o["attrs"].get("vineyard_id") for _, o in rows}),
            "rows (row_id)": len({o["attrs"].get("row_id") for _, o in rows}), "row length km": round(length / 1000, 2)}


def tile_change(a, b, n) -> float:
    """Metres of row axis that differ between two versions of a tile (symmetric difference, 0.5 m tolerance)."""
    la = [LineString(o["points"]) for o in a.get(n, []) if o["label"] == "row"]
    lb = [LineString(o["points"]) for o in b.get(n, []) if o["label"] == "row"]
    if not la and not lb:
        return 0.0
    from shapely.ops import unary_union
    ua = unary_union([g.buffer(20) for g in la]) if la else Polygon()
    ub = unary_union([g.buffer(20) for g in lb]) if lb else Polygon()
    gone = sum(g.difference(ub).length for g in la)
    new = sum(g.difference(ua).length for g in lb)
    return (gone + new) * 0.025


def sheet(a, b, names, tiles, out: Path, S=380):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (2 * S + 4, len(names) * (S + 4)), (255, 255, 255))
    dd = ImageDraw.Draw(img)
    for j, n in enumerate(names):
        base = Image.fromarray(tiles[n].read()[..., :3])
        for k, data in enumerate((a, b)):
            im = base.copy()
            d = ImageDraw.Draw(im, "RGBA")
            for o in data.get(n, []):
                if o["type"] == "box":
                    d.rectangle([o["xtl"], o["ytl"], o["xbr"], o["ybr"]], outline=(255, 80, 0, 255), width=6)
                    continue
                pts = [tuple(p) for p in o["points"]]
                if o["label"] == "row":
                    d.line(pts, fill=(255, 225, 40, 255), width=6)
                elif o["label"] == "vineyard" and len(pts) > 2:
                    d.polygon(pts, outline=(255, 60, 220, 255))
                elif o["label"] == "interrow_area" and len(pts) > 2:
                    d.polygon(pts, outline=(0, 230, 255, 255))
            img.paste(im.resize((S, S)), (k * (S + 4), j * (S + 4)))
        dd.rectangle([0, j * (S + 4), 230, j * (S + 4) + 14], fill=(0, 0, 0))
        dd.text((3, j * (S + 4) + 2), f"{n[7:16]}   old | new", fill=(255, 255, 0))
    img.save(out, quality=82)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--sheet", default=str(C.OUT / "compare.jpg"))
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    tiles = {t.name: t for t in all_tiles(C.TILES)}
    old, new = read_cvat(a.old), read_cvat(a.new)
    so, sn = reference_score(Path(a.old)), reference_score(Path(a.new))
    qo, qn = qa(Path(a.old)), qa(Path(a.new))
    to, tn = stats(old, tiles), stats(new, tiles)
    print(f"{'':28s} {'OLD':>12s} {'NEW':>12s}")
    print(f"{'reference score (2 tiles)':28s} {so:12.3f} {sn:12.3f}")
    worse = []
    for c in CHECKS:
        (s1, m1), (s2, m2) = qo.get(c, ("?", "")), qn.get(c, ("?", ""))
        flag = "  <-- worse" if RANK.get(s2, 0) < RANK.get(s1, 0) else ""
        if flag:
            worse.append(c)
        print(f"{c:4s} {s1:>5s} -> {s2:<5s}  old: {m1[:70]}\n{'':17s}new: {m2[:70]}{flag}")
    for k in to:
        print(f"{k:28s} {to[k]:>12} {tn[k]:>12}")
    changed = sorted(((tile_change(old, new, n), n) for n in tiles), reverse=True)
    changed = [(m, n) for m, n in changed if m > 1.0]
    print(f"tiles with changed rows: {len(changed)}; most changed: " + ", ".join(f"{n[7:16]} ({m:.0f} m)" for m, n in changed[:10]))
    sheet(old, new, [n for _, n in changed[:a.top]], tiles, Path(a.sheet))
    verdict = "NEW is not worse" if sn >= so - 1e-9 and not worse else "NEW is WORSE: keep OLD"
    print(f"verdict: {verdict} (reference {so:.3f} -> {sn:.3f}; QA checks worse: {worse or 'none'}); sheet: {a.sheet}")


if __name__ == "__main__":
    main()
