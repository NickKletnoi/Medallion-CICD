# Databricks notebook source
# Checks that must hold after every load, whatever the data. Any failed check fails the task (and the CI run).
#   phase = ""       only the invariants (the last task of the daily job)
#   phase = "first"  invariants, then saves the table counts
#   phase = "rerun"  invariants, then checks the counts did not change (a second run with no new files must change nothing)
# expected_fact_sales / expected_fact_returns (optional): exact row counts, for the known sample data of test_data/

# COMMAND ----------

dbutils.widgets.text("catalog", "main_lf")
dbutils.widgets.text("schema_prefix", "")
dbutils.widgets.text("phase", "")
dbutils.widgets.text("expected_fact_sales", "")
dbutils.widgets.text("expected_fact_returns", "")
get = lambda n: dbutils.widgets.get(n).strip()
catalog, prefix, phase = get("catalog"), get("schema_prefix"), get("phase")
SILVER, GOLD = f"{catalog}.{prefix}silver", f"{catalog}.{prefix}gold"
one = lambda sql: spark.sql(sql).first()[0]
failures = []

def check(name, bad_rows):
    """bad_rows = how many rows break the rule (0 = good)"""
    print(("ok    " if bad_rows == 0 else "FAIL  ") + f"{name}" + ("" if bad_rows == 0 else f"  ({bad_rows} rows)"))
    if bad_rows: failures.append(name)

# COMMAND ----------
# Gold dimensions: unique surrogate keys, exactly one Unknown member (-1)

for dim in ("dim_customer", "dim_product", "dim_customer_360"):
    t = f"{GOLD}.{dim}"
    check(f"{dim}: surrogate keys are unique", one(f"SELECT count(*) - count(DISTINCT {'customer_sk' if dim != 'dim_product' else 'product_sk'}) FROM {t}"))
    check(f"{dim}: one Unknown member (-1)", abs(1 - one(f"SELECT count(*) FROM {t} WHERE {'customer_sk' if dim != 'dim_product' else 'product_sk'} = -1")))

# Type 2 dimension: exactly one current row per person, versions do not overlap
t = f"{GOLD}.dim_customer_360"
check("dim_customer_360: exactly one current row per customer_key",
      one(f"SELECT count(*) FROM (SELECT customer_key FROM {t} WHERE is_current GROUP BY customer_key HAVING count(*) <> 1)"))
check("dim_customer_360: versions of a person do not overlap",
      one(f"SELECT count(*) FROM {t} a JOIN {t} b ON a.customer_key = b.customer_key AND a.customer_sk < b.customer_sk "
          f"AND a.effective_from < b.effective_to AND b.effective_from < a.effective_to"))

# COMMAND ----------
# Gold facts: unique business keys, every dimension key exists

for fact, key in (("fact_sales", "order_line_id"), ("fact_returns", "return_id")):
    f = f"{GOLD}.{fact}"
    check(f"{fact}: {key} is unique", one(f"SELECT count(*) - count(DISTINCT {key}) FROM {f}"))
    check(f"{fact}: every customer_sk exists in dim_customer",
          one(f"SELECT count(*) FROM {f} x LEFT JOIN {GOLD}.dim_customer d ON x.customer_sk = d.customer_sk WHERE d.customer_sk IS NULL"))
    check(f"{fact}: every product_sk exists in dim_product",
          one(f"SELECT count(*) FROM {f} x LEFT JOIN {GOLD}.dim_product d ON x.product_sk = d.product_sk WHERE d.product_sk IS NULL"))

# a sales line that Silver rejected (e.g. as a duplicate) must not stay in the fact: fact_sales = silver.sales
check("fact_sales has exactly the rows of silver.sales",
      one(f"SELECT abs((SELECT count(*) FROM {GOLD}.fact_sales) - (SELECT count(*) FROM {SILVER}.sales))"))

# COMMAND ----------
# Silver: the log has content, an interrupted run left no work table behind

check("silver.dq_log has rows", 1 if one(f"SELECT count(*) FROM {SILVER}.dq_log") == 0 else 0)
leftovers = spark.sql(f"SHOW TABLES IN {SILVER} LIKE '_work_*'").count()
check("no _work_ tables are left in silver", leftovers)
check("silver.load_log has rows", 1 if one(f"SELECT count(*) FROM {SILVER}.load_log") == 0 else 0)
check("the latest load of every source did not fail",
      one(f"SELECT count(*) FROM (SELECT status, row_number() OVER (PARTITION BY source ORDER BY run_ts DESC) AS rn FROM {SILVER}.load_log) WHERE rn = 1 AND status = 'failed'"))

# COMMAND ----------
# Optional exact counts (sample data only)

for fact, param in (("fact_sales", "expected_fact_sales"), ("fact_returns", "expected_fact_returns")):
    if get(param):
        n = one(f"SELECT count(*) FROM {GOLD}.{fact}")
        check(f"{fact} has {get(param)} rows (found {n})", int(n != int(get(param))))

# COMMAND ----------
# Counts to compare between the first run and the rerun

counts = {
    "dim_customer": f"{GOLD}.dim_customer", "dim_product": f"{GOLD}.dim_product", "dim_customer_360": f"{GOLD}.dim_customer_360",
    "fact_sales": f"{GOLD}.fact_sales", "fact_returns": f"{GOLD}.fact_returns",
    "silver_sales": f"{SILVER}.sales", "quarantine_sales": f"{SILVER}.sales_quarantine", "customer_unified": f"{SILVER}.customer_unified"}
now = {k: one(f"SELECT count(*) FROM {t}") for k, t in counts.items()}
print(now)
state = f"{SILVER}._ci_counts"

if phase == "first":
    spark.createDataFrame(list(now.items()), "name string, n long").write.mode("overwrite").saveAsTable(state)
elif phase == "rerun":
    check("rerun: the latest load of every source says 'nothing new'",
          one(f"SELECT count(*) FROM (SELECT status, row_number() OVER (PARTITION BY source ORDER BY run_ts DESC) AS rn FROM {SILVER}.load_log) WHERE rn = 1 AND status <> 'nothing new'"))
    before = dict(spark.table(state).collect())
    for k, n in now.items():
        check(f"rerun with no new files: {k} unchanged ({before[k]} -> {n})", int(before[k] != n))
    spark.sql(f"DROP TABLE IF EXISTS {state}")

# COMMAND ----------

assert not failures, f"{len(failures)} check(s) failed: {failures}"
print("all checks passed")
