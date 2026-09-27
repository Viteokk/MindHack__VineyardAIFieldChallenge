"""Must-visit points: bring route.geojson within REACH m of each given point with a short out-and-back detour.

Used for the organisers' pre-test point (X 629663.8, Y 5220195.3, EPSG:32635): the inspector loop passed 2.87 m from
it and a target counts as visited within 2 m. The detour leaves the route at its nearest point, walks straight towards
the point until REACH m from it and comes back. It is reported with the metres it adds outside the passable inter-rows
and authorised passages (same test as pipeline/validate.py), so the 2 % rule can be re-checked right after.

    python scripts/visit_points.py --point 629663.8 5220195.3                      # patches route.geojson in place
    python -m pipeline.validate --route route.geojson --inp out/pre_global_v5.xml --targets web/data/targets_inspector.geojson
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

REACH = 1.5  # m from the point: inside the 2 m visit radius with a margin


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default=str(C.ROOT / "route.geojson"))
    ap.add_argument("--inp", default=str(C.OUT / "pre_global_v5.xml"), help="annotations giving the inter-rows")
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--point", nargs=2, type=float, action="append", required=True, metavar=("X", "Y"))
    ap.add_argument("--out", default="", help="write here instead of patching --route in place")
    a = ap.parse_args()

    fc = json.loads(Path(a.route).read_text())
    feat = fc["features"][0]
    coords = [tuple(c[:2]) for c in feat["geometry"]["coordinates"]]
    layers, _ = convert(Path(a.inp), Path(a.tiles))
    inter = unary_union([shape(f["geometry"]) for f in layers.get("interrows", [])])
    passages = unary_union([shape(f["geometry"]) for f in json.loads((C.ROUTE_IN / "passages.geojson").read_text())["features"]])
    allowed = unary_union([inter, passages]).buffer(0.01)

    for x, y in a.point:
        P, line = Point(x, y), LineString(coords)
        d = line.distance(P)
        if d <= REACH:
            print(f"point ({x}, {y}): already {d:.2f} m from the route, nothing to do")
            continue
        s = line.project(P)
        Q = line.interpolate(s)
        t = (d - REACH) / d
        D = (Q.x + (P.x - Q.x) * t, Q.y + (P.y - Q.y) * t)
        leg = LineString([(Q.x, Q.y), D])
        cum, k = 0.0, len(coords) - 2
        for i in range(len(coords) - 1):
            seg = LineString(coords[i:i + 2]).length
            if cum + seg >= s:
                k = i
                break
            cum += seg
        coords = coords[:k + 1] + [(Q.x, Q.y), D, (Q.x, Q.y)] + coords[k + 1:]
        print(f"point ({x}, {y}): route was {d:.2f} m away -> detour of {2 * leg.length:.2f} m brings it to {REACH} m; "
              f"{2 * leg.difference(allowed).length:.2f} m of the detour outside the passable area")

    line = LineString(coords)
    feat["geometry"]["coordinates"] = [[round(c[0], 3), round(c[1], 3)] for c in coords]
    feat["properties"]["length_m"] = round(line.length, 1)
    feat["properties"]["must_visit"] = [[x, y] for x, y in a.point]
    Path(a.out or a.route).write_text(json.dumps(fc))
    print(f"length_m {feat['properties']['length_m']} -> {a.out or a.route}")


if __name__ == "__main__":
    main()
