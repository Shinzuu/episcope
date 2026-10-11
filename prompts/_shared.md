<shared_rules>
You are one of six parallel workers on EpiScope, our CSE 4345 Big Data Analytics group project
(repo ~/Documents/study/episcope). Five other workers are building other components in the same folder at
the same time. These rules keep you out of each other's way and keep the hidden-test-drop requirement safe.

Read first:
- docs/ANALYSIS.md: the brief, its marks and its traps.
- docs/research.md: primary-source notes for every method, dataset and tool. Use its recommended choices.
- docs/contracts.md: the interfaces between components. Code against them exactly.
- generator/schema.json: the raw data contract. Never change it.

Environment:
- Run Python as `env -u HADOOP_CONF_DIR -u HADOOP_HOME -u YARN_CONF_DIR mise x -- .venv/bin/python ...`.
  The machine's own Hadoop config would otherwise send Spark to HDFS.
- Get Spark only through `pipeline.spark.get_spark()`, with `EPISCOPE_DRIVER_MEMORY=1g` and
  `EPISCOPE_SPARK_MASTER=local[2]`. The laptop has about 6 GB free and other workers run Spark too.
- Pinned versions: pyspark 4.0.1, delta-spark 4.0.1, pulp 3.3.2, numpy 2.2.6, pandas 2.2.3,
  statsmodels 0.14.4. Don't install anything. If you need a package, list it in your report instead.

Boundaries:
- Write only inside the folders your task names, plus tests/test_<your_area>.py.
- Don't edit the shared files: requirements.txt, Makefile, README.md, docs/contracts.md,
  generator/schema.json, pipeline/spark.py. Put any change you need to them in your report.
- Don't commit or push. The team lead reviews and commits.
- Hard-code no dates, file names or row counts; derive them from the data. The instructor runs a hidden
  14-day drop through the code unmodified.
- Every module's docstring names the requirement IDs it serves, for example "Implements AN-1, AN-3".
- Before you finish, stop any Spark session or JVM you started.

Done means:
- your tests pass;
- your module runs on small data;
- you report: files created, test results, requirement IDs covered, shared-file changes needed, and open
  issues.
</shared_rules>
