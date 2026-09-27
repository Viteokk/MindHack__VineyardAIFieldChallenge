"""Row ends at roads and at the imagery edge (pipeline.row_ends) and row linking across tile seams (pipeline.blocks).

Synthetic masks in pixels (2.5 cm): vine rows are vegetation stripes along x, 2.5 m apart.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon, box
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import row_ends as R  # noqa: E402
from pipeline.blocks import link_rows  # noqa: E402

N = 800                      # 20 m
PERIOD = 100.0               # px = 2.5 m


def vineyard(track_x=None, black_x=None):
    """Vine rows at y = 150, 250, ... (a plant 0.5 m long every 1.2 m), optional bare track at x in track_x."""
    veg = np.zeros((N, N), bool)
    for y in range(150, N - 100, 100):
        for x0 in range(0, N, 48):
            veg[y - 10:y + 10, x0:x0 + 20] = True
    if track_x:
        veg[:, track_x[0]:track_x[1]] = False
    valid = np.ones((N, N), bool)
    if black_x is not None:
        valid[:, black_x:] = False
        veg[:, black_x:] = False
    return veg, valid


class RoadCrossing(unittest.TestCase):
    row = LineString([(0, 250), (N - 1, 250)])
    passage = box(380, 0, 500, N)                       # 3 m wide road across the rows

    def fix(self, veg, valid, blocks):
        return R.fix_row(self.row, veg, valid, PERIOD, self.passage, prep(self.passage), blocks)

    def test_road_between_two_blocks_cuts_the_row_on_the_track(self):
        veg, valid = vineyard(track_x=(390, 490))
        pieces, cut, why = self.fix(veg, valid, [box(0, 0, 380, N), box(500, 0, N, N)])
        self.assertEqual(len(pieces), 2)
        self.assertLessEqual(pieces[0].bounds[2], 405)             # the track starts at 390, the 0.5 m smoothing
        self.assertGreater(pieces[1].bounds[0], 480)

    def test_passage_inside_one_block_is_a_gap_not_a_cut(self):
        veg, valid = vineyard(track_x=(390, 490))       # the same bare stretch, but the planting is connected
        pieces, cut, why = self.fix(veg, valid, [box(0, 0, N, N)])
        self.assertEqual(len(pieces), 1)
        self.assertAlmostEqual(pieces[0].length, self.row.length)

    def test_row_ending_on_a_bare_road_is_trimmed(self):
        veg, valid = vineyard(track_x=(700, N))
        row = LineString([(0, 250), (N - 1, 250)])
        road = box(700, 0, N + 50, N)
        pieces, cut, why = R.fix_row(row, veg, valid, PERIOD, road, prep(road), [box(0, 0, N, N)])
        self.assertEqual(len(pieces), 1)
        self.assertLessEqual(pieces[0].bounds[2], 701)

    def test_row_ending_on_the_black_border_is_trimmed(self):
        veg, valid = vineyard(black_x=600)
        pieces, cut, why = R.fix_row(self.row, veg, valid, PERIOD, None, None, [])
        self.assertEqual(len(pieces), 1)
        self.assertLessEqual(pieces[0].bounds[2], 601)

    def test_row_running_to_the_tile_edge_is_kept(self):
        veg, valid = vineyard()
        pieces, cut, why = R.fix_row(self.row, veg, valid, PERIOD, None, None, [])
        self.assertEqual(cut, [])

    def test_shadows_are_not_no_data(self):
        img = np.full((4, 4, 3), 10, np.uint8)          # dark shadow: sum 30 > 0
        self.assertTrue((img.astype(np.float32).sum(2) > 0).all())


class InterRows(unittest.TestCase):
    tile = box(0, 0, N, N)

    def test_inter_row_stops_where_its_rows_stop(self):
        rows = [LineString([(0, 250), (500, 250)]), LineString([(0, 350), (500, 350)])]
        ir = Polygon([(0, 262), (N, 262), (N, 338), (0, 338)])
        out = R.clip_interrow(ir, rows, PERIOD, self.tile)
        self.assertIsNotNone(out)
        self.assertLessEqual(out.bounds[2], 500 + R.CLIP_TOL / R.RES + 1)

    def test_inter_row_between_rows_to_the_tile_edge_is_unchanged(self):
        rows = [LineString([(0, 250), (N, 250)]), LineString([(0, 350), (N, 350)])]
        ir = Polygon([(0, 262), (N, 262), (N, 338), (0, 338)])
        self.assertIs(R.clip_interrow(ir, rows, PERIOD, self.tile), ir)

    def test_inter_row_outside_the_outermost_row_is_dropped(self):
        rows = [LineString([(0, 250), (N, 250)])]
        ir = Polygon([(0, 262), (N, 262), (N, 338), (0, 338)])
        self.assertIsNone(R.clip_interrow(ir, rows, PERIOD, self.tile))

    def test_no_data_polygon_is_found_only_for_black(self):
        valid = np.ones((256, 256), bool)
        self.assertIsNone(R.nodata_px(valid))
        valid[:, 128:] = False
        g = R.nodata_px(valid)
        self.assertGreater(g.area, 0.4 * 256 * 256)


class LinkAcrossSeams(unittest.TestCase):
    def test_short_corner_piece_between_two_long_pieces_keeps_the_row_id(self):
        segs = [LineString([(0, 0), (50, 0)]), LineString([(50.2, 0.05), (52.2, 0.05)]),
                LineString([(52.4, 0.1), (100, 0.1)])]
        ids = link_rows(segs, ["a", "b", "c"])
        self.assertEqual(len(set(ids)), 1)

    def test_touching_ends_link_even_at_ten_degrees(self):
        a = LineString([(0, 0), (50, 0)])
        ang = np.radians(10)
        b = LineString([(50.3, 0.0), (50.3 + 20 * np.cos(ang), 20 * np.sin(ang))])
        self.assertEqual(len(set(link_rows([a, b], ["a", "b"]))), 1)

    def test_neighbouring_rows_stay_apart(self):
        segs = [LineString([(0, 0), (50, 0)]), LineString([(50.2, 2.5), (100, 2.5)])]
        self.assertEqual(len(set(link_rows(segs, ["a", "b"]))), 2)


if __name__ == "__main__":
    unittest.main()


class InteriorEnds(unittest.TestCase):
    """Row ends inside a tile stop where the row pattern stops; ends on the tile edge are never trimmed."""

    def exg(self, vines_to):
        e = np.full((N, N), 0.02, np.float32)              # bare soil
        for y in range(150, N - 100, 100):
            e[y - 10:y + 10, :vines_to] = 0.25              # vine band greener than the mid-lines
        e[:, vines_to:] = 0.20                              # beyond: uniform grass (as green on the axis as between)
        return e

    def test_interior_end_on_grass_is_trimmed(self):
        e = self.exg(400)
        row = LineString([(100, 250), (700, 250)])           # both ends inside the tile (the test tile is 800 px wide)
        out, removed = R.trim_interior(row, e, PERIOD)
        self.assertIsNotNone(out)
        self.assertLess(out.bounds[2], 460)
        self.assertGreater(removed, 5.0)

    def test_end_on_the_tile_edge_is_kept(self):
        e = self.exg(400)
        row = LineString([(100, 250), (2047, 250)])          # reaches the tile edge
        e2 = np.pad(e, ((0, 0), (0, 2048 - N)), mode="edge")
        out, removed = R.trim_interior(row, e2, PERIOD)
        self.assertEqual(removed, 0.0)


class WindowSearch(unittest.TestCase):
    """Pieces of one row found by overlapping windows are merged, and the number of windows is kept (consensus)."""

    def test_pieces_of_one_row_merge_with_their_window_count(self):
        from pipeline.window_detect import _merge
        per = 2.6 / 0.025
        pieces = [(LineString([(0, 100), (400, 100)]), 1), (LineString([(300, 108), (700, 108)]), 2),   # same row, 0.2 m apart
                  (LineString([(0, 100 + per), (500, 100 + per)]), 1)]                                  # the next row, shorter
        out = _merge(pieces, per)
        self.assertEqual(len(out), 2)
        long = max(out, key=lambda r: r[0].length)
        self.assertEqual(long[1], 2)
        self.assertGreater(long[0].length, 690)

    def test_buildings_are_found_on_roofs_not_on_soil(self):
        from pipeline.window_detect import buildings_px
        img = np.zeros((2048, 2048, 3), np.uint8)
        img[:] = (170, 150, 120)                           # beige soil
        img[200:600, 200:700] = (150, 150, 152)            # a grey roof, 10 x 12.5 m
        img[1200:1500, 1200:1600] = (190, 70, 60)          # a red-tile roof
        g = buildings_px(img)
        self.assertGreater(g.area * 0.025 ** 2, 150)
        self.assertTrue(g.contains(Polygon([(250, 250), (650, 250), (650, 550)]).centroid))
        soil = np.zeros((2048, 2048, 3), np.uint8)
        soil[:] = (170, 150, 120)
        self.assertTrue(buildings_px(soil).is_empty)
