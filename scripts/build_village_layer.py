"""Background layer for the web map: the whole Sireț3 flight (village included), not only the 311 challenge tiles.

Reads the organisers' full source orthomosaic (04_source/siret3_source_orthomosaic_EPSG4326.tif, WGS84, 2.4 cm/px, with
internal overviews), takes the overview level closest to the requested resolution, reprojects it to EPSG:32635 (the
map's frame) and writes WebP chunks with a transparent no-data border to web/data/village/, plus
web/data/village_index.json ({res, chunks: [{img, bounds: [minx, miny, maxx, maxy]}]}). The web app draws them under
the challenge mosaic and loads a chunk only when it comes into view.

Usage:  python scripts/build_village_layer.py [--src PATH] [--res 0.2] [--chunk 1024]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import tifffile
from PIL import Image
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

SRC = C.ROOT.parent / "04_source" / "siret3_source_orthomosaic_EPSG4326.tif"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--res", type=float, default=0.2, help="output metres per pixel")
    ap.add_argument("--chunk", type=int, default=1024, help="chunk size in pixels")
    ap.add_argument("--out", default=str(C.ROOT / "web" / "data" / "village"))
    ap.add_argument("--quality", type=int, default=70)
    a = ap.parse_args()
    t0 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.webp"):
        f.unlink()
    with tifffile.TiffFile(a.src) as tf:
        base = tf.pages[0]
        sx, sy = base.tags["ModelPixelScaleTag"].value[:2]
        lon0, lat0 = base.tags["ModelTiepointTag"].value[3:5]
        H0, W0 = base.shape[:2]
        levels = tf.series[0].levels
        m_per_deg = 111320.0
        base_res = sy * m_per_deg                           # metres per pixel (north-south)
        k = max(i for i in range(len(levels)) if base_res * (H0 / levels[i].shape[0]) <= a.res * 1.05) \
            if any(base_res * (H0 / lv.shape[0]) <= a.res * 1.05 for lv in levels) else 0
        lvl = levels[k]
        print(f"source {W0}x{H0} px, {base_res * 100:.1f} cm/px; using overview {k}: {lvl.shape[1]}x{lvl.shape[0]} "
              f"({base_res * H0 / lvl.shape[0] * 100:.1f} cm/px)", flush=True)
        img = lvl.asarray()
    h, w = img.shape[:2]
    sxk, syk = sx * W0 / w, sy * H0 / h                     # degrees per pixel at this level
    to_utm = Transformer.from_crs(4326, 32635, always_xy=True)
    to_geo = Transformer.from_crs(32635, 4326, always_xy=True)
    cx = [lon0, lon0 + w * sxk]
    cy = [lat0 - h * syk, lat0]
    xs, ys = to_utm.transform([cx[0], cx[1], cx[0], cx[1]], [cy[0], cy[0], cy[1], cy[1]])
    X0, X1 = np.floor(min(xs)), np.ceil(max(xs))
    Y0, Y1 = np.floor(min(ys)), np.ceil(max(ys))
    step = a.chunk * a.res
    chunks = []
    ny = int(np.ceil((Y1 - Y0) / step))
    nx = int(np.ceil((X1 - X0) / step))
    for j in range(ny):
        for i in range(nx):
            bx0, by1 = X0 + i * step, Y1 - j * step
            bx1, by0 = bx0 + step, by1 - step
            u = bx0 + (np.arange(a.chunk) + 0.5) * a.res
            v = by1 - (np.arange(a.chunk) + 0.5) * a.res
            uu, vv = np.meshgrid(u, v)
            lon, lat = to_geo.transform(uu.ravel(), vv.ravel())
            mapx = ((np.asarray(lon) - lon0) / sxk - 0.5).reshape(uu.shape).astype(np.float32)
            mapy = ((lat0 - np.asarray(lat)) / syk - 0.5).reshape(uu.shape).astype(np.float32)
            if mapx.max() < 0 or mapy.max() < 0 or mapx.min() > w - 1 or mapy.min() > h - 1:
                continue
            rgb = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            alpha = (rgb.astype(np.int16).sum(2) > 0).astype(np.uint8) * 255
            alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8))                     # no dark fringe at the edge
            if alpha.mean() < 0.5:
                continue
            name = f"v_{j:02d}_{i:02d}.webp"
            Image.fromarray(np.dstack([rgb, alpha])).save(out / name, "WEBP", quality=a.quality, method=4)
            chunks.append({"img": f"data/village/{name}", "bounds": [round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2)]})
    idx = {"res": a.res, "source": Path(a.src).name, "extent": [X0, Y0, X1, Y1], "chunks": chunks}
    (C.ROOT / "web" / "data" / "village_index.json").write_text(json.dumps(idx))
    size = sum(f.stat().st_size for f in out.glob("*.webp")) / 1e6
    print(f"{len(chunks)} chunks, {size:.1f} MB, extent UTM {X0:.0f},{Y0:.0f} -> {X1:.0f},{Y1:.0f} "
          f"({(X1 - X0) / 1000:.2f} x {(Y1 - Y0) / 1000:.2f} km) in {time.time() - t0:.0f} s -> {out}")


if __name__ == "__main__":
    main()
