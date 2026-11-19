"""Read the sprint CSV, rename columns to standard names and check the data.

The dashboard only ever sees the standard names below, so another Jira export works
by changing the column mapping in config.yaml, not the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

REQUIRED = ["issue_key", "issue_type", "status", "sprint", "sprint_start", "sprint_end", "team"]
OPTIONAL = [
    "summary", "priority", "resource", "request_source", "story_points", "created", "started",
    "resolved", "epic", "carried_over", "added_mid_sprint",
]  # fmt: skip
DATES = ["sprint_start", "sprint_end", "created", "started", "resolved"]
FLAGS = ["carried_over", "added_mid_sprint"]
YES = {"yes", "y", "true", "1", "x"}


class DataError(ValueError):
    """The CSV cannot be used as it is."""


@dataclass
class Config:
    csv_path: Path
    columns: dict[str, str]
    done_statuses: list[str]
    team_headcount: dict[str, int] = field(default_factory=dict)
    team_order: list[str] = field(default_factory=list)
    request_source_order: list[str] = field(default_factory=list)


def load_config(path: Path | str = "config.yaml") -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    csv = Path(raw.get("csv_path", "data/sprint_tasks.csv"))
    if not csv.is_absolute():
        csv = path.parent / csv
    cols = raw.get("columns") or {}
    return Config(
        csv_path=csv,
        columns={std: str(cols.get(std, std)) for std in REQUIRED + OPTIONAL},
        done_statuses=list(raw.get("done_statuses") or ["Done"]),
        team_headcount={str(k): int(v) for k, v in (raw.get("team_headcount") or {}).items()},
        team_order=list(raw.get("team_order") or []),
        request_source_order=list(raw.get("request_source_order") or []),
    )


@dataclass
class Dataset:
    tasks: pd.DataFrame  # one row per task per sprint, standard column names
    sprints: pd.DataFrame  # one row per sprint, ordered by start date
    findings: pd.DataFrame  # data-quality findings: check, rows, example
    present: set[str]  # optional columns that exist in the file
    source: Path
    modified: float  # file modification time


def _flag(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(YES)


def load(cfg: Config, path: Path | None = None) -> Dataset:
    path = Path(path or cfg.csv_path)
    if not path.exists():
        raise DataError(f"CSV not found: {path}")
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    inverse = {src: std for std, src in cfg.columns.items()}
    missing = [cfg.columns[c] for c in REQUIRED if cfg.columns[c] not in raw.columns]
    if missing:
        raise DataError(
            "The CSV is missing required columns: " + ", ".join(missing)
            + ". Rename them in the file or map them in config.yaml."
        )  # fmt: skip
    df = raw[[c for c in raw.columns if c in inverse]].rename(columns=inverse)
    present = {c for c in OPTIONAL if c in df.columns}
    for c in OPTIONAL:
        if c not in df.columns:
            df[c] = pd.NA

    for c in ["issue_key", "issue_type", "status", "sprint", "team", "resource", "request_source", "priority"]:
        df[c] = df[c].astype("string").str.strip()
    for c in DATES:
        df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=False, format="mixed")
    df["story_points"] = pd.to_numeric(df["story_points"], errors="coerce")
    for c in FLAGS:
        df[c] = _flag(df[c]) if c in present else False
    done = {s.lower() for s in cfg.done_statuses}
    df["is_done"] = df["status"].str.lower().isin(done).fillna(False).astype(bool)
    df["request_source"] = df["request_source"].fillna("Not set")

    findings = quality_checks(df, present)
    df = df.dropna(subset=["sprint", "sprint_start"]).copy()
    if df.empty:
        raise DataError("No rows with a sprint name and a sprint start date.")

    sprints = (
        df.groupby("sprint", as_index=False)
        .agg(start=("sprint_start", "min"), end=("sprint_end", "max"))
        .sort_values("start")
        .reset_index(drop=True)
    )
    sprints["n"] = range(1, len(sprints) + 1)
    order = list(sprints["sprint"])
    df["sprint"] = pd.Categorical(df["sprint"], categories=order, ordered=True)
    df["lead_days"] = (df["resolved"] - df["created"]).dt.total_seconds() / 86400
    df["cycle_days"] = (df["resolved"] - df["started"]).dt.total_seconds() / 86400
    df.loc[~df["is_done"], ["lead_days", "cycle_days"]] = pd.NA
    df.loc[df["lead_days"] < 0, "lead_days"] = pd.NA
    df.loc[df["cycle_days"] < 0, "cycle_days"] = pd.NA
    return Dataset(df.reset_index(drop=True), sprints, findings, present, path, path.stat().st_mtime)


def quality_checks(df: pd.DataFrame, present: set[str]) -> pd.DataFrame:
    """Problems in the Jira data itself, so they can be fixed at the source."""
    checks = []

    def add(name: str, mask: pd.Series, advice: str):
        mask = mask.fillna(False)
        if mask.any():
            checks.append({
                "check": name, "rows": int(mask.sum()), "advice": advice,
                "examples": ", ".join(df.loc[mask, "issue_key"].dropna().astype(str).head(5)),
            })  # fmt: skip

    add("No sprint name or start date", df["sprint"].isna() | df["sprint_start"].isna(),
        "Rows are left out. Export with the Sprint field and the sprint dates.")  # fmt: skip
    add("No team", df["team"].isna(), "Set the resource's team so the task counts for a team.")
    if "story_points" in present:
        add("Done without story points", df["is_done"] & df["story_points"].isna(),
            "Velocity undercounts these tasks. Estimate before closing.")  # fmt: skip
    if "resolved" in present:
        add("Done without a resolved date", df["is_done"] & df["resolved"].isna(),
            "Lead time is missing for these tasks.")  # fmt: skip
        add("Resolved before created", df["resolved"] < df["created"], "Check the dates in Jira.")
    if "started" in present:
        add("Done without a start date", df["is_done"] & df["started"].isna(),
            "Cycle time is missing; move the ticket to In Progress when work starts.")  # fmt: skip
    add("Same task twice in one sprint", df.duplicated(["issue_key", "sprint"], keep=False),
        "Duplicates inflate the numbers; the export may contain repeated rows.")  # fmt: skip
    if "request_source" in present:
        add("No request source", df["request_source"].isna() | (df["request_source"] == "Not set"),
            "Set who requested the work (Roadmap, Sales, Change Request...).")  # fmt: skip
    return pd.DataFrame(checks, columns=["check", "rows", "advice", "examples"])
