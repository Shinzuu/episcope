# One-command build (ENG-1): `make all` from a clean clone.
PY := .venv/bin/python
# Ignore any local Hadoop config so Spark uses the local filesystem.
RUN := env -u HADOOP_CONF_DIR -u HADOOP_HOME -u YARN_CONF_DIR mise x -- $(PY)
SF ?= 0.25
SEED ?= 42

check:     ; $(RUN) scripts/verify.py

.PHONY: check setup all generate bronze silver gold analytics serve dashboard test clean

setup:
	mise install
	mise x -- python -m venv .venv
	$(PY) -m pip install -r requirements.txt
	test -f .env || cp .env.example .env

all: generate bronze silver gold analytics serve

generate:   ; $(RUN) -m generator.generate --sf $(SF) --seed $(SEED)
bronze:     ; $(RUN) -m pipeline.bronze.ingest
silver:     ; $(RUN) -m pipeline.silver.build
gold:       ; $(RUN) -m pipeline.gold.build
analytics:  ; $(RUN) -m analytics.run_all
serve:      ; $(RUN) -m serving.export
dashboard:  ; cd dashboard && python3 -m http.server 8000
test:       ; $(RUN) -m pytest -q tests
clean:      ; rm -rf data lake spark-warehouse metastore_db derby.log
