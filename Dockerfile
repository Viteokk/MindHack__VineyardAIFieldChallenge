# VinePlan (vineyard-ai): GeoTIFF tiles -> annotations, routes, measurements, web map and QA report. CPU only.
#   docker build -t vineplan .                                  classical pipeline (no torch)
#   docker build -t vineplan --build-arg WITH_MODEL=1 .         + YOLO11 inference (canopy veto, waste) and the released weights
#   docker compose run --rm pipeline                            challenge package -> route.geojson, measurements.csv, web data
#   docker compose run --rm qa                                  integration tests -> QA_REPORT.md
#   docker compose up web                                       web map + live API on http://localhost:8000
# Built and tested on Docker Desktop (Apple Silicon, arm64) and by GitHub Actions on every push (.github/workflows/docker.yml).
FROM python:3.12-slim
ARG WITH_MODEL=0
ENV PYTHONUNBUFFERED=1 VINEYARD_RAW=/raw OMP_NUM_THREADS=4 MPLBACKEND=Agg PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock.txt requirements.txt ./
# the direct dependencies (requirements.txt) at the exact versions of requirements.lock.txt; torch / ultralytics only for the
# optional model steps: the classical pipeline, routes, measurements, QA and the web server run without them
RUN grep -v "^ultralytics" requirements.txt > /tmp/requirements.txt && pip install -c requirements.lock.txt -r /tmp/requirements.txt \
 && if [ "$WITH_MODEL" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/* \
      && pip install --index-url https://download.pytorch.org/whl/cpu $(grep -iE "^(torch|torchvision)==" requirements.lock.txt) \
      && pip install -c requirements.lock.txt ultralytics \
      && mkdir -p /opt/vineplan \
      && python -c "import urllib.request as u; u.urlretrieve('https://github.com/Viteokk/MindHack__VineyardAIFieldChallenge/releases/download/v0.2-weights/yolo11n-seg-vineyard-waste.pt', '/opt/vineplan/yolo11n-seg-vineyard-waste.pt')"; \
    fi
COPY config.py ./
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY registry ./registry
COPY tests ./tests
COPY train ./train
COPY web ./web
EXPOSE 8000
# self-check of the image: the unit tests (they need no tiles), then every pipeline module must import
RUN python -m unittest discover -s tests -q \
 && python -c "import importlib, pkgutil, pipeline; ms = [m.name for m in pkgutil.iter_modules(pipeline.__path__)]; [importlib.import_module('pipeline.' + m) for m in ms]; print(len(ms), 'pipeline modules import')"
CMD ["python", "-m", "pipeline.run", "--all"]
