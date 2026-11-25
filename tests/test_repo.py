"""Fast checks that run on every pull request, without Databricks (pytest tests)."""
import ast, json, re, pathlib
import pytest, yaml, nbformat

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
NOTEBOOKS = sorted(SRC.glob("*.ipynb"))


def code_cells(nb_path):
    nb = nbformat.read(nb_path, as_version=4)
    return [c.source for c in nb.cells if c.cell_type == "code"]


# ── notebooks ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_notebook_is_valid_and_has_no_outputs(path):
    nb = nbformat.read(path, as_version=4)
    nbformat.validate(nb)
    with_output = [i for i, c in enumerate(nb.cells) if c.cell_type == "code" and (c.outputs or c.execution_count)]
    assert not with_output, f"{path.name}: cells {with_output} contain outputs; clear them before committing"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_code_cells_are_valid_python(path):
    for i, source in enumerate(code_cells(path)):
        if source.lstrip().startswith("%"):              # %pip, %run, %sql: notebook magics
            continue
        try:
            ast.parse(source)
        except SyntaxError as e:
            pytest.fail(f"{path.name} cell {i}: {e}")


def test_no_hardcoded_environment_in_cells():
    """Catalog, paths and schema names come from the settings, never typed into a cell."""
    pattern = re.compile(r"/Volumes/|main_lf|\bbronze\.\w|\bgold\.\w")
    for path in NOTEBOOKS:
        for i, source in enumerate(code_cells(path)):
            if path.name == "00_config.ipynb" and i == 0:  # the settings cell (first code cell) holds the defaults
                continue
            for n, line in enumerate(source.splitlines(), 1):
                if line.strip().startswith(("#", "print(", '"""')) or "f\"" in line and "{" in line and "print" in line:
                    continue
                code = line.split("#")[0]                    # a trailing comment may mention paths
                assert not pattern.search(code), f"{path.name} cell {i} line {n}: hard-coded environment: {line.strip()[:90]}"


# ── rules ────────────────────────────────────────────────────────────────────
RULES = yaml.safe_load((SRC / "dq_rules.yml").read_text())


def sources_loaded():
    found = set()
    for path in NOTEBOOKS:
        for source in code_cells(path):
            found |= set(re.findall(r'load_silver\(\s*"(\w+)"', source))
    return found


def test_every_loaded_source_has_rules_and_the_other_way_round():
    loaded = sources_loaded()
    assert loaded, "no load_silver(...) call found"
    assert loaded == set(RULES), f"without rules: {sorted(loaded - set(RULES))}; rules without a load_silver call: {sorted(set(RULES) - loaded)}"


@pytest.mark.parametrize("source", sorted(RULES))
def test_rule_block_shape(source):
    block = RULES[source]
    assert isinstance(block.get("checks"), list) and block["checks"], f"{source}: needs a non-empty checks list"
    for check in block["checks"]:
        assert check.get("criticality") in ("error", "warn"), f"{source}: criticality must be error or warn: {check}"
    if "dedupe" in block:
        assert block["dedupe"].get("name") and block["dedupe"].get("keys"), f"{source}: dedupe needs name and keys"


def test_dqx_accepts_every_rule():
    try:
        from databricks.labs.dqx.engine import DQEngine
    except Exception as e:                                  # DQX or pyspark not installed
        pytest.skip(f"DQX not importable: {e}")
    for source, block in RULES.items():
        status = DQEngine.validate_checks(block["checks"])
        assert not status.has_errors, f"{source}: {status.errors}"


# ── bundle ───────────────────────────────────────────────────────────────────
def bundle_files():
    return [ROOT / "databricks.yml", *sorted((ROOT / "resources").glob("*.yml"))]


def notebook_paths():
    """every notebook_path of every job (resources/ and targets), as an absolute path"""
    out = []
    for f in bundle_files():
        text = f.read_text()
        for rel in re.findall(r"notebook_path:\s*([^\s,}]+)", text):
            out.append((f, (f.parent / rel).resolve()))
    return out


@pytest.mark.parametrize("path", bundle_files(), ids=lambda p: p.name)
def test_bundle_yaml_parses(path):
    assert isinstance(yaml.safe_load(path.read_text()), dict)


def test_job_notebooks_exist():
    pairs = notebook_paths()
    assert pairs, "no notebook_path found"
    for f, p in pairs:
        assert p.exists(), f"{f.name}: notebook_path points to {p.relative_to(ROOT)}, which does not exist"


def test_bundle_variables_are_declared():
    declared = set(yaml.safe_load((ROOT / "databricks.yml").read_text())["variables"])
    for f in bundle_files():
        used = set(re.findall(r"\$\{var\.(\w+)\}", f.read_text()))
        assert used <= declared, f"{f.name} uses undeclared variables {sorted(used - declared)}"


def test_job_tasks_depend_on_existing_tasks():
    docs = [yaml.safe_load(f.read_text()) for f in bundle_files()]
    jobs = []
    for d in docs:
        jobs += list(((d.get("resources") or {}).get("jobs") or {}).values())
        for t in (d.get("targets") or {}).values():
            jobs += list((((t or {}).get("resources") or {}).get("jobs") or {}).values())
    assert jobs
    for job in jobs:
        keys = {t["task_key"] for t in job["tasks"]}
        for t in job["tasks"]:
            for dep in t.get("depends_on", []):
                assert dep["task_key"] in keys, f"{job['name']}: {t['task_key']} depends on unknown task {dep['task_key']}"


def test_every_job_parameter_is_read_by_a_notebook():
    """a job parameter that no notebook reads as a widget would be silently ignored"""
    widgets = set(re.findall(r'(?:_setting|widgets\.text)\(\s*"(\w+)"', "".join(
        "\n".join(code_cells(p)) for p in NOTEBOOKS) + "".join(p.read_text() for p in (ROOT / "tests").glob("*.py"))))
    for f in bundle_files():
        for name in re.findall(r"name:\s*(\w+),\s*default", f.read_text()):
            assert name in widgets, f"{f.name}: job parameter '{name}' is not read by any notebook"


def test_prod_target_never_gets_the_ci_job():
    d = yaml.safe_load((ROOT / "databricks.yml").read_text())
    assert "medallion_ci_test" in d["targets"]["ci"]["resources"]["jobs"]
    assert "jobs" not in (d["targets"]["prod"].get("resources") or {})
    assert d["targets"]["prod"]["variables"]["schema_prefix"] == "", "prod must use the real (unprefixed) schemas"
