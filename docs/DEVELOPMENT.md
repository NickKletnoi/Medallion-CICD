# Making a change and getting it released

Databricks calls the Repos feature **Git folders** now; it is the same thing. The flow:

```
 Git folder (your branch)  ──push──▶  GitHub pull request  ──▶  CI tests on Databricks  ──merge──▶  main  ──▶  CD deploys to prod
  edit + run in dev_<you>_ schemas                                (ci_ schemas, removed again)
```

## A. One-time setup (a team lead or admin, about 30 minutes)

1. **GitHub repo.** Create it, push this folder to `main`. In *Settings → Branches* protect `main`: require a pull request, one approval, and the status checks `checks` and `integration`.
2. **Service principal.** In Databricks *Settings → Identity and access → Service principals* create `medallion-cicd`, then *Secrets → Generate secret* (keep the client id and the secret).
3. **Give it access** (SQL editor, as a catalog admin):
   ```sql
   GRANT USE CATALOG, CREATE SCHEMA ON CATALOG main_lf TO `<service principal application id>`;
   GRANT USE SCHEMA ON SCHEMA main_lf.default TO `<service principal application id>`;
   GRANT READ VOLUME, WRITE VOLUME ON VOLUME main_lf.default.data TO `<service principal application id>`;
   -- production writes into the existing schemas (privileges are inherited by their tables)
   GRANT ALL PRIVILEGES ON SCHEMA main_lf.bronze TO `<service principal application id>`;
   GRANT ALL PRIVILEGES ON SCHEMA main_lf.silver TO `<service principal application id>`;
   GRANT ALL PRIVILEGES ON SCHEMA main_lf.gold   TO `<service principal application id>`;
   ```
   The principal also needs workspace access and permission to use serverless compute for jobs.
4. **GitHub secrets.** In the repo *Settings → Environments* create two environments, `ci` and `production`. In each add the secrets `DATABRICKS_HOST` (`https://<workspace>.cloud.databricks.com`), `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`. On `production` add *Required reviewers* if every release needs a person's approval.
5. **Each developer:** in Databricks *Settings → Linked accounts* add the Git credential (GitHub personal access token with `repo` scope), then *Workspace → Create → Git folder*, paste the repo URL.
6. *(optional)* `databricks.yml`, target `prod`: set `run_as` to the service principal.

## B. Make a change (every time)

Example used below: a new warning rule that counts customers without a country.

1. **Start from a fresh `main`.** Open your Git folder. Click the branch name, choose **Pull**. Then click the branch name again → **Create Branch**, name it `feature/<ticket>-short-description` (for example `feature/EDP-123-customer-country-rule`) based on `main`.
2. **Edit** in the Git folder. For the example add to the `customers` block in `src/dq_rules.yml`:
   ```yaml
       - criticality: warn
         name: customer_country_present
         check: {function: is_not_null, arguments: {column: country}}
   ```
   Other typical changes: a new source (a `load_silver(...)` call **and** a block in `dq_rules.yml`), a new `load_dim` / `load_fact`, a change in `00_config`.
3. **Run it on your own copy.** Open `src/01_dimensions` and run all. The widget `schema_prefix` at the top defaults to `dev_<your user name>_`, so you write to `main_lf.dev_<you>_bronze / silver / gold`, never to the real schemas. Then run `src/02_facts`.
   - You changed a rule or a column list of a source: in your dev schemas nothing is stored yet for the first run, so it simply works. Later in the same dev schemas call `load_silver("<source>", ..., rebuild=True)` once.
4. **Check it.** Open `tests/verify`, set its widget `schema_prefix` to your prefix (same value as in step 3), run all. Every line must say `ok`. For the example also run `display(dq_report().filter("source = 'customers'"))` and look for `customer_country_present`.
5. **Clear notebook outputs** (*Edit → Clear all cell outputs*). The CI refuses notebooks with outputs.
6. **Commit and push.** In the Git folder click the branch name → the dialog lists the changed files. Tick them, write a message (`Add customer_country_present rule`), **Commit & Push**.
   *If someone else changed `main` meanwhile:* click **Pull** with `main` selected, then in your branch choose **Merge** / **Rebase** (the Git dialog's three-dot menu) and push again.

## C. Open the pull request

7. On GitHub the repo shows *Compare & pull request* for your branch (or *Pull requests → New*). Base `main`, compare `feature/...`. Fill the template: what changed and why, the checklist, and **whether a source must be rebuilt after the merge**.
8. **Watch the checks** (a few minutes):
   - `checks`: notebooks valid and without outputs, every `load_silver` source has rules, DQX accepts the rules, the bundle files are consistent.
   - `integration`: deploys the bundle as `ci`, loads the sample files in `test_data/` into fresh `ci_` schemas, runs the workflow, runs it **again with no new files** and requires that nothing changed, then removes everything. The run log of the failed task shows which `verify` line says `FAIL`.
9. **Fix and push again** (commit to the same branch in your Git folder). The checks restart on their own.
10. **Review.** A colleague approves in GitHub. Changes to rules deserve a second pair of eyes, because they decide which rows reach Silver.
11. **Merge** with *Squash and merge*. Delete the branch.

## D. After the merge

12. The `cd` workflow validates and deploys the bundle to production (workspace folder `/Workspace/Shared/.bundle/medallion/prod`, job `medallion-prod`). Check *Actions → cd*. If you set required reviewers, approve there.
13. **If the PR said a rebuild is needed** (a rule or a column list changed): once after the deploy, open `src/01_dimensions` (or `02_facts`) in your Git folder on `main`, run the first cells down to `%run ./00_config`, set the widget `schema_prefix` to **empty** (= the real schemas), and run only the `load_silver(...)` cell of that source with `rebuild=True` added. Do not commit that edit.
    Silver is incremental: it does not retest rows it already processed, so a changed rule only applies to old rows after a rebuild.
14. The job runs daily at 06:00 New York time once you set `pause_status: UNPAUSED` in `resources/medallion_job.yml` (that is itself a change: branch, PR, merge). Or start it by hand: *Workflows → medallion-prod → Run now*.
15. In your Git folder switch to `main` and **Pull**. Optionally drop your dev copy: run `tests/reset_schemas` with your prefix.

## E. Rolling back

On GitHub open the merged PR, **Revert**. That creates a new pull request that goes through the same checks, and its merge redeploys the old version. Data already written by the bad version stays: rebuild the affected sources (step 13) after the revert.

## F. Trying the real workflow from a terminal (optional)

Install the Databricks CLI, `databricks auth login --host https://<workspace>`, then in the repo folder:
```
databricks bundle validate
databricks bundle deploy            # target dev: schemas dev_<you>_..., job "[dev <you>] medallion-dev"
databricks bundle run medallion
databricks bundle destroy           # removes the job and the files, not the dev_ tables
```

## G. When something fails

| Symptom | Cause / fix |
|---|---|
| `checks`: *cells [..] contain outputs* | Clear all outputs in that notebook, commit again |
| `checks`: *sources without rules* | A `load_silver("x", ...)` has no `x:` block in `src/dq_rules.yml` (or the other way round) |
| `integration`: authentication error | Secrets of the environment `ci` missing or the client secret expired |
| `integration`: `PERMISSION_DENIED` / schema cannot be created | Step A.3 grants are missing |
| `verify` line `fact_sales has exactly the rows of silver.sales` | A fact keeps rows that Silver no longer has: check that the fact goes through `load_fact` |
| `verify` line `rerun with no new files: ... unchanged` | A step is not idempotent: the second run changed data although no file was new |
| `verify` count line `fact_sales has 394 rows` | The sample data changed. Update `expected_fact_sales` / `expected_fact_returns` in target `ci` of `databricks.yml` |
| CI waits a long time | Only one Databricks test run at a time is allowed (shared `ci_` schemas): the queue is working |
| Job parameter ignored in a job run | The widget name in the notebook must equal the parameter name (`catalog`, `landing_root`, `schema_prefix`, `rules_path`) |
