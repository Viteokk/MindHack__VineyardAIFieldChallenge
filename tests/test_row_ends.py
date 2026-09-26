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
