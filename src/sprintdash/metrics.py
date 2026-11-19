"""Sprint metrics on the standard task table (see data.py).

Definitions used throughout:
  committed points   story points of every task in the sprint (carried over and added mid-sprint included)
  completed points   story points of tasks finished in the sprint (velocity)
  say/do ratio       completed / committed points
  carry-over         tasks moved in from an earlier sprint unfinished
  lead time          created -> resolved (waiting + working), in days
  cycle time         started -> resolved (working only), in days
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _pts(df: pd.DataFrame) -> pd.Series:
    return df["story_points"].fillna(0)


def sprint_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(pts=_pts(df), done_pts=_pts(df) * df["is_done"])
    out = g.groupby("sprint", observed=True).agg(
        committed=("pts", "sum"),
        completed=("done_pts", "sum"),
        tasks=("issue_key", "size"),
        done_tasks=("is_done", "sum"),
        carried=("carried_over", "mean"),
        added=("added_mid_sprint", "mean"),
        lead_median=("lead_days", "median"),
        cycle_median=("cycle_days", "median"),
    )
    done = df[df["is_done"]]
    out["bug_share"] = done.groupby("sprint", observed=True)["issue_type"].apply(lambda s: (s == "Bug").mean())
    out["say_do"] = out["completed"] / out["committed"].replace(0, np.nan)
    return out.reset_index()


def headcount(df: pd.DataFrame, configured: dict[str, int]) -> pd.Series:
    seen = df.groupby("team")["resource"].nunique()
    teams = sorted(set(seen.index) | set(configured))
    return pd.Series({t: configured.get(t, int(seen.get(t, 0)) or 1) for t in teams})


def team_summary(df: pd.DataFrame, configured: dict[str, int], order: list[str] | None = None) -> pd.DataFrame:
    n_sprints = max(df["sprint"].nunique(), 1)
    g = df.assign(pts=_pts(df), done_pts=_pts(df) * df["is_done"])
    out = g.groupby("team").agg(
        committed=("pts", "sum"),
        completed=("done_pts", "sum"),
        done_tasks=("is_done", "sum"),
        carried=("carried_over", "mean"),
        cycle_median=("cycle_days", "median"),
        cycle_p85=("cycle_days", lambda s: s.quantile(0.85)),
    )
    done = df[df["is_done"]]
    out["bug_share"] = done.groupby("team")["issue_type"].apply(lambda s: (s == "Bug").mean())
    out["people"] = headcount(df, configured).reindex(out.index)
    out["say_do"] = out["completed"] / out["committed"].replace(0, np.nan)
    out["points_per_person"] = out["completed"] / out["people"] / n_sprints
    out["load_per_person"] = out["committed"] / out["people"] / n_sprints
    out = out.reset_index()
    if order:
        out["_o"] = out["team"].map({t: i for i, t in enumerate(order)}).fillna(len(order))
        out = out.sort_values(["_o", "team"]).drop(columns="_o")
    return out.reset_index(drop=True)


def by_sprint(df: pd.DataFrame, by: str, measure: str = "points", done_only: bool = True) -> pd.DataFrame:
    """Sprint x category table of completed points (or task counts)."""
    d = df[df["is_done"]] if done_only else df
    vals = _pts(d) if measure == "points" else pd.Series(1, index=d.index)
    return d.assign(v=vals).pivot_table(index="sprint", columns=by, values="v", aggfunc="sum", observed=True).fillna(0)


def shares(table: pd.DataFrame) -> pd.DataFrame:
    return table.div(table.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def flow(df: pd.DataFrame, by: str = "team", kind: str = "cycle_days") -> pd.DataFrame:
    d = df[df["is_done"] & df[kind].notna()]
    out = d.groupby(by)[kind].describe(percentiles=[0.5, 0.85])[["count", "50%", "85%", "max"]]
    return out.rename(columns={"50%": "median", "85%": "p85"}).reset_index()


def bugs_by_sprint(df: pd.DataFrame, sprints: pd.DataFrame) -> pd.DataFrame:
    """Bugs raised during each sprint vs bugs fixed in it."""
    bugs = df[df["issue_type"] == "Bug"].drop_duplicates("issue_key", keep="first")
    rows = []
    for _, s in sprints.iterrows():
        end = s["end"] + pd.Timedelta(days=1)
        raised = bugs[(bugs["created"] >= s["start"]) & (bugs["created"] < end)]
        fixed = df[(df["issue_type"] == "Bug") & df["is_done"] & (df["sprint"] == s["sprint"])]
        rows.append({"sprint": s["sprint"], "raised": len(raised), "fixed": len(fixed),
                     "high_raised": int(raised["priority"].isin(["Highest", "High"]).sum())})  # fmt: skip
    return pd.DataFrame(rows)


def workload(df: pd.DataFrame) -> pd.DataFrame:
    n_sprints = max(df["sprint"].nunique(), 1)
    g = df.assign(pts=_pts(df), done_pts=_pts(df) * df["is_done"])
    out = g.groupby(["team", "resource"]).agg(
        committed=("pts", "sum"), completed=("done_pts", "sum"), tasks=("issue_key", "size"),
        carried=("carried_over", "sum"),
    )  # fmt: skip
    out["completed_per_sprint"] = out["completed"] / n_sprints
    out["say_do"] = out["completed"] / out["committed"].replace(0, np.nan)
    return out.reset_index()


def headline(summary: pd.DataFrame) -> dict:
    """Latest sprint against the average of the sprints before it (in the selection)."""
    if summary.empty:
        return {}
    last, prev = summary.iloc[-1], summary.iloc[:-1]

    def pair(col):
        before = prev[col].mean() if len(prev) else np.nan
        return float(last[col]), float(before)

    return {
        "sprint": str(last["sprint"]),
        "completed": pair("completed"),
        "say_do": pair("say_do"),
        "carried": pair("carried"),
        "bug_share": pair("bug_share"),
        "cycle_median": pair("cycle_median"),
        "lead_median": pair("lead_median"),
    }


def forecast(summary: pd.DataFrame, window: int = 3) -> dict:
    """Expected velocity next sprint: mean of the last sprints, with the range seen recently."""
    v = summary["completed"].tail(max(window, 1))
    recent = summary["completed"].tail(6)
    return {"expected": float(v.mean()), "low": float(recent.min()), "high": float(recent.max()), "window": len(v)}


def insights(df: pd.DataFrame, summary: pd.DataFrame, teams: pd.DataFrame) -> list[str]:
    """Plain-language findings for the current selection."""
    out: list[str] = []
    if len(summary) >= 2:
        avg = summary["say_do"].mean()
        worst = summary.loc[summary["say_do"].idxmin()]
        if worst["say_do"] < avg - 0.08:
            out.append(f"**{worst['sprint']}** delivered {worst['say_do']:.0%} of its commitment, against {avg:.0%} on average.")
    if len(teams) >= 2 and teams["say_do"].notna().any():
        low = teams.loc[teams["say_do"].idxmin()]
        load = teams.loc[teams["load_per_person"].idxmax()]
        out.append(
            f"**{low['team']}** completes the smallest share of its commitment ({low['say_do']:.0%}) "
            f"and carries over {low['carried']:.0%} of its tasks."
        )
        if load["team"] == low["team"]:
            out.append(
                f"{load['team']} also has the highest load per person: {load['load_per_person']:.1f} points "
                f"committed per person per sprint, against {teams['load_per_person'].median():.1f} for a typical team."
            )
    src = shares(by_sprint(df, "request_source"))
    if len(src) >= 4:
        others = [c for c in src.columns if c not in ("Roadmap", "Not set")]
        unplanned = src[others].sum(axis=1)
        top = unplanned.idxmax()
        if unplanned[top] > unplanned.median() + 0.08:
            out.append(
                f"In **{top}**, {unplanned[top]:.0%} of completed points went to Sales, change requests and "
                f"support, against {unplanned.median():.0%} in a typical sprint."
            )
    bugs = df[df["is_done"]].groupby(["team", "sprint"], observed=True)["issue_type"].apply(lambda s: (s == "Bug").mean())
    bugs = bugs[df[df["is_done"]].groupby(["team", "sprint"], observed=True).size() >= 8]
    if len(bugs):
        excess = bugs - bugs.groupby(level="team").transform("median")
        (team, sprint) = excess.idxmax()
        share, typical = bugs[(team, sprint)], bugs.xs(team, level="team").median()
        if share > typical + 0.15:
            out.append(f"**{team}** spent {share:.0%} of its work on bugs in {sprint}, against {typical:.0%} usually.")
    lt = flow(df, "issue_type", "lead_days")
    if len(lt) >= 2:
        slow = lt.loc[lt["median"].idxmax()]
        out.append(f"**{slow['issue_type']}** items wait longest: median {slow['median']:.0f} days from request to done.")
    return out
