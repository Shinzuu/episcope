"""Tests for the dashboard and its mock serving data. Covers VIS-1, VIS-2, VIS-4, VIS-5, VIS-6 and PRV-2 checks
on the mock exports (shapes per docs/contracts.md "Serving", size budget, suppression), plus static checks on
dashboard/ (allowed libraries only, SRI hashes match the vendored files, module imports resolve)."""

import base64
import hashlib
import json
import pathlib
import re

import pytest

from serving import mock_data

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    out = tmp_path_factory.mktemp("dash_data")
    mock_data.main(["--out", str(out), "--weeks", "120", "--seed", "3"])
    return out, {p.name: json.loads(p.read_text()) for p in out.glob("*.json")}


def test_all_contract_files_and_size(exports):
    out, files = exports
    assert set(files) == {"meta.json", "weekly.json", "alerts.json", "forecasts.json", "allocation.json",
                          "calendar.json", "dq.json"}
    assert sum(p.stat().st_size for p in out.glob("*.json")) < 10 * 1024 * 1024


def test_row_shapes_match_contract(exports):
    _, f = exports
    assert set(f["meta.json"]) == {"generated_at", "data_through", "diseases", "suppression_threshold"}
    assert f["meta.json"]["suppression_threshold"] == 5
    shapes = {
        "weekly.json": {"d", "dz", "w", "c", "s", "inc"},
        "alerts.json": {"d", "dz", "w", "method", "stat", "thr", "alert"},
        "forecasts.json": {"d", "dz", "origin", "h", "target", "mean", "q10", "q50", "q90"},
        "calendar.json": {"div", "dz", "w", "idx", "action"},
        "dq.json": {"batch_id", "source", "rows_in", "rows_clean", "rows_quarantined", "rules"},
    }
    for name, keys in shapes.items():
        assert all(set(r) - {"gate"} == keys for r in f[name]), name
    assert all(r["gate"] is False for r in f["alerts.json"] if "gate" in r)
    alloc = f["allocation.json"]
    assert set(alloc) == {"scenarios", "rows"}
    assert all(set(s) == {"id", "budget", "alpha"} for s in alloc["scenarios"])
    assert all(set(r) == {"id", "d", "r", "alloc", "unmet", "demand", "stock"} for r in alloc["rows"])


def test_coverage_64_districts_4_diseases(exports):
    _, f = exports
    w = f["weekly.json"]
    assert len({r["d"] for r in w}) == 64
    assert {r["dz"] for r in w} == set(f["meta.json"]["diseases"]) == {"dengue", "diarrhoea", "ili", "malaria"}
    assert len({r["w"] for r in w}) == 120


def test_suppression_hides_small_counts(exports):
    _, f = exports
    w = f["weekly.json"]
    suppressed = [r for r in w if r["s"]]
    assert suppressed, "mock data must contain suppressed cells"
    assert all(r["c"] is None and r["inc"] is None for r in suppressed)
    assert not any(r["c"] is not None and 1 <= r["c"] <= 4 for r in w)
    # A stat on a suppressed week would leak the count.
    hidden = {(r["d"], r["dz"], r["w"]) for r in suppressed}
    assert all(a["stat"] is None for a in f["alerts.json"] if (a["d"], a["dz"], a["w"]) in hidden)
    # Secondary suppression: a district-week line across diseases with exactly one hidden cell must have no
    # visible non-zero cell left (otherwise the hidden one could be recovered from a total).
    lines = {}
    for r in w:
        lines.setdefault((r["d"], r["w"]), []).append(r)
    for cells in lines.values():
        if sum(c["s"] for c in cells) == 1:
            assert not any(not c["s"] and c["c"] for c in cells)


def test_scenario_grid_is_complete(exports):
    _, f = exports
    alloc = f["allocation.json"]
    budgets = {s["budget"] for s in alloc["scenarios"]}
    alphas = {s["alpha"] for s in alloc["scenarios"]}
    assert len(budgets) >= 5 and len(alphas) >= 5
    assert len(alloc["scenarios"]) == len(budgets) * len(alphas)
    ids = {s["id"] for s in alloc["scenarios"]}
    assert {r["id"] for r in alloc["rows"]} == ids
    assert all(r["alloc"] >= 0 and r["unmet"] >= 0 for r in alloc["rows"])


def test_forecast_quantiles_ordered(exports):
    _, f = exports
    assert all(r["q10"] <= r["q50"] <= r["q90"] and 1 <= r["h"] <= 4 for r in f["forecasts.json"])


def _dashboard_sources():
    return [p for p in DASH.rglob("*") if p.suffix in {".html", ".js", ".mjs", ".css"} and "vendor" not in p.parts]


def test_no_forbidden_chart_libraries():
    for p in _dashboard_sources():
        text = p.read_text().lower()
        for lib in ("plotly", "chart.js", "chartjs", "vega"):
            assert lib not in text, f"{p} mentions {lib}"


def test_vendored_scripts_match_sri():
    html = (DASH / "index.html").read_text()
    tags = re.findall(r'<script src="([^"]+)"\s+integrity="(sha384-[^"]+)"', html)
    assert len(tags) == 2
    for src, sri in tags:
        digest = base64.b64encode(hashlib.sha384((DASH / src).read_bytes()).digest()).decode()
        assert sri == f"sha384-{digest}", src
    assert "d3.v7" in html


def test_module_imports_resolve_and_ids_cited():
    for p in (DASH / "js").rglob("*.js"):
        text = p.read_text()
        for imp in re.findall(r'from\s+"(\.[^"]+)"', text):
            assert (p.parent / imp).resolve().exists(), f"{p}: {imp}"
        if "views" in p.parts or p.name in {"main.js", "data.js", "state.js", "util.js"}:
            assert re.search(r"VIS-\d", text.split("\n\n")[0]), f"{p} header must cite VIS IDs"


def test_dashboard_reads_only_data_dir():
    for p in (DASH / "js").rglob("*.js"):
        for url in re.findall(r'(?:d3\.json|fetch)\(\s*[`"]([^`"]+)', p.read_text()):
            # data/ is the national bundle, roles/<district>/ a district user's bundle (PRV-3); both are serving output
            ok = url.startswith(("data/", "roles/")) or (url.startswith("${base}/")
                                                         and 'const base = role ? `roles/${role}` : "data";' in p.read_text())
            assert ok, f"{p} fetches {url}"
