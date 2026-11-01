PY ?= python
DBT = dbt --no-use-colors
export FLEET_DB ?= data/fleet.duckdb
export FLEET_PROCESSED ?= data/processed

.PHONY: setup data load build export readout all refresh app test docs jira clean

setup:            ## install dependencies
	$(PY) -m pip install -r requirements.txt

data:             ## download raw PVDAQ files from the OEDI data lake (about 800 MB, cached)
	$(PY) -m ingest.download

load:             ## reshape raw files into 5-minute inverter power (parquet)
	$(PY) -m ingest.load

build:            ## run dbt models and tests
	$(DBT) build --project-dir dbt --profiles-dir dbt

export:           ## export marts to reports/marts/*.csv
	$(PY) -m analysis.export

readout:          ## write reports/readout.md and reports/summary.json
	$(PY) -m analysis.readout

all: data load build export readout

refresh: data load  ## pipeline order: publish marts first, then run tests and record results
	$(DBT) seed --project-dir dbt --profiles-dir dbt
	$(DBT) run --project-dir dbt --profiles-dir dbt
	$(PY) -m analysis.export
	$(PY) -m analysis.readout
	$(DBT) test --project-dir dbt --profiles-dir dbt; status=$$?; \
	  $(PY) -m analysis.test_report; exit $$status

app:              ## launch the dashboard
	streamlit run app/streamlit_app.py

test:             ## end-to-end tests on synthetic fixtures with injected faults
	$(PY) -m pytest -q

docs:             ## build and serve dbt docs (lineage graph)
	$(DBT) docs generate --project-dir dbt --profiles-dir dbt && $(DBT) docs serve --project-dir dbt --profiles-dir dbt

jira:             ## dry run of the Jira push (add ARGS="--send" to create issues)
	$(PY) -m integrations.jira_push --limit 10 $(ARGS)

clean:
	rm -rf data/processed data/ci_processed data/*.duckdb dbt/target dbt/logs
