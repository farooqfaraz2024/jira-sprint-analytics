"""Sprint analytics dashboard.

    streamlit run app.py                         # uses config.yaml
    streamlit run app.py -- --config other.yaml

Reads the CSV named in config.yaml. Replace that file with a new export at any time:
the dashboard checks it every 30 seconds and reloads when it has changed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))
from sprintdash import metrics as m  # noqa: E402
from sprintdash.data import DataError, load, load_config  # noqa: E402

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TEAM_COLORS = {"UX/UI": SLOTS[0], "iOS": SLOTS[1], "Android": SLOTS[2], "React": SLOTS[3], "HTML": SLOTS[4],
               "Services": SLOTS[6]}  # fmt: skip
SOURCE_COLORS = {"Roadmap": SLOTS[0], "Sales": SLOTS[1], "Change Request": SLOTS[6], "Production Support": SLOTS[7],
                 "Not set": "#a3a29b"}  # fmt: skip
TYPE_COLORS = {"Story": SLOTS[0], "Task": SLOTS[2], "Bug": SLOTS[7], "Change Request": SLOTS[6], "Spike": SLOTS[3]}
SEQ = ["#e8f1fd", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
DIV = [[0, "#d03b3b"], [0.5, "#f0efec"], [1, "#2a78d6"]]


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    return ap.parse_known_args()[0]


@st.cache_data(show_spinner="Reading the CSV…")
def get_data(config_path: str, mtime: float):  # mtime in the key: a new file means a new read
    cfg = load_config(config_path)
    return cfg, load(cfg)


def style(fig: go.Figure, height: int = 380, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=78 if legend else 48, b=8), hovermode="x unified",
        title=dict(y=0.99, yref="container", yanchor="top", x=0, xanchor="left", font=dict(size=15)),
        legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, title=None) if legend else dict(visible=False),
        font=dict(family="Inter, system-ui, sans-serif"), plot_bgcolor="rgba(0,0,0,0)",
    )  # fmt: skip
    fig.update_xaxes(title=None, showgrid=False)
    fig.update_yaxes(title=None, gridcolor="rgba(128,128,128,.18)")
    return fig


def order_cols(t: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    cols = [c for c in order if c in t.columns] + [c for c in t.columns if c not in order]
    return t[cols]


def main() -> None:
    st.set_page_config(page_title="Sprint Analytics", page_icon="📊", layout="wide")
    config_path = args().config
    try:
        cfg0 = load_config(config_path)
        mtime = cfg0.csv_path.stat().st_mtime
        cfg, ds = get_data(config_path, mtime)
    except (DataError, FileNotFoundError, OSError) as exc:
        st.error(f"Cannot read the data: {exc}")
        st.stop()

    @st.fragment(run_every="30s")
    def watch():
        try:
            if cfg.csv_path.stat().st_mtime != ds.modified:
                st.rerun()
        except OSError:
            pass

    watch()

    # ---------------- filters ----------------
    sb = st.sidebar
    sb.header("Filters")
    names = list(ds.sprints["sprint"])
    default_from = names[max(0, len(names) - 10)]
    first, last = sb.select_slider("Sprints", options=names, value=(default_from, names[-1]))
    i0, i1 = names.index(first), names.index(last)
    chosen = names[i0 : i1 + 1]
    df_all = ds.tasks
    teams_all = [t for t in cfg.team_order if t in set(df_all["team"].dropna())] + sorted(
        set(df_all["team"].dropna()) - set(cfg.team_order)
    )
    teams = sb.multiselect("Teams", teams_all, placeholder="All teams")
    srcs_all = [s for s in cfg.request_source_order if s in set(df_all["request_source"])] + sorted(
        set(df_all["request_source"]) - set(cfg.request_source_order)
    )
    srcs = sb.multiselect("Requested by", srcs_all, placeholder="All sources")
    types = sb.multiselect("Issue type", sorted(df_all["issue_type"].dropna().unique()), placeholder="All types")
    prios = sb.multiselect("Priority", [p for p in ["Highest", "High", "Medium", "Low", "Lowest"]
                                        if p in set(df_all["priority"].dropna())], placeholder="All priorities")  # fmt: skip
    measure = sb.radio("Measure", ["Story points", "Tasks"], horizontal=True)
    meas = "points" if measure == "Story points" else "tasks"

    df = df_all[df_all["sprint"].isin(chosen)]
    for col, sel in (("team", teams), ("request_source", srcs), ("issue_type", types), ("priority", prios)):
        if sel:
            df = df[df[col].isin(sel)]
    sprints = ds.sprints[ds.sprints["sprint"].isin(chosen)]

    sb.divider()
    sb.caption(f"**Data:** `{ds.source.name}`  \nUpdated {datetime.fromtimestamp(ds.modified):%d %b %Y %H:%M}  \n"
               f"{len(df_all):,} rows · {df_all['issue_key'].nunique():,} tasks · {len(names)} sprints  \n"
               "The file is checked every 30 seconds.")  # fmt: skip
    if sb.button("Reload now"):
        st.cache_data.clear()
        st.rerun()

    st.title("Sprint analytics")
    team_txt = ", ".join(teams) if teams else "all teams"
    st.caption(f"{first} – {last} · {team_txt} · "
               f"{chosen and sprints['start'].min():%d %b} – {sprints['end'].max():%d %b %Y}")  # fmt: skip
    if df.empty:
        st.warning("No tasks match the filters.")
        st.stop()

    summary = m.sprint_summary(df)
    team_tbl = m.team_summary(df, cfg.team_headcount, cfg.team_order)
    h = m.headline(summary)
    fc = m.forecast(summary)

    # ---------------- KPIs ----------------
    def delta(cur, prev, fmt, pts=False):
        if pd.isna(prev) or pd.isna(cur):
            return None
        return f"{(cur - prev) * 100:+.0f} pts" if pts else fmt.format(cur - prev)

    c = st.columns(6)
    c[0].metric(f"Velocity ({h['sprint']})", f"{h['completed'][0]:,.0f} pts",
                delta(*h["completed"], "{:+,.0f} vs avg"), help="Story points completed in the latest sprint, "
                "compared with the average of the earlier sprints in the selection.")  # fmt: skip
    c[1].metric("Say/do ratio", f"{h['say_do'][0]:.0%}", delta(*h["say_do"], "", pts=True),
                help="Completed points / committed points.")  # fmt: skip
    c[2].metric("Carry-over", f"{h['carried'][0]:.0%}", delta(*h["carried"], "", pts=True), delta_color="inverse",
                help="Share of the sprint's tasks moved in unfinished from an earlier sprint.")  # fmt: skip
    c[3].metric("Bug share", f"{h['bug_share'][0]:.0%}", delta(*h["bug_share"], "", pts=True), delta_color="inverse",
                help="Share of completed tasks that were bugs.")  # fmt: skip
    c[4].metric("Cycle time (median)", f"{h['cycle_median'][0]:.1f} days",
                delta(*h["cycle_median"], "{:+.1f} days"), delta_color="inverse",
                help="Days from work started to done.")  # fmt: skip
    c[5].metric(
        "Next sprint",
        f"~{fc['expected']:,.0f} pts",
        f"range {fc['low']:,.0f}–{fc['high']:,.0f}",
        delta_color="off",
        help=f"Average of the last {fc['window']} sprints; range of the last 6.",
    )

    notes = m.insights(df, summary, team_tbl)
    if notes:
        with st.container(border=True):
            st.markdown("**What stands out**  \n" + "\n".join(f"- {n}" for n in notes))

    tabs = st.tabs(["Delivery", "Teams", "Work mix & requests", "Flow", "Quality", "Workload", "Data checks", "Data"])

    # ---------------- Delivery ----------------
    with tabs[0]:
        left, right = st.columns([1.6, 1])
        long = summary.melt("sprint", ["committed", "completed"], var_name="kind", value_name="points")
        fig = px.bar(long, x="sprint", y="points", color="kind", barmode="group",
                     color_discrete_map={"committed": "#9ec5f4", "completed": "#1c5cab"},
                     title="Committed vs completed story points")  # fmt: skip
        left.plotly_chart(style(fig), width="stretch")
        fig = px.line(summary, x="sprint", y="say_do", markers=True, title="Say/do ratio")
        fig.update_traces(line_color=SLOTS[0], line_width=2.5)
        fig.add_hline(
            y=summary["say_do"].mean(),
            line_dash="dot",
            line_color="#7c7b75",
            annotation_text=f"average {summary['say_do'].mean():.0%}",
            annotation_position="bottom right",
        )
        fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
        right.plotly_chart(style(fig, legend=False), width="stretch")
        left2, right2 = st.columns([1.6, 1])
        vt = order_cols(m.by_sprint(df, "team", meas), cfg.team_order)
        fig = px.line(vt, markers=True, color_discrete_map=TEAM_COLORS,
                      title=f"Completed {'points' if meas == 'points' else 'tasks'} by team")  # fmt: skip
        left2.plotly_chart(style(fig), width="stretch")
        spill = summary[["sprint", "carried", "added"]].melt("sprint", var_name="kind", value_name="share")
        spill["kind"] = spill["kind"].map({"carried": "Carried over", "added": "Added mid-sprint"})
        fig = px.bar(spill, x="sprint", y="share", color="kind", barmode="group",
                     color_discrete_sequence=[SLOTS[1], SLOTS[6]], title="Unplanned and unfinished work")  # fmt: skip
        fig.update_yaxes(tickformat=".0%")
        right2.plotly_chart(style(fig), width="stretch")

    # ---------------- Teams ----------------
    with tabs[1]:
        shown = team_tbl[
            [
                "team",
                "people",
                "completed",
                "points_per_person",
                "load_per_person",
                "say_do",
                "carried",
                "bug_share",
                "cycle_median",
                "cycle_p85",
            ]
        ].copy()
        shown[["say_do", "carried", "bug_share"]] = shown[["say_do", "carried", "bug_share"]] * 100
        st.dataframe(
            shown,
            hide_index=True, width="stretch",
            column_config={
                "team": "Team", "people": st.column_config.NumberColumn("People", help="From config.yaml"),
                "completed": st.column_config.NumberColumn("Points done", format="%d"),
                "points_per_person": st.column_config.NumberColumn("Done / person / sprint", format="%.1f"),
                "load_per_person": st.column_config.NumberColumn("Committed / person / sprint", format="%.1f"),
                "say_do": st.column_config.ProgressColumn("Say/do", format="%.0f%%", min_value=0, max_value=100),
                "carried": st.column_config.NumberColumn("Carry-over", format="%.0f%%"),
                "bug_share": st.column_config.NumberColumn("Bugs", format="%.0f%%"),
                "cycle_median": st.column_config.NumberColumn("Cycle days (median)", format="%.1f"),
                "cycle_p85": st.column_config.NumberColumn("Cycle days (85%)", format="%.1f"),
            },
        )  # fmt: skip
        left, right = st.columns(2)
        pp = team_tbl.melt("team", ["load_per_person", "points_per_person"], var_name="kind", value_name="points")
        pp["kind"] = pp["kind"].map({"load_per_person": "Committed", "points_per_person": "Completed"})
        fig = px.bar(pp, x="team", y="points", color="kind", barmode="group",
                     color_discrete_map={"Committed": "#9ec5f4", "Completed": "#1c5cab"},
                     title="Story points per person per sprint")  # fmt: skip
        left.plotly_chart(style(fig), width="stretch")
        g = df.assign(p=df["story_points"].fillna(0), d=df["story_points"].fillna(0) * df["is_done"])
        sd = g.groupby(["team", "sprint"], observed=True)[["p", "d"]].sum()
        heat = (sd["d"] / sd["p"].replace(0, pd.NA)).unstack("sprint")
        heat = heat.reindex([t for t in cfg.team_order if t in heat.index] + [t for t in heat.index if t not in cfg.team_order])
        fig = px.imshow(heat.astype(float), color_continuous_scale=DIV, zmin=0.4, zmax=1.0, aspect="auto",
                        text_auto=".0%", title="Say/do ratio by team and sprint")  # fmt: skip
        fig.update_coloraxes(colorbar_tickformat=".0%")
        right.plotly_chart(style(fig, legend=False), width="stretch")

    # ---------------- Work mix ----------------
    with tabs[2]:
        left, right = st.columns(2)
        mix = order_cols(m.shares(m.by_sprint(df, "issue_type", meas)), list(TYPE_COLORS))
        fig = px.bar(mix, color_discrete_map=TYPE_COLORS, title="Completed work by issue type")
        fig.update_yaxes(tickformat=".0%")
        left.plotly_chart(style(fig), width="stretch")
        src = order_cols(m.shares(m.by_sprint(df, "request_source", meas)), list(SOURCE_COLORS))
        fig = px.bar(src, color_discrete_map=SOURCE_COLORS, title="Completed work by who requested it")
        fig.update_yaxes(tickformat=".0%")
        right.plotly_chart(style(fig), width="stretch")
        d = df[df["is_done"]].assign(v=df["story_points"].fillna(0) if meas == "points" else 1)
        ts = d.pivot_table(index="team", columns="request_source", values="v", aggfunc="sum").fillna(0)
        ts = order_cols(m.shares(ts), list(SOURCE_COLORS))
        ts = ts.reindex([t for t in cfg.team_order if t in ts.index] + [t for t in ts.index if t not in cfg.team_order])
        fig = px.bar(ts, orientation="h", color_discrete_map=SOURCE_COLORS,
                     title="Where each team's capacity goes")  # fmt: skip
        fig.update_xaxes(tickformat=".0%")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(style(fig, 320).update_layout(hovermode="y unified"), width="stretch")
        ep = d.groupby("epic")["v"].sum().sort_values(ascending=True).tail(12)
        if len(ep):
            fig = px.bar(ep, orientation="h", title="Completed work by epic", color_discrete_sequence=[SLOTS[0]])
            st.plotly_chart(style(fig, 360, legend=False).update_layout(hovermode="y unified"), width="stretch")

    # ---------------- Flow ----------------
    with tabs[3]:
        if "started" not in ds.present and "created" not in ds.present:
            st.info("Add created, started and resolved dates to the CSV to see lead and cycle times.")
        else:
            left, right = st.columns(2)
            done = df[df["is_done"]]
            fig = px.box(done, x="team", y="cycle_days", color="team", color_discrete_map=TEAM_COLORS, points=False,
                         category_orders={"team": cfg.team_order}, title="Cycle time by team (days, started → done)")  # fmt: skip
            left.plotly_chart(style(fig, legend=False).update_layout(hovermode="closest"), width="stretch")
            fig = px.box(done, x="issue_type", y="lead_days", color="issue_type", color_discrete_map=TYPE_COLORS,
                         points=False, title="Lead time by issue type (days, created → done)")  # fmt: skip
            right.plotly_chart(style(fig, legend=False).update_layout(hovermode="closest"), width="stretch")
            ft = m.flow(df, "team", "cycle_days").merge(m.flow(df, "team", "lead_days"), on="team", suffixes=("_cycle", "_lead"))
            ft = ft.assign(_o=ft["team"].map({t: i for i, t in enumerate(cfg.team_order)}).fillna(99)).sort_values("_o")
            st.dataframe(ft[["team", "count_cycle", "median_cycle", "p85_cycle", "median_lead", "p85_lead"]], hide_index=True,
                         width="stretch", column_config={
                             "team": "Team", "count_cycle": st.column_config.NumberColumn("Tasks", format="%d"),
                             "median_cycle": st.column_config.NumberColumn("Cycle median", format="%.1f d"),
                             "p85_cycle": st.column_config.NumberColumn("Cycle 85%", format="%.1f d"),
                             "median_lead": st.column_config.NumberColumn("Lead median", format="%.1f d"),
                             "p85_lead": st.column_config.NumberColumn("Lead 85%", format="%.1f d")})  # fmt: skip
            st.markdown("**Longest waits from request to done**")
            slow = done.nlargest(10, "lead_days")[["issue_key", "summary", "issue_type", "team", "request_source",
                                                   "created", "resolved", "lead_days"]]  # fmt: skip
            st.dataframe(
                slow,
                hide_index=True,
                width="stretch",
                column_config={
                    "issue_key": "Key",
                    "summary": "Summary",
                    "issue_type": "Type",
                    "team": "Team",
                    "request_source": "Requested by",
                    "created": st.column_config.DatetimeColumn("Created", format="D MMM YYYY"),
                    "resolved": st.column_config.DatetimeColumn("Done", format="D MMM YYYY"),
                    "lead_days": st.column_config.NumberColumn("Lead time", format="%.0f days"),
                },
            )

    # ---------------- Quality ----------------
    with tabs[4]:
        left, right = st.columns(2)
        b = m.bugs_by_sprint(df, sprints)
        fig = px.bar(b.melt("sprint", ["raised", "fixed"], var_name="kind", value_name="bugs"), x="sprint", y="bugs",
                     color="kind", barmode="group", color_discrete_map={"raised": SLOTS[7], "fixed": SLOTS[2]},
                     title="Bugs raised during the sprint vs fixed")  # fmt: skip
        left.plotly_chart(style(fig), width="stretch")
        dd = df[df["is_done"]]
        bs = dd.assign(bug=dd["issue_type"].eq("Bug")).pivot_table(index="team", columns="sprint", values="bug",
                                                                       aggfunc="mean", observed=True)  # fmt: skip
        bs = bs.reindex([t for t in cfg.team_order if t in bs.index] + [t for t in bs.index if t not in cfg.team_order])
        fig = px.imshow(bs.astype(float), color_continuous_scale=SEQ, zmin=0, aspect="auto", text_auto=".0%",
                        title="Share of completed work that was bugs")  # fmt: skip
        fig.update_coloraxes(colorbar_tickformat=".0%")
        right.plotly_chart(style(fig, legend=False), width="stretch")
        bp = df[df["issue_type"] == "Bug"].drop_duplicates("issue_key")
        if len(bp):
            fig = px.histogram(bp, x="team", color="priority", barmode="stack",
                               category_orders={"team": cfg.team_order, "priority": ["Highest", "High", "Medium", "Low"]},
                               color_discrete_map={"Highest": "#86201b", "High": SLOTS[7], "Medium": "#f6c6bd", "Low": "#d6d4cc"},
                               title="Bugs by team and priority")  # fmt: skip
            st.plotly_chart(style(fig, 320), width="stretch")

    # ---------------- Workload ----------------
    with tabs[5]:
        st.caption("For balancing work inside a team, not for ranking people: story points differ by type of work, "
                   "and design, review and support time is not in the points.")  # fmt: skip
        if "resource" not in ds.present:
            st.info("Add the resource (assignee) column to see workload per person.")
        else:
            team = st.selectbox("Team", teams_all if not teams else teams)
            w = m.workload(df[df["team"] == team])
            if w.empty:
                st.info("No tasks for this team in the selection.")
            else:
                w = w.sort_values("committed", ascending=False)
                fig = go.Figure([
                    go.Bar(x=w["resource"], y=w["committed"], name="Committed", marker_color="#9ec5f4"),
                    go.Bar(x=w["resource"], y=w["completed"], name="Completed", marker_color="#1c5cab"),
                ])  # fmt: skip
                fig.update_layout(barmode="group", title=f"{team}: story points per person, selected sprints")
                st.plotly_chart(style(fig), width="stretch")

    # ---------------- Data checks ----------------
    with tabs[6]:
        if ds.findings.empty:
            st.success("No data problems found in the file.")
        else:
            st.warning(
                f"{int(ds.findings['rows'].sum()):,} rows have data problems. Fixing them in Jira makes the numbers more accurate."
            )
            st.dataframe(ds.findings, hide_index=True, width="stretch")
        missing = sorted({"story_points", "started", "resolved", "request_source", "resource"} - ds.present)
        if missing:
            st.info("Optional columns not in the file: " + ", ".join(missing) + ". Adding them switches on more charts.")

    # ---------------- Data ----------------
    with tabs[7]:
        show = df.drop(columns=["lead_days", "cycle_days"]).copy()
        st.dataframe(show, hide_index=True, width="stretch", height=420)
        st.download_button("Download filtered rows (CSV)", show.to_csv(index=False), "sprint_tasks_filtered.csv")


if __name__ == "__main__":
    main()
