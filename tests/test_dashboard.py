from pathlib import Path

import pandas as pd
import pytest

from sprintdash import metrics as m
from sprintdash.data import DataError, load, load_config
from sprintdash.mockdata import TEAMS, generate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    d = tmp_path_factory.mktemp("d")
    generate().to_csv(d / "tasks.csv", index=False)
    cfg = load_config(ROOT / "config.yaml")
    return cfg, load(cfg, d / "tasks.csv")


def test_mock_data_matches_team_sizes():
    df = generate()
    people = df.groupby("resource_team")["resource_name"].nunique().to_dict()
    assert people == {t: v[0] for t, v in TEAMS.items()}
    assert df["sprint_name"].nunique() == 10
    assert df.equals(generate())  # reproducible


def test_load_and_derived_columns(ds):
    cfg, d = ds
    assert list(d.sprints["sprint"])[:2] == ["Sprint 31", "Sprint 32"]
    t = d.tasks
    assert t["is_done"].dtype == bool and t["carried_over"].dtype == bool
    assert (t.loc[t["is_done"], "lead_days"].dropna() >= 0).all()
    assert t.loc[~t["is_done"], "lead_days"].isna().all()
    assert d.findings.empty


def test_column_mapping(tmp_path, ds):
    cfg, d = ds
    raw = generate().rename(columns={"sprint_name": "Sprint", "resource_team": "Squad"})
    raw.to_csv(tmp_path / "jira.csv", index=False)
    cfg.columns.update(sprint="Sprint", team="Squad")
    try:
        assert len(load(cfg, tmp_path / "jira.csv").tasks) == len(d.tasks)
    finally:
        cfg.columns.update(sprint="sprint_name", team="resource_team")


def test_missing_required_column(tmp_path, ds):
    cfg, _ = ds
    generate().drop(columns="resource_team").to_csv(tmp_path / "x.csv", index=False)
    with pytest.raises(DataError, match="resource_team"):
        load(cfg, tmp_path / "x.csv")


def test_optional_columns_can_be_missing(tmp_path, ds):
    cfg, _ = ds
    minimal = generate()[["issue_key", "issue_type", "status", "sprint_name", "sprint_start", "sprint_end", "resource_team"]]
    minimal.to_csv(tmp_path / "min.csv", index=False)
    d = load(cfg, tmp_path / "min.csv")
    assert d.present == set()
    s = m.sprint_summary(d.tasks)
    assert (s["completed"] == 0).all() and len(s) == 10  # no story points: counts still work


def test_quality_checks(tmp_path, ds):
    cfg, _ = ds
    df = generate()
    done = df.index[df["status"] == "Done"][-3:]
    df.loc[done, "story_points"] = None
    df.loc[done[:1], "resource_team"] = None
    df = pd.concat([df, df.head(2)])
    df.to_csv(tmp_path / "bad.csv", index=False)
    f = load(cfg, tmp_path / "bad.csv").findings.set_index("check")["rows"]
    assert f["Done without story points"] == 3 and f["No team"] == 1 and f["Same task twice in one sprint"] == 4


def test_sprint_summary_definitions(ds):
    _, d = ds
    s = m.sprint_summary(d.tasks)
    t = d.tasks
    first = t[t["sprint"] == "Sprint 31"]
    assert s.iloc[0]["committed"] == first["story_points"].sum()
    assert s.iloc[0]["completed"] == first.loc[first["is_done"], "story_points"].sum()
    assert s.iloc[0]["carried"] == 0  # nothing to carry into the first sprint
    assert 0.6 < s["say_do"].mean() < 0.9


def test_planted_stories(ds):
    cfg, d = ds
    t = d.tasks
    s = m.sprint_summary(t).set_index("sprint")
    assert s["say_do"].idxmin() in ("Sprint 37", "Sprint 38", "Sprint 39")
    teams = m.team_summary(t, cfg.team_headcount, cfg.team_order).set_index("team")
    assert teams["say_do"].idxmin() == "HTML" and teams["load_per_person"].idxmax() == "HTML"
    assert list(teams.index) == cfg.team_order
    src = m.shares(m.by_sprint(t, "request_source"))
    assert src.loc["Sprint 39", "Sales"] > 2 * src.loc["Sprint 32", "Sales"]
    lead = m.flow(t, "issue_type", "lead_days").set_index("issue_type")["median"]
    assert lead.idxmax() == "Change Request"
    text = " ".join(m.insights(t, m.sprint_summary(t), m.team_summary(t, cfg.team_headcount)))
    assert "HTML" in text and "iOS" in text and "Change Request" in text


def test_forecast_and_headline(ds):
    _, d = ds
    s = m.sprint_summary(d.tasks)
    f = m.forecast(s)
    assert f["low"] <= f["expected"] <= f["high"]
    h = m.headline(s)
    assert h["sprint"] == "Sprint 40" and h["completed"][0] == s.iloc[-1]["completed"]


def test_headcount_from_config_or_data(ds):
    cfg, d = ds
    hc = m.headcount(d.tasks, {"iOS": 12})
    assert hc["iOS"] == 12 and hc["HTML"] == 2


def test_streamlit_app_runs():
    st_test = pytest.importorskip("streamlit.testing.v1")
    at = st_test.AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception
    assert [x.label for x in at.metric][0].startswith("Velocity")
    at.sidebar.multiselect[0].set_value(["HTML"]).run()
    assert not at.exception
    at.sidebar.radio[0].set_value("Tasks").run()
    assert not at.exception
