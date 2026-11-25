# Medallion pipeline on Databricks: workflow + CI/CD

The v11 pipeline (Volume CSVs → bronze → silver → gold, DQX rules, incremental Silver, Type 2 `dim_customer_360`) packaged as a **Databricks Asset Bundle**, with tests and a GitHub Actions pipeline.

**How to change something and get it released: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**

## Layout
| Path | What |
|---|---|
| `src/` | `00_config`, `01_dimensions`, `02_facts` notebooks and `dq_rules.yml` (the v11 code) |
| `resources/medallion_job.yml` | The workflow: `dimensions` → `facts` → `verify`, serverless, daily 06:00 New York (paused) |
| `databricks.yml` | Bundle with three targets: `dev`, `ci`, `prod`; the `ci` target also holds the test workflow |
| `tests/test_repo.py` | pytest checks that run on every pull request (no Databricks) |
| `tests/verify.py` | Checks after every load (last task of the workflow, also used by CI) |
| `tests/reset_schemas.py` | Drops a *prefixed* copy of the schemas (refuses an empty prefix) |
| `test_data/Batch1..4` | Sample files; CI loads all of them. Expected: `fact_sales` 394, `fact_returns` 33 |
| `.github/workflows/ci.yml`, `cd.yml` | Pull request checks; deploy to production on merge |

## Environments
| Target | Schemas | Landing folder | Deployed by |
|---|---|---|---|
| (Git folder, interactive) | `dev_<user>_bronze/silver/gold` | the real `Landing` (read only) | you |
| `dev` | `dev_<user>_...` | `Landing_dev` | you, with the CLI |
| `ci` | `ci_...` (removed after the run) | `Landing_ci` (filled from `test_data/`, removed after the run) | pull request |
| `prod` | `bronze/silver/gold` | `Landing` | merge to `main` |

Settings are notebook widgets (`catalog`, `landing_root`, `schema_prefix`, `rules_path`); the job sets them from the bundle variables.

## Changes to the v11 code
1. `00_config` settings cell: widgets instead of constants (see above). **The interactive default for `schema_prefix` is now `dev_<your user>_`**, so a run in a Git folder no longer writes to your real `bronze/silver/gold`. Set the widget to empty to do that on purpose. The production job passes an empty prefix.
2. **Bug fix:** `load_fact` now deletes fact rows that are no longer in the query result (`upsert(..., delete_missing=True)`, Delta `WHEN NOT MATCHED BY SOURCE THEN DELETE`). Before, after Batch 4 the sales line 10001 (rejected as a duplicate) stayed in `gold.fact_sales`: 395 rows instead of 394. The new check `fact_sales = silver.sales` found it.
3. `rules_path` defaults to the `dq_rules.yml` next to the notebook.
4. **Load history:** every `load_silver` call appends one row to `silver.load_log` (`loaded` / `nothing new` / `failed` + error text, new files and rows, rejected rows, totals, seconds). `load_report()` shows it. `verify` checks the latest load of each source did not fail, and in the CI rerun that every source says `nothing new`.

## Tested / not tested
- **Run locally (Spark stand-ins, the same technique as before):** all four batches through `01_dimensions` and `02_facts` with the new settings cell and prefix; `verify` first run (all checks, 394 / 33), a rerun with no new files (counts unchanged), `reset_schemas` and its empty-prefix guard; `tests/test_repo.py` (25 tests, and a copy with three deliberate mistakes fails as it should).
- **Not run:** `databricks bundle validate/deploy/run` and both GitHub workflows (no Databricks CLI, workspace or GitHub access in the sandbox). Things only a real run can confirm: job parameters reaching the notebooks as widgets after `%pip` + `restartPython`; `.ipynb` files as `notebook_path`; `os.getcwd()` pointing at the notebook folder on serverless; serverless job compute for notebooks with `%pip`; `databricks fs cp` into a Volume; the `WHEN NOT MATCHED BY SOURCE` clause on your runtime. Run `databricks bundle validate -t ci` first and send me any error text.
