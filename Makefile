# One-command build (ENG-1): `make all` from a clean clone.
PY := .venv/bin/python
# Ignore any local Hadoop config so Spark uses the local filesystem.
RUN := env -u HADOOP_CONF_DIR -u HADOOP_HOME -u YARN_CONF_DIR mise x -- $(PY)
SF ?= 0.25
SEED ?= 42

# Incremental 14-day drop; pass the same SF as the base build (SF defaults to 0.25); DROP_SEED picks the drop (ING-4 drill).
drop:      ; $(RUN) -m generator.generate --incremental-days 14 --sf $(SF) --seed $(or $(DROP_SEED),7)
mock:      ; $(RUN) -m serving.mock_data
dashboard-verify: ; node dashboard/tools/verify.mjs
check:     ; $(RUN) scripts/verify.py

.PHONY: check setup reference reference-rebuild all generate bronze silver gold analytics evaluate serve catalog stream asof \
        drop mock dashboard dashboard-verify test clean

setup:
	mise install
	mise x -- python -m venv .venv
	$(PY) -m pip install -r requirements.txt
	test -f .env || { sed "s/^EPISCOPE_HASH_SALT=.*/EPISCOPE_HASH_SALT=$$($(PY) -c 'import secrets;print(secrets.token_hex(32))')/" .env.example > .env; }

# Processed reference data is committed (data/reference/, about 0.6 MB), so a clean clone skips the
# 2-hour rate-limited weather download. Rebuild with `make reference-rebuild`.
reference: ; @test -f data/reference/weather_daily.parquet || $(RUN) -m pipeline.reference.build
reference-rebuild: ; $(RUN) -m pipeline.reference.build

# ENG-4: the stages run as a DAG with retries (scripts/dag.py); each stage target below also runs on its own.
all:        ; $(RUN) scripts/dag.py --sf $(SF) --seed $(SEED)

generate:   ; $(RUN) -m generator.generate --sf $(SF) --seed $(SEED)
bronze:     ; $(RUN) -m pipeline.bronze.ingest
silver:     ; $(RUN) -m pipeline.silver.build
gold:       ; $(RUN) -m pipeline.gold.build
analytics:  ; $(RUN) -m analytics.run_all
evaluate:   ; $(RUN) -m analytics.evaluation.run
serve:      ; $(RUN) -m serving.export
catalog:    ; $(RUN) -m pipeline.catalog --list
stream:     ; $(RUN) -m pipeline.stream   # ING-6: refresh alerts as new encounter files land
asof:       ; $(RUN) -m pipeline.gold.asof $(DATE)   # ING-7: make asof DATE=2025-06-30
dashboard:  ; cd dashboard && python3 -m http.server 8000
test:       ; $(RUN) -m pytest -q tests
clean:      ; test ! -d data || find data -mindepth 1 -maxdepth 1 ! -name reference -exec rm -rf {} +; rm -rf lake spark-warehouse metastore_db derby.log
