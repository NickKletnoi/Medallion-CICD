# Databricks notebook source
# Drops the bronze / silver / gold schemas of a PREFIXED copy of the pipeline (test runs and cleanup).
# It refuses to run without a schema prefix, so it can never touch the real schemas.

# COMMAND ----------

dbutils.widgets.text("catalog", "main_lf")
dbutils.widgets.text("schema_prefix", "")
catalog, prefix = dbutils.widgets.get("catalog").strip(), dbutils.widgets.get("schema_prefix").strip()
assert prefix, "refusing to drop schemas: schema_prefix is empty (that would be the real bronze / silver / gold)"

# COMMAND ----------

for layer in ("bronze", "silver", "gold"):
    spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{prefix}{layer} CASCADE")
print(f"dropped {catalog}.{prefix}bronze / silver / gold")
