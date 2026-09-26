# VinePlan (vineyard-ai): GeoTIFF tiles -> annotations, routes, measurements, web map and QA report. CPU only.
#   docker build -t vineplan .                                  classical pipeline (no torch)
#   docker build -t vineplan --build-arg WITH_MODEL=1 .         + YOLO11 inference (canopy veto, waste) and the released weights
#   docker compose run --rm pipeline                            challenge package -> route.geojson, measurements.csv, web data
#   docker compose run --rm qa                                  integration tests -> QA_REPORT.md
#   docker compose up web                                       web map + live API on http://localhost:8000
FROM python:3.12-slim
ARG WITH_MODEL=0
ENV PYTHONUNBUFFERED=1 VINEYARD_RAW=/raw OMP_NUM_THREADS=4 MPLBACKEND=Agg
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock.txt requirements.txt ./
# torch / ultralytics only for the optional model steps; the classical pipeline, routes, measurements and QA run without them
RUN pip install --no-cache-dir $(grep -viE "^(torch|torchvision|ultralytics)" requirements.lock.txt) \
 && if [ "$WITH_MODEL" = "1" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision \
      && pip install --no-cache-dir $(grep -iE "^ultralytics==" requirements.lock.txt) \
      && python -c "import urllib.request as u; import os; os.makedirs('/opt/vineplan', exist_ok=True); u.urlretrieve('https://github.com/Viteokk/vineyard-ai/releases/download/v0.2-weights/yolo11n-seg-vineyard-waste.pt', '/opt/vineplan/yolo11n-seg-vineyard-waste.pt')"; \
    fi
COPY config.py ./
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY registry ./registry
COPY tests ./tests
COPY train ./train
COPY web ./web
EXPOSE 8000
# quick self-check of the image (unit tests need no tiles)
RUN python -m unittest discover -s tests -q
CMD ["python", "-m", "pipeline.run", "--all"]
