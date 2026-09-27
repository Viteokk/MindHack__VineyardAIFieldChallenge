# VinePlan (MindHack__VineyardAIFieldChallenge) — Vineyard AI Field Challenge (DeepTech GigaHack 2026 · Marcaj)

[![docker](https://github.com/Viteokk/MindHack__VineyardAIFieldChallenge/actions/workflows/docker.yml/badge.svg)](https://github.com/Viteokk/MindHack__VineyardAIFieldChallenge/actions/workflows/docker.yml)

End-to-end pipeline for the Sireț3 UAV orthomosaic (311 GeoTIFF tiles, 2.5 cm/px, EPSG:32635):
AI pre-annotations (canopies, row axes, inter-row areas, attributes) → manual correction in Marcaj →
global block / row IDs → measurements → two walking routes → interactive web map.

| Deliverable | Where |
|---|---|
| Walking route, inspector (row gaps + waste) | [`route.geojson`](route.geojson) — one LineString, EPSG:32635, `length_m` |
| Walking route, farmer (waste only) | [`route_waste.geojson`](route_waste.geojson) |
| Measurements by `vineyard_id` / `row_id` | [`measurements.csv`](measurements.csv) |
| Web interface | **https://viteokk.github.io/MindHack__VineyardAIFieldChallenge/** (GitHub Pages from `web/`, branch `gh-pages`) · local: `python -m http.server -d web 8000` |
| Model weights | [yolo11n-seg-vineyard-waste.pt (release v0.2-weights)](https://github.com/Viteokk/MindHack__VineyardAIFieldChallenge/releases/tag/v0.2-weights) · earlier canopy-only [v0.1-weights](https://github.com/Viteokk/MindHack__VineyardAIFieldChallenge/releases/tag/v0.1-weights) |
| Pre-annotations uploaded to Marcaj | `out/upload_v5/*.zip` (detector v3 + window search + row continuation / row ends, CVAT for images 1.1, built by `pipeline/export_cvat.py`) — Marcaj project “Team Victor Istrati (v2)” after the organisers' one-time reset; earlier sets kept in `out/upload_v3/`, `out/upload_v4/` and `out/upload/` (v1) |

## Architecture

```
 311 GeoTIFF tiles ──► detect (pipeline.baseline: ExG → row grid → canopies / inter-rows / attributes,
                       vine / non-vine + forbidden filters; optional pipeline.infer_yolo)
                   ──► blocks (rows stop at roads / imagery edge → global vineyard_id / row_id across tiles)
                   ──► export_cvat (Marcaj ZIPs) ──► human correction in Marcaj ──► export ──┐
                   ──► targets (row gaps ≥ 3 m, waste) ──► route ×2 (grid graph + TSP) ──► validate  │
                   ──► measurements.csv                                                              │
                   ──► web/data (mosaic + GeoJSON) ──► web/index.html (Leaflet, static)  ◄───────────┘ (Sunday recompute)
```
Every arrow is a CLI stage (`python -m pipeline.<stage>`), chained by `pipeline/run.py`; all geometry in EPSG:32635 metres.

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock.txt          # exact versions (numpy, opencv, shapely, scipy, ortools, ultralytics, torch)
# raw challenge package (01_tiles … 05_examples) in the parent folder, or: export VINEYARD_RAW=/path/to/package
python scripts/setup_data.py                  # extracts the 311 tiles into data/tiles + route inputs + examples
```

## Run: from the supplied tiles to the routes and the measurements

```bash
python -m pipeline.run --all                  # classical detector (the configuration we submitted)
python -m pipeline.run --all --weights yolov8n-seg-vineyard-canopy.pt   # YOLO canopies + classical rows
```

Stages (each is its own CLI, `python -m pipeline.<stage> --help`):

| Stage | Module | What it does |
|---|---|---|
| detect | `pipeline.baseline` | ExG vegetation → dominant row orientation → periodic row grid → per-row line fit → canopies inside the ±0.3 m band → inter-row strips → `row_structure`, `interrow_cover`. Vine / non-vine filter (crown spill + size) and forbidden-zone filter. |
| (optional) | `pipeline.infer_yolo` | YOLOv8n-seg canopies on 640 px crops, merged with the classical rows |
| blocks | `pipeline.row_ends`, `pipeline.blocks` | rows and inter-rows stop at the roads and at the imagery edge (below), then global `vineyard_id` (connected plantings < 5 m apart, roads always separate) and `row_id` shared by the segments of one physical row across tiles |
| targets | `pipeline.targets` | inspection targets = row gaps ≥ 5 m (ID, X, Y, `vineyard_id`, `row_id`) + waste centres |
| route | `pipeline.route` | 0.5 m grid graph on inter-rows + passages (canopies / forbidden blocked, vine rows are walls), OR-Tools TSP from START, legs straightened by string pulling, outside-share budget ≤ 1.7 % (official limit 2 %); `--mode inspector` / `--mode farmer` (rules below) |
| validate | `pipeline.validate` | one LineString, EPSG:32635, `length_m`, start = end ≤ 5 m, share outside inter-rows + passages, targets visited ≤ 2 m, row crossings outside passages (info) |
| measure | `pipeline.measurements` | `measurements.csv`: totals, per block, per row (m, m², ha; canopy area = union of polygons) |
| export | `pipeline.export_cvat` | Marcaj upload ZIPs (`annotations.xml` + unchanged tiles), split < 60 MB, validated |
| web | `scripts/make_web_tiles.py`, `scripts/build_web_map.py` | orthophoto mosaic (10 cm/px + full-res reference tiles) and GeoJSON layers for `web/index.html` |

**Walking route rules** (`pipeline/route.py`). The route never crosses a vine row: the trellis wires make a row
impassable even where vines are missing, so every row axis is a wall (±0.65 m: the ±0.3 m vine band plus the 0.35 m
walking margin, round ends) over its whole length, planting gaps included, with the segments of one row joined across
tile seams. The route walks along the inter-rows and changes inter-row only past the row ends (headlands) or on an
authorised passage (a road crossing the rows stays open). Each leg is then straightened by string pulling: the stops stay
fixed vertices, and a shortcut is kept only if it stays on walkable cells, crosses no row, touches no canopy / forbidden
zone and adds no metres outside the inter-rows + passages, so the line has no grid staircase. `--allow-row-crossing` restores the old
behaviour (stepping over a row through a planting gap).

**Coverage under the 2 % rule.** Every change of inter-row goes around a row end, and those metres are outside the
inter-rows + passages; visiting all 795 reachable targets would put the tour 6.6 % outside, so the route keeps the stops
worth most per outside metre: (1) a gap on a row is seen from the inter-row on either side, and a greedy set cover puts
each stop in the inter-row shared by most gaps (31 % fewer inter-rows to walk); (2) while over budget the tour is
thinned — in bulk while far over, then by the exact outside metres each stop saves (its two legs minus the leg that
replaces them) — and re-solved; value: waste 10, gap ≥ 5 m 3, shorter gap 1. Waste up to 25 m off the inter-rows /
passages is a target (short walk off, paid from the budget). Inspector route on the final annotations (v5, 855 targets: gaps ≥ 3 m + waste): **17.3 km, 1.49 % outside (validate.py), 0 m through canopies / forbidden zones, 0 row crossings, back at START, 440 targets within 2 m**; farmer route 0.9 km, 1.14 % outside. Stops in inter-rows closed off by walls, the study-area edge or forbidden zones (reachable only across a row) are skipped.

Sunday recompute from the corrected Marcaj export (ZIP or annotations.xml; one per task or one for the project):
`python scripts/sunday.py EXPORT.zip` — merges the files, reports missing attributes, then targets → routes →
validation → measurements → web data (`--dry DIR` runs the same chain into DIR first, `--reblock` recomputes IDs,
`--quick` skips the day tours and the web route variants: ~15 min instead of 31.5 min on the M4 Pro).

Local scoring on the two official example tiles (same formulas as the challenge): `python -m pipeline.eval --pred out/baseline.xml`
→ partial score 0.817 for the classical detector (canopy 0.587, axes 0.961, attributes 0.970, grouping 1.0, counts 0.98).

## Run on your own survey

The pipeline is not tied to Sireț3. For another vineyard flight:

1. Tiles: georeferenced RGB GeoTIFFs (any size, ~2–4 cm/px works best), EPSG:32635 or any metric CRS with the
   georeference in the TIFF tags (`ModelTiepoint` + `ModelPixelScale`, read by `pipeline/tiles.py`). Put them in
   `data/tiles/` named `siret3_rRRR_cCCC.tif` (row / column of a regular grid) or adapt `NAME_RE` in `pipeline/tiles.py`.
2. Route inputs in `data/route/`: `start.geojson` (Point), `passages.geojson` (walkable roads / paths, MultiPolygon),
   `forbidden.geojson` (no-go areas), `study_area.geojson` — same CRS as the tiles.
3. `python -m pipeline.run --all` → `route.geojson`, `route_waste.geojson`, `measurements.csv`, `out/upload/*.zip`
   (CVAT 1.1 for a Marcaj / CVAT correction pass), `web/` ready to serve. Detector parameters (row spacing band, canopy
   thresholds) are in `pipeline/baseline.py` (`class P`); check them on 1–2 annotated tiles with `pipeline/eval.py`.
4. Docker (the repository is mounted, so outputs land in this checkout; the package is read from `..` or `VINEYARD_RAW`):
   ```bash
   docker compose build                      # classical pipeline; --build-arg WITH_MODEL=1 adds YOLO11 + the released weights
   docker compose run --rm pipeline          # tiles -> route.geojson, measurements.csv, Marcaj ZIPs, web data
   docker compose run --rm sunday EXPORT.zip # corrected Marcaj export -> final deliverables
   docker compose run --rm qa                # integration tests -> QA_REPORT.md
   docker compose up web                     # web map + live API on http://localhost:8000
   ```
   The image (Python 3.12, CPU; without the model 0.85 GB on Linux amd64, 1.2 GB on arm64) installs the direct dependencies of `requirements.txt` at the
   versions of `requirements.lock.txt`, runs the unit tests and imports every pipeline module while it is built.
   **Tested** on 27 Sep 2026 with Docker Desktop 4.92 (Apple Silicon, arm64), and built on every push by
   [GitHub Actions](.github/workflows/docker.yml) on a clean Linux runner (amd64). Inside the container:
   `measurements.csv` regenerated from `out/pre_global_v5.xml` is byte-identical to the committed file,
   `pipeline.validate` accepts `route.geojson` (1.49 % outside the passable area, start = end), `pipeline.eval` gives the
   laptop's reference score (0.844) and the web map + live API answer.
   With `--build-arg WITH_MODEL=1` (~3 GB, torch CPU) the released YOLO11 weights load, segment the reference tile
   `r021_c012` (219 canopies) and the live API reports the model.
   The QA web checks (W1–W11) need Playwright + Chromium and run on the laptop, not in the image.

## Quality assurance

[`QA_REPORT.md`](QA_REPORT.md) — integration tests on the real data and on synthetic edge cases, with the measured value,
the threshold and the evidence for each check (`python -m pipeline.qa --perf`; the same checks as tests:
`VINEPLAN_INTEGRATION=1 python -m unittest tests.test_integration`):

- **Boundaries:** rows keep their `row_id` / `vineyard_id` across tile seams, nothing on the black no-data border, rows
  stop at roads, inter-rows never on canopies, the route never crosses a row, stays on passable ground and returns to START.
- **Look-alikes:** shrub and tree crowns, rows on striped meadows or scrub (vegetation on the axis vs between rows, with
  images of the flagged blocks), white vine tubes and roofs taken for waste, the garden rule.
- **Edge cases:** synthetic tiles through the same detector — black, meadow, noise, bare soil, orchard, striped meadow,
  vineyard at 0° and 35°, half no-data, an 8 m planting gap — and damaged Marcaj exports (folder names, missing attributes).
- **Rules:** Marcaj labels and attributes, one polyline per row per tile, upload ZIPs byte-identical to the supplied tiles,
  measurement consistency, EPSG:32635, the score on the two reference tiles.
- **Performance and scalability:** seconds per tile, speed-up with the cores (tiles are independent), route cost per stop,
  web payload, measured stage timings.
- **Web app on phone, tablet and desktop** (`pipeline/qa_web.py`, headless Chromium through Playwright, ~1 min): no
  JavaScript errors and no missing file on any page; nothing scrolls sideways at 375 / 768 / 1440 px on the landing,
  sign-in and every app view (screenshots in `docs/qa/web_*.png`); sign-in opens the app in its role; RO / RU / EN
  translate the interface and the page keeps answering; a zone drawn freehand, edited, extended and cut with the mouse and
  by touch on a phone; a walking route computed in the browser; navigation when location is refused; load time and
  volume; every button named and tappable. `pip install playwright && playwright install chromium`; `--no-web` skips them.

Before a new set of pre-annotations replaces the previous one: `python scripts/compare_versions.py OLD.xml NEW.xml`
(reference score, the key QA checks side by side, object counts, the tiles that changed and a before / after sheet of
the most changed ones; verdict "not worse" only if the score does not drop and no check gets worse). Reference-tile
score by version: v1 0.817 → v3 0.844 (row direction by support, row support filter) → v4 / v5 0.844 (the later
changes are outside the two reference tiles: roads, row ends, tile seams, young vineyards).

Unit tests (fast, no data needed): `python -m unittest discover -s tests`.

## Processing time and hardware

Measured on a MacBook Pro (Apple M4 Pro, 24 GB), macOS 27, Python 3.12, no GPU used for the submitted pipeline:

| Stage | 311 tiles |
|---|---|
| detect (classical v3: direction by support, second pass) | 5 min 24 s (v1: 1 min 53 s) |
| blocks (global IDs) | 3 s |
| targets | 7 s |
| route inspector (795 targets, row walls) | 10 min 26 s (grid 21 s, distance matrix 22 s, cached; 60 s TSP + 16 re-solves of 10 s with the marginal-saving thinning, legs rebuilt and straightened in parallel) |
| route farmer (6 waste) | < 1 min |
| day tours + web route variants (gap ≥ 5 / 8 / 10 m) | ~16 min (skipped by `--quick`) |
| measurements + export ZIPs | 5 s |
| web data (mosaic + layers) | 3 min |

YOLO11n-seg training (2 classes, optional): about 8 min per epoch on the M4 Pro GPU (MPS), 15 epochs with early stop
(the first YOLOv8n-seg canopy model: 53 min, 17 epochs).
Full per-stage timings of the last run: `out/timing.json`.

## Detector v3 (row direction and row support)
The first upload to Marcaj (v1) drew one row direction per tile from the most *periodic* vegetation pattern; on tiles
with ploughed fields, tree lines or scrub that pattern was not the vines, so rows were drawn across the real rows or
over non-vineyard land (measured: on 123 of 194 tiles the lines carried no more vegetation than the space between them).
`pipeline/baseline.py` v3 (default; `--v1` reproduces the upload):
- **direction by support**: of the 6 most periodic directions, keep the one whose row lines sit on the most vegetation
  compared with half a spacing away (vines 0.2–0.5, wrong lines ~0);
- **row support filter**: a row needs on/between vegetation ≥ 1.3 and a difference ≥ 0.08 (vine rows: median 7,
  wrong lines ~1.0);
- **second pass**: what the first vineyard leaves unexplained is searched for a second vineyard with another direction
  (block corners, neighbouring plots);
- **model veto** (`scripts/veto_list.py` → `scripts/model_veto.py`): tiles where the detectors disagree completely (the
  high-recall v1 classical detector draws vines, the released YOLO11 model finds no canopy) stay empty: meadow, scrub,
  ploughed fields, gardens. Very young vineyards found only by v3 (tiny plants the model misses) are kept.
- **window search** (`pipeline/window_detect.py`, run by `pipeline.blocks` first): the detector finds one row direction
  and spacing per 51.2 m tile, so a small vineyard (a 15 m strip of young vines, a grassy plot) in a tile that is mostly
  meadow or scrub does not dominate the estimate and the tile kept no rows. The same detector runs on 12.8 m windows
  (half overlapping, 10 500 windows, 4 min on 11 cores); a window row is kept only where the annotations do not
  already explain it, and only with consensus and context: found by ≥ 2 overlapping windows (a tile's best by ≥ 3; a
  weaker row only as the neighbour of a strong one), > 8 m from any roof or paved yard (unsaturated or red-tile
  patches ≥ 20 m²), not along an organiser passage, ≥ 3 parallel rows per tile. Tiles emptied by the model veto are
  not searched. Checked tile by tile on contact sheets: yards, gardens, road verges and scrub were the false rows of
  the unfiltered search (all removed); 127 rows and 1 130 canopies added in 23 tiles (young vineyards r030_c018,
  r031_c017, r032_c021, rows missing in V-blocks r025_c017, r033_c022, r036_c025 …); reference score unchanged (0.844).
- **row continuation** (`pipeline/row_extend.py`, run by `pipeline.blocks` next): the detector works tile by tile, so
  where a vineyard only fills a corner of a tile, or its inter-rows are grassy, that tile kept no rows although the rows
  of the tile next to it run up to the shared edge and the vines carry on to the road. Every row end on a tile edge
  with no continuing row across it is followed into the neighbouring tile along its own axis while the row pattern
  holds (greenness on the axis minus on the mid-lines, at least 45 % of the same row's contrast over its last 10 m in
  its own tile), stopping at the road, the no-data border or an existing row; canopies from the green pixels of the vine
  band (not where the band is uniformly green) and inter-rows between continued neighbours come with it. One
  continuation per axis (a gap tile continued from both sides keeps the longer). Sireț3: 384 rows, 3.1 km, 1 001
  canopies, 133 inter-rows added; the reference tiles are untouched (score 0.844). Interior row ends (not on a tile
  edge) are trimmed where the row pattern falls below 30 % of the row's own for ≥ 3 m (grass verge, scrub, yard,
  roof); ends on a tile edge are never trimmed (on both reference tiles every row runs to the tile edge, and trimming
  them cost 0.02). 44 blocks, 693 rows.
- **row ends** (`pipeline/row_ends.py`, run by `pipeline.blocks`): the detector ends a row where the vegetation on its
  axis ends, so rows ran across tracks, poked into roads and carried on into gardens and scrub beyond them. A row is cut
  where it crosses an organiser passage **that separates two blocks** (on the bare track itself when it shows within 5 m
  of the passage); inside one block the passage is a gap and the row runs on — reference tile r006_c004 keeps its rows
  through such a grass strip. Bare row ends on a road and ends on the black no-data border are removed, a piece left
  beyond a road is dropped (with its canopies) unless it sits on a vine row (on/between vegetation as above), and every
  inter-row is clipped to the outline of its two rows and to the imagery (the detector ran inter-rows to the tile edge
  over the black border). Vegetation-only trimming was tried and rejected: young vines read as bare ground and grassy
  inter-rows as meadow, and on the reference tiles it cut rows that run to the tile edge. Result on the mosaic: 314 road
  crossings cut, 53 passages kept inside a block, 196 pieces in scrub / yards / fields dropped, 3.96 km of row and
  1.3 ha of inter-row removed, 47 blocks, 691 rows; the reference-tile score is unchanged (0.844). Row links across tile
  seams also accept up to 12° when the facing ends touch and no longer skip a short corner piece between two long
  ones: the same `row_id` on both sides of 99.0 % of the 718 seams (was 84.3 %).
- **waste** (`scripts/add_waste.py`): YOLO11 waste detections with score ≥ 0.4, at most 2.5 m per side and outside the
  organiser forbidden zones (sheet-metal roofs were the main false positive): 11 boxes, checked in Marcaj.

On the two official reference tiles the local score (`pipeline.eval`) goes from 0.817 to 0.844 (axes F1 0.961 → 1.000,
canopies 0.587 → 0.633); the per-line check `scripts/row_support.py` finds 0 tiles with lines off the vines (v1: 123).
Remaining gaps: very young vineyards and vineyards in a tile corner (listed for manual work). Correction plan for Marcaj
(v1 in Marcaj vs v3, per task and frame): `scripts/marcaj_plan_v3.py` → `out/marcaj_plan_v3.md`, web `review.html` and
the Corectură tab. v3 upload ZIPs: `out/upload_v3/` (only usable if the organisers reset the project).

```bash
python -m pipeline.baseline --out out/baseline_all_v3.xml
python -m pipeline.infer_multi --weights yolo11n-seg-vineyard-waste.pt --base out/pre_global.xml --out out/model11_canopy_all.xml \
       --scores out/waste_model11.json --canopy model --conf 0.25 --conf-review 0.15      # model canopies + waste scores
python scripts/veto_list.py && python scripts/model_veto.py out/baseline_all_v3.xml out/baseline_all_v3v.xml
python scripts/add_waste.py out/baseline_all_v3v.xml out/baseline_all_v3w.xml
python -m pipeline.blocks --inp out/baseline_all_v3w.xml --out out/pre_global_v5.xml --geojson out/blocks_v5.geojson   # windows, continuation, row ends, IDs
python scripts/compare_versions.py out/pre_global_v4.xml out/pre_global_v5.xml     # accept a new version only if it is not worse
python -m pipeline.export_cvat --inp out/pre_global_v5.xml --out out/upload_v5      # the 9 ZIPs uploaded to Marcaj (project v2)
python scripts/row_support.py --inp out/pre_global_v3.xml && python scripts/marcaj_plan_v3.py
```

## Model

How the whole solution works, the training data and the open questions: [`docs/MODEL.md`](docs/MODEL.md).
One **YOLO11n-seg** with two classes (`vineyard`, `waste`) —
[weights: GitHub release v0.2-weights](https://github.com/Viteokk/MindHack__VineyardAIFieldChallenge/releases/tag/v0.2-weights):
`train/make_multi_dataset.py` (Sireț3 crops with the official reference + pseudo-labels, DroneWaste v1.0 and UAVVaste,
both CC BY 4.0, non-waste categories dropped) → `train/train_yolo.py --model yolo11n-seg.pt --name multi11` (15 epochs,
2 h 51 min on the M4 Pro GPU) → `pipeline/infer_multi.py`. Held-out validation: mask mAP50 vineyard 0.717, box mAP50
waste 0.731 (YOLOv8n-seg: 0.713 / 0.654). On the held-out example tile the classical canopies still score higher
(0.683 vs 0.529 model-only), so the submitted canopies are classical; the model supplies waste candidates and the
live "analyse a tile" mode.

Submitted detections come from the classical pipeline (it scored higher than the fine-tuned YOLO on the example
tiles: canopy 0.587 vs 0.562, because its outlines match the loosely traced reference better). The neural model
is delivered anyway: `train/make_dataset.py` (pseudo-labels + official example, 640 px crops) → `train/train_yolo.py`
(yolov8n-seg, MPS) → weights in the release above; `pipeline/infer_yolo.py` plugs it into the same pipeline.

## Web interface

Pages (RO / RU / EN, Romanian by default): `web/index.html` (landing: what the platform solves, for whom, how it works, EU alignment, data sources) → `web/login.html` (role + e-mail or phone; demo accounts `inspector@fieldplanner.demo` / `+373 69 000 101` and `administrator@fieldplanner.demo` / `+373 69 000 202`, password `demo2026`, prefilled and checked in the browser only; password reset and sign-up dialogs are demo flows; or MPass) → `web/app.html`. Each role starts on its own page (inspector: Azi; administrator: Plantația mea), then Planifică / Hartă / Rapoarte; the chosen zone scopes every figure, list and CSV; technical pages are in *Mod echipă*. The role is fixed by the account; logging out is the way to switch.

`web/app.html` — Leaflet on the real orthophoto in UTM (CRS.Simple, no reprojection): layers (canopies, rows with
`row_id`, inter-rows with cover, passages, forbidden, blocks), both routes with length, walking time and targets
visited, measurements per block / row, pipeline status. Data contract: `web/data/*.geojson`, `web/data/pred/*`,
`web/data/route*.geojson`, `web/data/route_check_*.json`, `web/data/variants/`.

- **Roles** (login page, or `#inspector` / `#fermier` in the link): state inspector (compliance, blue route, gaps,
  blocks, areas) and vineyard administrator (red route, waste, missing vines ≈ gap length / 1.2 m, replanting cost,
  yearly maintenance at MDL 52 000–80 000 / ha from the brief, Marcaj corrections). Each role sees only its tabs.
- **MPass:** „Intră cu MPass” on the login page redirects to the real `https://mpass.gov.md/login/saml`. For the demo, a
  simulated flow is linked under it: `mpass.html`, an authorization page clearly labelled as a demo, with test identities
  and no credential fields, then `auth.html`, the callback that opens the session with the role. A real integration needs VinePlan registered as a SAML 2.0 service provider with the
  Agenția de Guvernare Electronică and a server-side assertion consumer endpoint that checks the signature and maps
  the IDNP to a role; a static site cannot do this.
- **Planifică (both roles):** choose a work zone (cadastral number anywhere in Moldova via ASP, a drawn rectangle / polygon,
  blocks or a parcel clicked on the map), a START (official, clicked on the map, or the phone's GPS) and the targets (row gaps
  from a minimum length, annotated waste, AI waste candidates). The walking route is computed **in the browser**
  (`web/router.js`, a Web Worker) with the same rules as `pipeline/route.py`: 0.5 m grid, only inter-rows and authorised
  passages as cheap cells, canopies (+0.35 m) and forbidden zones blocked, targets visited within 2 m, TSP with 2-opt,
  START → targets → START; GPX / GeoJSON export. Typical block: 1–40 s. The share outside inter-rows is shown; the
  optional *Strict* mode drops the costliest stops to stay under the competition's 2 % (checked with `pipeline.validate`:
  0 m through canopies and forbidden zones).
- **Parameters:** walking speed and hours per day (times and field days update at once), minimum gap to inspect
  (precomputed routes for ≥ 5 / 8 / 10 m on the static site, `scripts/build_route_variants.py`).
- **Field use:** GPX export of each route, GPS navigation on the phone with a chosen start and checked targets
  (location refused or unavailable: a clear message and target-by-target navigation without the distance).
- **The whole village:** the organisers' full source orthomosaic (the challenge tiles cover only the vineyards) is
  reprojected to EPSG:32635 at 20 cm (`scripts/build_village_layer.py`, 52 WebP chunks, 7.5 MB, 12 s) and drawn under the
  challenge mosaic; a chunk is downloaded only when it comes into view (layer “Satul Sireți”).
- **Zone drawing:** freehand (press and drag) or corners by click, rectangle, polygon, a block or a cadastral parcel;
  then *Editează* (drag corners, add a corner on a side, double-click deletes one), *Adaugă* (another shape) and
  *Exclude* (a hole: a house, a road). The zone travels in the link (`#zona=poly:…|…~…`).
- **Language:** the menu lists Română / Русский / English (RO by default).

### Environmental indicators (ISO 14001 activity data)
`python -m pipeline.env_indicators` → `web/data/env_indicators.json`, `out/env_indicators.csv` (one row per block +
TOTAL; < 1 s on the MacBook Pro M4 Pro, in `out/timing.json`). For ISO 14001 clause 9.1 (monitoring and
measurement); **activity data only, no GHG / CO2 calculation (ISO 14064-1 out of scope)**. Per `vineyard_id`:

| Indicator | Definition | Unit |
|---|---|---|
| `block_area_ha` | block polygon area | ha |
| `waste_count`, `waste_per_ha` | annotated waste in the block, per hectare | pieces, pieces/ha |
| `waste_candidates_ai` | AI waste candidates (not confirmed) in the block | pieces |
| `bare_soil_share`, `vegetation_share`, `mixed_share`, `unassessable_share` | inter-row cover, area-weighted over the block's `interrow_area` polygons | 0–1 |
| `missing_vines`, `missing_vines_share` | ≈ gap length / 1.2 m; gap length / row length | vines, 0–1 |
| `inspection_km_block`, `inspection_km_saved` | km to inspect only this block; full inspection tour − that | km |

`inspection_km_block` is an approximation (a separate optimal route per block takes ~1 min each): 1.3 × (straight-line
distance between consecutive targets of the block in the official route's order + 2 × straight-line distance from
START); for V42 it gives 3.0 km vs 3.1–3.3 km from the real routers. Totals are checked against `blocks_report.json`
(area, missing vines, inter-row area, row length; the module fails if any differs by more than 1 %). Web map:
Măsurători → **Mediu · ISO 14001**: sortable table, map coloured by the chosen indicator, indicators in each block
card, **Export CSV (ISO 14001)** with source (flight 20 May 2025), method, units and scope in the header.

### Vineyard register (DEMO, EU-compatible)
`python -m pipeline.register [--make-demo]` → `web/data/register.geojson`, `out/register.csv` (≈ 2 s).
Model in [`registry/schema.json`](registry/schema.json), aligned with Reg. (EU) 2018/273 art. 7 and annexes III–IV:
**grower** (`DEMO-G-xx`, pseudonymised) → **parcel** (`cad_nr` `DEMO-xxxx`, geometry, RVV code, declared area, variety,
planting year and scheme, authorisation, status planted / grubbed_up / abandoned, IGP) → **events** (planting,
replanting, grubbing-up, inspection). **Everything declared is synthetic and marked `demo: true`**; the parcels are
generated from the detected blocks (whole blocks, halves split parallel to the rows, one half without authorisation,
two parcels with no vines, one declared abandoned) and linked to `registry/rvv_demo.json` by `cad_nr`.
Measured fields per parcel come from the pipeline: rows clipped to the parcel × the block's median row spacing
(`measured_area_ha`, a few % below the block figure because rows are cut exactly at the parcel edge), rows, density
nominal / effective (1.2 m vine spacing), gap share, missing vines, inter-row cover shares and `status_detected`
(`no_vines`: measured < 10 % of the parcel; `abandoned`, heuristic: vegetation inter-rows > 80 % and gaps > 40 %).
In the web map (Conformitate → Registru viticol, layer “Registru viticol DEMO”, ⌘K search `DEMO-0005`, link
`#parcel=DEMO-0005`) a click opens the **parcel sheet**: declared vs measured side by side (differences above the
tolerances in `criteria.json` highlighted), missing authorisation in red, event history, DEMO label always visible;
**register extract** as CSV or GeoJSON for one parcel or all, with generation date, source and DEMO notice.
Tests: `python -m unittest tests.test_register` (halves sum to the block within 1 %, every parcel has id / geometry /
status, scenarios present, registry linked). Real integration (ONVV RVV, ASP cadastre, AIPA) would be a data exchange
through MConnect; not implemented.

### Possible unauthorised plantings / register to update (DEMO)
`python -m pipeline.register_mismatch` → `web/data/register_mismatch.geojson`, `out/register_mismatch.csv` (< 1 s).
Compares where vines are (detected blocks) with where they are registered (DEMO register), Reg. (EU) 1308/2013
art. 62–72 and Reg. (EU) 2018/273 art. 7, 37. Only real vineyards count (≥ 3 rows and ≥ 0.15 ha, the register
threshold); smaller ones are listed as below threshold, never flagged. **Type A** “possible unauthorised planting”:
part of a vineyard not covered by a planted, authorised parcel (≥ 0.05 ha or ≥ 10 % of the block). **Type B**
“possible grubbed-up / abandoned”: parcel registered as planted with < 10 % covered by detected vines. Thresholds,
wording and legal texts live in `registry/criteria.json` → `mismatch`; every item is a signal, not a verdict, with a
visit point snapped to the nearest inter-row / passage. In the web map (Conformitate): red / orange layer, list sorted
by area, “+ vizită”; on the laptop (`python -m pipeline.serve`) the route through the chosen places is computed and
checked with `pipeline.validate` (example: 2 places, 1.24 km, 0.14 % outside, valid); on GitHub Pages the visit list
downloads as GPX. `route.geojson` is never changed. Tests: `python -m unittest tests.test_register_mismatch`
(all deliberate DEMO mismatches found, nothing below 0.15 ha flagged).

### Compliance: vineyard register (ONVV), cadastre, AIPA subsidies
Tab „Conformitate” (role *Inspector*): per block, what the drone measured (planted area, density, gaps) against the
Registrul vitivinicol entry and the AIPA request (demo records, clearly labelled), plus the **real public cadastral parcels**
(ASP, WFS on geodata.gov.md): status per block, the eligible amount recomputed, a 5.1 km control route through the 9 blocks
to visit only, and a printable inspection report. A real registry extract (CSV) can be loaded in live mode.
Sources, legal basis and what is real vs demo: [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md).
`python -m pipeline.cadastre` · `python -m pipeline.compliance --visit-route [--registry-csv extras.csv]`

### Zones, cadastral search, Moldova context
- **Zonă** tab (`#zona`): one zone polygon from three inputs — draw (rectangle / polygon), select (blocks, tiles, the
  cadastral parcel under a click; Shift+click adds), or a cadastral number. The same zone gives measurements (rows,
  canopies, inter-rows, gaps, missing vines, waste), the blocks with their compliance status, the cadastral parcels,
  GeoJSON / CSV export, a printable report and, in live mode, a route through the zone only. Shareable links:
  `#zona=bloc:V63,V32`, `#zona=cad:80371140153`, `#zona=tile:r021_c012`, `#zona=poly:x,y;x,y;...`.
- **Cadastral numbers**: search anywhere in Moldova (⌘K or the Conformitate tab) — the page queries the public ASP
  cadastre on geodata.gov.md (WFS with CORS; MOLDREF99 → UTM 35N with proj4) and shows land use, area and the vines
  detected on the parcel when it is inside the flight. No owner data is used.
- **Moldova**: country outline (Natural Earth), 35 raions and the Sireți commune (ASP), study area highlighted
  (`scripts/build_moldova.py`).
- Production step not in this repo: the vineyard register (RVV, ONVV) and AIPA files have no public API; the real
  integration is a data exchange through MConnect (the government interoperability platform). The CSV import in live
  mode is the stand-in.

### Live mode on the laptop
```bash
python -m pipeline.serve            # http://127.0.0.1:8000 — the same site plus the local API
```
The site detects the API and switches to live computation:
- **Recompute a route** with any gap threshold (`POST /api/route`): `pipeline.route` on the filtered targets with
  the cached distance matrix, 1–3 min on the M4 Pro, drawn as a third route with its own GPX.
- **Analyse a new tile** (`POST /api/analyze`): upload any georeferenced GeoTIFF at ~2.5 cm/px; the classical
  detector and the AI model return canopies, rows, inter-rows and waste on the map with counts and areas
  (~4 s per 2048 px tile on CPU).

## Back-office (administration, demo)

`web/admin.html` (sign-in role **Back-office**, account `backoffice@fieldplanner.demo`): the administration side of the platform, in the
style of the government back-offices (MAIA → AIPA → territorial subdivisions, ONVV). Modules:

- **Subsidy applications**: list with search, filters, column choice, CSV; each application goes through *Depunere → Verificare
  administrativă → Control pe teren → Evaluare → Decizie → Plată*, has an assigned inspector (AIPA Strășeni), the drone's declared vs
  measured comparison and checks for its block, the inspection report, and an **activity panel** (who viewed or changed what, before →
  after). Actions: assign the inspector, next stage, eligible amount, decision, note.
- **Beneficiaries** (the holdings that apply), **Organisations** (hierarchy, address), **Platform users** (roles per module,
  permissions, sign-in and activity tab with module / date filters), **Roles and permissions** (create a role or a permission, MPass-only
  roles), **Reports** (applications, field inspections per inspector, beneficiaries, register, users; indicator panel or table, CSV).
- The inspector keeps working in the portal (`app.html`); what they do there (block card, inspection report, checked targets, route)
  is logged in the browser and shows up in the application of the same block and in the inspector's activity.

All people, IDNP / IDNO, phones, e-mails, beneficiaries, applications and activity are **synthetic** (`scripts/build_backoffice_demo.py`
→ `web/data/backoffice.json`, IPs from the RFC 5737 documentation ranges); the institution names are real. Changes made in the
back-office stay in the viewer's browser ("Resetează datele demo" restores the data).

**Accessibility:** WCAG 2.1 A/AA and best practices checked with axe-core on the landing, the sign-in page, the portal views of both
roles and the back-office, desktop and phone: 0 violations (QA check W13). Skip links, visible focus, labelled controls and landmarks,
keyboard-reachable tables, AA contrast in both themes.

## Paid APIs / LLMs

None in the processing pipeline. Development assisted by Claude Code.

## Licences & attribution

Challenge provider: [Marcaj](https://marcaj.com) (challenge, annotation platform, annotation rules and scoring); event: Deeptech GigaHack 2026, hosted and organised by GigaHack at Tekwill, Chișinău.

Sireț3 imagery: CC BY 4.0 — 3DATA COLLECT / OpenAerialMap, contributors to the Open Imagery Network. Changes: reprojected to EPSG:32635 and tiled (the supplied tiles), resampled and WebP / JPEG-compressed for the web map (`web/data`). The web pages show this credit in the landing footer and on the map (corner attribution, layers panel, legend).
Route inputs contain information from OpenStreetMap © OpenStreetMap contributors, ODbL.
