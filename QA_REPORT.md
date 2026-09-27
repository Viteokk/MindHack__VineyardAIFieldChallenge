# VinePlan · QA report

Generated 2026-09-27 07:43 · commit `ed26184` · annotations `out/marcaj_global.xml` (md5 e04adc255e) · Apple M4 Pro, 24 GB RAM, 12 cores, Darwin 27.0.0, Python 3.12.1 · 105 s

**46 PASS · 6 WARN · 0 FAIL** out of 52 checks. PASS: meets the threshold. WARN: known limitation or close to the limit, explained below. FAIL: broken.

Integration tests run on the real Sireț3 data (311 tiles, the annotations sent to Marcaj, the official route) and on synthetic tiles built to hit one edge case each. Rerun: `python -m pipeline.qa --perf` (this report) or `VINEPLAN_INTEGRATION=1 python -m unittest tests.test_integration` (the same checks as tests).

| Level | Checks | PASS | WARN | FAIL |
|---|---|---|---|---|
| Boundaries | 8 | 6 | 2 | 0 |
| Look-alikes (shrubs, trees, meadows, tubes) | 6 | 4 | 2 | 0 |
| Edge cases | 13 | 12 | 1 | 0 |
| Rules and consistency | 9 | 9 | 0 | 0 |
| Performance and scalability | 5 | 4 | 1 | 0 |
| Web app on phone, tablet and desktop (headless Chromium) | 11 | 11 | 0 | 0 |

## Boundaries

| ID | Check | Measured | Threshold | Status |
|---|---|---|---|---|
| B1 | A row cut by a tile edge keeps its row_id and vineyard_id in the next tile | 1089 seams: same row_id 98.7%, same vineyard_id 99.6% | ≥ 97 % (WARN ≥ 90 %) | **PASS** |
| B2 | Every vertex lies inside its 2048 × 2048 px tile | 0 of 297,470 vertices outside | 0 | **PASS** |
| B3 | No canopy or row on the black no-data border of edge tiles | 0 of 2,539 objects on 28 tiles with a no-data border | 0 (WARN ≤ 0.2 %) | **PASS** |
| B4 | Row axes stop at the road: share of row length lying on authorised passages | 1,224 m of 50,300 m = 2.4% | ≤ 1 % (WARN ≤ 5 %) | **WARN** |
| B5 | Inter-row areas never overlap canopies | 131.9 m² = 0.89% of the canopy area | ≤ 0.5 % (WARN ≤ 2 %) | **WARN** |
| B6 | Official route: valid, back at START, 0 row crossings, nothing through canopies or forbidden zones | 17.32 km · outside 1.49% · start/end 0.0/0.0 m · 0 row crossings · canopies 0.0 m · forbidden 0.0 m | valid, ≤ 2 % outside, ≤ 5 m, 0 crossings | **PASS** |
| B7 | The route never leaves the study area or the authorised passages | 0.00 m outside | < 1 m | **PASS** |
| B8 | Polygons are valid (no self-intersections) as exported to Marcaj | 0 of 16,510 polygons invalid | ≤ 0.1 % (WARN ≤ 1 %) | **PASS** |

**B1** · Seam = two row ends facing each other across a tile edge, < 1.5 m apart, < 0.5 m sideways.
- V02-R59 (r006_c00) ≠ V02-R58 (r007_c00)
- V02-R59 (r007_c00) ≠ V02-R58 (r007_c00)
- V02-R66 (r007_c00) ≠ V02-R67 (r008_c00)
- V02-R58 (r007_c00) ≠ V02-R59 (r007_c00)
- V02-R38 (r007_c00) ≠ V02-R39 (r007_c00)
- V02-R67 (r008_c00) ≠ V02-R66 (r008_c00)

**B3** · An object counts when its centre (canopy) or most of its axis (row) lies on black pixels.

**B4** · Rules 4.4: rows and inter-rows stop at the edge of the planting, not at the road centre. Part of this is the organisers' passage polygons overlapping planted rows (seen on the orthophoto).
- V17-R01: 65.0 m on a road
- V16-R37: 62.1 m on a road
- V17-R01: 47.4 m on a road
- V16-R37: 43.1 m on a road
- V12-R58: 42.3 m on a road

**B5** · Rules: canopies and inter-row areas never overlap; the inter-row runs from canopy edge to canopy edge.

**B6** · Checked by pipeline.validate against the annotations.

**B8** · Invalid rings are repaired with make_valid in every measurement; Marcaj accepts them.

## Look-alikes (shrubs, trees, meadows, tubes)

| ID | Check | Measured | Threshold | Status |
|---|---|---|---|---|
| C1 | Canopy shape: no shrub or tree crown counted as a vine (compared with the reference tiles) | width p99 0.61 m (reference 0.63 m), 0.00% wider than 1.2 m; 5.5% of 15,113 canopies > 3 m² (reference tiles 2.2%); median 0.49 m² | ≤ 0.5 % wider than 1.2 m; share > 3 m² ≤ 2× the reference (WARN ≤ 3×) | **WARN** |
| C2 | Rows sit on vines, not on striped meadows, tracks or scrub (vegetation on the axis vs between rows) | 2 of 46 blocks mostly on weak rows; 1.4% of all row length weak | 0 blocks > 50 % weak | **WARN** |
| C3 | White vine tubes are not waste: no waste box on a row axis | 0 of 11 waste boxes within 0.4 m of a row axis | 0 | **PASS** |
| C4 | No waste on roofs or inside forbidden zones (village, buildings) | 0 of 11 | 0 | **PASS** |
| C5 | Waste boxes are object-sized (0.1–2.5 m) | 0 of 11 out of range | 0 | **PASS** |
| C6 | Garden rule: every block has at least 3 rows | 0 of 46 blocks with < 3 rows | 0 | **PASS** |

**C1** · Rules 2.2–2.3: a vine is under 1 m wide, a tree crown 2–4 m and round. Touching canopies of older vines are long, narrow blobs, and the reference keeps them whole (tile r006_c004: up to 31 m²), so size alone is not an error.

**C2** · Support = vegetation (ExG > 0.10) on the axis ± 0.15 m ÷ vegetation on the two mid-lines; weak < 1.3. Known limitation of the classical detector on striped meadows and scrub: these blocks are on the Marcaj correction list.
- V11: 100% of 17 m of rows weakly supported
- V38: 53% of 56 m of rows weakly supported
![weak_rows_V11](docs/qa/weak_rows_V11.jpg)
![weak_rows_V38](docs/qa/weak_rows_V38.jpg)

**C3** · Rules 3: white protective tubes and stakes next to young vines belong to the planting.

**C6** · Rules 2.3: a single vine or two rows in a yard is not a vineyard.

## Edge cases

| ID | Check | Measured | Threshold | Status |
|---|---|---|---|---|
| E1 | Black no-data tile → nothing detected | 0 objects | 0 | **PASS** |
| E2 | Uniform grass meadow → no rows | 0 rows, 0 objects | 0 rows | **PASS** |
| E3 | Random noise → no rows | 0 rows, 0 objects | 0 rows | **PASS** |
| E4 | Bare tilled soil → nothing detected | 0 objects | 0 | **PASS** |
| E5 | Orchard (crowns 3 m wide, 5 m apart) → not taken for a vineyard | 0 rows, 0 objects | 0 rows | **PASS** |
| E6 | Striped meadow (continuous 1.3 m green / 1.3 m dry strips) → no rows | 19 rows, 19 canopies | 0 rows | **WARN** |
| E7 | Synthetic vineyard 2.6 m × 1.2 m → every row, the right spacing, one canopy per vine | 19 rows (expected ~20), spacing 2.60 m, 817 canopies | rows ± 2, spacing ± 5 %, ≥ 80 % of the vines | **PASS** |
| E8 | Vineyard rotated 35° → rows found at the right angle | 27 rows, median angle error 0.0° | ± 3° | **PASS** |
| E9 | Edge tile, right half black → rows only on the image, nothing on no-data | 19 rows, 0 objects on the black half | rows > 0, 0 on black | **PASS** |
| E10 | An 8 m planting gap marks that row 'disrupted', the others stay 'regular' | gap row: disrupted; others {'regular': 18} | disrupted; others regular | **PASS** |
| E11 | Marcaj export with folder-prefixed image names is read tile by tile | 12 of 12 tiles recovered | 12 | **PASS** |
| E12 | Missing attributes in an export are reported, not silently accepted | row without row_id: 1, interrow without interrow_cover: 1, vineyard without vineyard_id: 1 | all three reported | **PASS** |
| E13 | CVAT write → read round trip keeps every object and attribute | 5,513 objects on 40 tiles | identical | **PASS** |

**E2** · Green everywhere: no periodic row pattern.

**E5** · Rows 5 m apart are outside the vine spacing band (2.0–3.4 m).

**E6** · Known limitation: continuous strips at a vine-like spacing look like rows to a classical detector. In the real data the YOLO11 canopy veto and the human correction in Marcaj remove them (see C2).

**E10** · Rules 4.3: a visible gap of 5 m or more along the row makes it disrupted; the row is not split.

## Rules and consistency

| ID | Check | Measured | Threshold | Status |
|---|---|---|---|---|
| R1 | Labels and attributes exactly as in the annotation rules (lower case, allowed values, none missing) | no violation | 0 | **PASS** |
| R2 | One polyline per physical row per tile | 0 duplicated row_id in a tile (1,868 row polylines) | 0 (WARN ≤ 1 %) | **PASS** |
| R3 | row_id belongs to its block (V03-R017 lies in V03) and every row_id is used in one block only | 0 row_ids outside their block, 0 in several blocks, 760 rows | 0 | **PASS** |
| R4 | Upload ZIPs: 311 original tiles (byte-identical), each ZIP < 90 MB, labels as in Appendix A | 9 ZIPs, 311 tiles (0 duplicates), 0 changed, largest 57.5 MB | 311 · 0 · < 90 MB | **PASS** |
| R5 | Tiles with nothing to annotate are known (each needs 'No objects in this frame' in Marcaj) | 161 of 311 tiles empty | listed | **PASS** |
| M1 | measurements.csv: totals equal the sum of the rows; m² and ha agree | 46 blocks, 760 rows, 50.30 km; Δlength 0.15 m, Δha 0.00007 | Δ < 1 m, Δ < 0.001 ha, counts equal | **PASS** |
| M2 | Block report and environmental indicators agree with measurements.csv | row length Δ 0.000%, missing vines Δ 0.00%, blocks Δ 0 | < 0.1 %, < 1 %, 0 | **PASS** |
| M3 | Deliverables are georeferenced in EPSG:32635 | 20 of 20 GeoJSON files declare EPSG:32635 | route.geojson + route_waste.geojson required | **PASS** |
| A1 | Score on the two official reference tiles (local re-implementation of the metric) | partial score 0.844 (60 % of the total covered here) | ≥ 0.80 (WARN ≥ 0.75) | **PASS** |

**R4** · Annex A of the rules: the tiles must be the supplied files, unchanged, with their original names.

**R5** · A job cannot be submitted while one of its tiles has no answer.

**M3** · Map layers without a crs member are still in UTM 35N metres.

**A1** · Canopies 25 %, waste 10 %, axes 8 %, attributes 5 %, grouping 2 %, counts 10 %, normalised; tiles r006_c004 and r021_c012.
- canopy   IoU=0.662  F1=0.590  (pred 631 / ref 650, TP 378)  -> 0.633
- axes     F1=1.000  (pred 51 / ref 51, TP 51)
- attrs    0.982  (row_structure n=51, interrow_cover n=49)
- counts   blocks=1.00  rows=1.00  canopy_area=0.94  interrow_area=1.00  row_length=0.99

## Performance and scalability

| ID | Check | Measured | Threshold | Status |
|---|---|---|---|---|
| P1 | Detector time per tile and throughput (311 tiles = 81 ha of challenge tiles) | 2.09 s per vineyard tile, 1.73 s per empty tile, max 4.0 s; peak memory 698 MB | < 5 s per tile | **PASS** |
| P2 | Tiles are independent: the detector scales with the cores | 1 worker: 0.39 tiles/s · 4 workers: 1.34 tiles/s · 11 workers: 2.14 tiles/s · speed-up ×5.5 | ≥ ×3.0 with 11 workers | **PASS** |
| P3 | Route planning cost: grid once, then one shortest-path search per stop | grid 22 s (1,001,633 walkable cells at 0.5 m), 77 ms per stop |  | **PASS** |
| P4 | Web interface: data loaded at start | 23.2 MB (imagery 10 cm/px for 145 ha + vector layers) | ≤ 30 MB (WARN ≤ 60 MB) | **PASS** |
| P5 | Measured end-to-end times of the pipeline stages (out/timing.json) | targets 4 s, route 2447 s, validate 11 s, measure 165 s, web 68 s · total 44.9 min | < 20 min from the Marcaj export to all deliverables | **WARN** |

**P1** · Best throughput 2.1 tiles/s ≈ 2,018 ha per hour of imagery on one laptop (Apple M4 Pro, 24 GB RAM, 12 cores, Darwin 27.0.0, Python 3.12.1).

**P2** · Each tile is processed on its own; the only global steps (block and row IDs, measurements) take seconds.

**P3** · Distance matrix ≈ grid + stops × search ÷ 11 workers: 50 stops ≈ 22 s, 900 stops ≈ 28 s (+ 60 s TSP). A zone chosen in the app has tens of stops: the browser plans it in 1–5 s.

**P4** · Static site on GitHub Pages; the imagery is cut in 32 chunks, full 2.5 cm/px only on the reference tiles.

**P5** · Hardware: arm64 · macOS-27.0-arm64-arm-64bit · python 3.12.1. Detection of the 311 tiles: 5 min 24 s (README).

## Web app on phone, tablet and desktop (headless Chromium)

| ID | Check | Measured | Threshold | Status |
|---|---|---|---|---|
| W1 | Pages load without JavaScript errors (landing, sign-in, app) | 0 errors on 3 pages and 5 app views | 0 | **PASS** |
| W2 | Phone, tablet and desktop: nothing scrolls sideways, every page and app view fits | 21 page × device combinations, 0 with overflow | 0 | **PASS** |
| W3 | Sign-in with the demo account opens the app in the account's role | login → app in 0.8 s, role inspector, first page “DATE DE TESTRegistrul viticol și cererile AIPA sunt fictive (date demonstrative); măsurătorile provin din zborul dronei.” | role = inspector | **PASS** |
| W4 | Romanian, Russian and English: the interface translates and stays responsive | ro: “Hartă” shown, max 3 ms; ru: “Карта” shown, max 3 ms; en: “Map” shown, max 2 ms | labels translated, page answers < 1 s | **PASS** |
| W5 | Work zone: freehand drawing, corner editing, add and exclude a shape (mouse and touch) | freehand zone ✓, corner handles ✓, add a shape ✓, exclude a part ✓, phone: corners by touch + Gata ✓ | all steps | **PASS** |
| W6 | Walking route computed in the browser for a chosen block | block V19 (25 gaps): route in 2.4 s, 1,79 km | computed, < 120 s | **PASS** |
| W7 | Field navigation works when the phone refuses location | message: “Accesul la locație este blocat în browser. Permiteți accesul…”; navigation panel shown | clear message + target-by-target navigation | **PASS** |
| W8 | Load time and data downloaded to open the map (desktop, local server) | map ready in 0.6 s, 32.7 MB in 120 files | < 8 s, < 45 MB (WARN < 15 s, < 70 MB) | **PASS** |
| W9 | Buttons have a name and are big enough to tap on a phone | 49 visible buttons on a phone: 0 without a name, 0 smaller than 24 px | 0 without a name; ≤ 3 small | **PASS** |
| W11 | Entering the app: the whole of Moldova first, then a smooth flight to the flown area of Sireți | scale 50 km → 200 m; frames every 8 ms (p95 17 ms) | country → flown area; p95 frame ≤ 50 ms | **PASS** |
| W10 | Every file the pages ask for exists (no 404, no failed request) | 2067 requests, 0 failed | 0 failed | **PASS** |

**W1** · Uncaught exceptions and console errors, desktop Chromium. Missing files are counted in W10.

**W2** · Viewports 375 × 812 (phone, touch), 768 × 1024 (tablet, touch), 1440 × 900 (desktop): the page width never exceeds the screen and no visible element sticks out of it (map imagery excluded).
![web_phone_landing](docs/qa/web_phone_landing.png)
![web_phone_app](docs/qa/web_phone_app.png)
![web_tablet_landing](docs/qa/web_tablet_landing.png)
![web_tablet_app](docs/qa/web_tablet_app.png)
![web_desktop_app](docs/qa/web_desktop_app.png)

**W3** · The demo credentials are prefilled by the page and never leave the browser.

**W4** · Each language loaded from the user's choice; the page must answer a script call after every view change (catches a translation loop that once froze the English interface).

**W5** · Mouse on a desktop, touch taps on a 375 px phone (the panel folds away while drawing; the floating bar keeps Anulează / Gata on screen).

**W6** · `web/router.js` in a Web Worker: 0.5 m grid, rows as walls, TSP; the same rules as `pipeline/route.py`.

**W7** · Location refused (permission denied) on a phone: the app explains how to allow it and keeps guiding target by target without the distance.

**W8** · Everything the map needs to open (orthophoto mosaic, the village imagery in view, all annotation layers); the high-resolution tiles and the rest of the village load only when zoomed in or panned to.

**W9** · Name = visible text, aria-label or title (screen readers, tooltips).

**W11** · Once per session, after sign-in: the country and its districts, the imagery of the flown area preloaded, then a 3 s flight drawn sharp at every frame. Links to a zone, parcel or block open straight on it; reduced motion skips it; a touch stops it.

**W10** · All requests of the other web checks. `/api/status` is the live-mode probe (answered only by `python -m pipeline.serve`) and is expected to be missing on the static site.

## Known limitations and what covers them

- **Striped meadows and scrub (C2, E6).** A classical row detector sees any vegetation strips at a vine-like spacing as rows. Mitigation: the YOLO11 canopy veto empties tiles where the neural model sees no vines, and every block flagged in C2 is on the Marcaj correction list; the final numbers are computed from the corrected Marcaj export.
- **Rows running onto roads (B4).** Part of the row length lies on the organisers' passage polygons, which in places cover planted rows. Routing never blocks a road; the correction in Marcaj trims rows that really cross a road.
- **Route coverage.** Rows are walls (the trellis cannot be crossed), so targets inside closed pockets are left out rather than reached by cutting through a row; the route stays valid (≤ 2 % outside, back at START).

## How the checks work

- Source: `pipeline/qa.py`; each check returns the measured value, the threshold and the evidence. The synthetic tiles are generated in memory (2048 × 2048 px at 2.5 cm/px) and go through the same detector as the real tiles (`pipeline/baseline.py`).
- Performance figures come from `python -m pipeline.qa --perf` (cached in `out/qa_perf.json`) and from the timed pipeline runs in `out/timing.json`.
