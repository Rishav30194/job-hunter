"""Streamlit dashboard — metrics, human queue, applied funnel, all jobs, analytics."""

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func, select

from src.db.models import Application, Job, PipelineRun
from src.db.session import get_session

st.set_page_config(page_title="Job Hunter", layout="wide")
st.title("Job Hunter Dashboard")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _query_metrics() -> dict:
    with get_session() as session:
        scanned = session.scalar(select(func.count(Job.id))) or 0
        high_match = session.scalar(
            select(func.count(Job.id)).where(Job.score >= 85)
        ) or 0
        applied = session.scalar(
            select(func.count(Job.id)).where(
                Job.status.in_(["applied", "phone_screen", "interview", "offer", "rejected"])
            )
        ) or 0
        outreach = session.scalar(
            select(func.count(Job.id)).where(Job.outreach_sent_at.isnot(None))
        ) or 0
        interviews = session.scalar(
            select(func.count(Job.id)).where(Job.status == "interview")
        ) or 0
    return dict(scanned=scanned, high_match=high_match, applied=applied,
                outreach=outreach, interviews=interviews)


def _query_human_review() -> list[dict]:
    with get_session() as session:
        rows = session.scalars(
            select(Job)
            .where(Job.status == "human_review")
            .order_by(Job.score.desc())
        ).all()
        return [_job_to_dict(j) for j in rows]


def _query_manual_apply() -> list[dict]:
    """queued_apply jobs on non-Indeed platforms — need manual one-click apply."""
    with get_session() as session:
        rows = session.scalars(
            select(Job)
            .where(Job.status == "queued_apply")
            .where(~Job.url.contains("indeed.com"))
            .order_by(Job.score.desc())
        ).all()
        return [_job_to_dict(j) for j in rows]


def _query_applied() -> list[dict]:
    with get_session() as session:
        rows = session.scalars(
            select(Job)
            .where(Job.status.in_(["applied", "phone_screen", "interview", "offer", "rejected"]))
            .order_by(Job.applied_at.desc())
        ).all()
        return [_job_to_dict(j) for j in rows]


def _query_all_jobs() -> list[dict]:
    with get_session() as session:
        rows = session.scalars(select(Job).order_by(Job.fetched_at.desc())).all()
        return [_job_to_dict(j) for j in rows]


def _query_score_distribution() -> list[int]:
    with get_session() as session:
        return session.scalars(
            select(Job.score).where(Job.score.isnot(None))
        ).all()


def _query_applications_over_time() -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            select(
                func.date(Job.applied_at).label("date"),
                func.count(Job.id).label("count"),
            )
            .where(Job.applied_at.isnot(None))
            .group_by(func.date(Job.applied_at))
            .order_by(func.date(Job.applied_at))
        ).all()
        return [{"date": str(r.date), "count": r.count} for r in rows]


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location or "",
        "work_type": job.work_type or "",
        "salary_text": job.salary_text or "",
        "score": job.score,
        "status": job.status,
        "url": job.url or "",
        "source": job.source or "",
        "posted_at": job.posted_at,
        "fetched_at": job.fetched_at,
        "applied_at": job.applied_at,
        "score_reasoning": job.score_reasoning or "",
        "visa_disqualified": job.visa_disqualified,
    }


# ---------------------------------------------------------------------------
# DB mutations
# ---------------------------------------------------------------------------

def _approve_job(job_id: str) -> None:
    """Move job to queued_apply and create an Application row for tracking."""
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "queued_apply"
            session.add(Application(job_id=job_id, method="manual_approve"))


def _skip_job(job_id: str) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "skipped"


# ---------------------------------------------------------------------------
# Metrics row
# ---------------------------------------------------------------------------

try:
    metrics = _query_metrics()
    _db_ok = True
except Exception as _db_err:
    _db_ok = False
    st.error(f"Database unavailable — {_db_err.__class__.__name__}: {_db_err}")
    metrics = dict(scanned=0, high_match=0, applied=0, outreach=0, interviews=0)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Scanned", metrics["scanned"])
c2.metric("High Match (≥85)", metrics["high_match"])
c3.metric("Applied", metrics["applied"])
c4.metric("Outreach Sent", metrics["outreach"])
c5.metric("Interviews", metrics["interviews"])

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_queue, tab_applied, tab_all, tab_analytics = st.tabs(
    ["Human Queue", "Applied", "All Jobs", "Analytics"]
)

# ── Human Queue ──────────────────────────────────────────────────────────────
with tab_queue:
    st.subheader("Review Queue")
    try:
        review_jobs = _query_human_review()
    except Exception as e:
        st.error(f"Could not load review queue: {e}")
        review_jobs = []

    if not review_jobs:
        st.info("No jobs awaiting review.")
    else:
        for job in review_jobs:
            with st.expander(f"**{job['title']}** @ {job['company']}  |  Score: {job['score']}"):
                col_l, col_r = st.columns([3, 1])
                with col_l:
                    st.write(f"**Location:** {job['location']}  |  **Work type:** {job['work_type']}")
                    st.write(f"**Salary:** {job['salary_text'] or 'Not listed'}")
                    if job["url"]:
                        st.markdown(f"[Open posting]({job['url']})")
                    if job["score_reasoning"]:
                        st.caption(job["score_reasoning"])
                with col_r:
                    if st.button("Approve", key=f"approve_{job['id']}", type="primary"):
                        _approve_job(job["id"])
                        st.success("Moved to apply queue.")
                        st.rerun()
                    if st.button("Skip", key=f"skip_{job['id']}"):
                        _skip_job(job["id"])
                        st.warning("Skipped.")
                        st.rerun()

    st.divider()
    st.subheader("Manual Apply Queue")
    st.caption("High-scoring jobs on Workday / Greenhouse / Oracle — auto-apply blocked; open and apply manually.")

    try:
        manual_jobs = _query_manual_apply()
    except Exception as e:
        st.error(f"Could not load manual apply queue: {e}")
        manual_jobs = []

    if not manual_jobs:
        st.info("No jobs pending manual apply.")
    else:
        for job in manual_jobs:
            col_title, col_score, col_btn = st.columns([4, 1, 1])
            col_title.write(f"**{job['title']}** @ {job['company']}  |  {job['location']}")
            col_score.write(f"Score: **{job['score']}**")
            if job["url"]:
                col_btn.link_button("Open & Apply", job["url"])
            else:
                col_btn.write("No URL")

# ── Applied ───────────────────────────────────────────────────────────────────
with tab_applied:
    try:
        applied_jobs = _query_applied()
    except Exception as e:
        st.error(f"Could not load applied jobs: {e}")
        applied_jobs = []

    # Funnel
    funnel_stages = ["applied", "phone_screen", "interview", "offer"]
    funnel_counts = {s: sum(1 for j in applied_jobs if j["status"] == s) for s in funnel_stages}
    rejected_count = sum(1 for j in applied_jobs if j["status"] == "rejected")

    st.subheader("Application Funnel")
    funnel_df = pd.DataFrame({
        "Stage": [s.replace("_", " ").title() for s in funnel_stages],
        "Count": [funnel_counts[s] for s in funnel_stages],
    })
    if funnel_df["Count"].sum() > 0:
        fig = px.funnel(funnel_df, x="Count", y="Stage")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No applications yet.")

    st.caption(f"Rejected: {rejected_count}")

    st.subheader("Application Log")
    if applied_jobs:
        df = pd.DataFrame(applied_jobs)[
            ["title", "company", "location", "score", "status", "applied_at", "url"]
        ]
        df["applied_at"] = pd.to_datetime(df["applied_at"]).dt.strftime("%Y-%m-%d")
        df["url"] = df["url"].apply(lambda u: f'<a href="{u}" target="_blank">Link</a>' if u else "")
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("No applications yet.")

# ── All Jobs ──────────────────────────────────────────────────────────────────
with tab_all:
    try:
        all_jobs = _query_all_jobs()
    except Exception as e:
        st.error(f"Could not load jobs: {e}")
        all_jobs = []

    if not all_jobs:
        st.info("No jobs in the database yet.")
    else:
        df_all = pd.DataFrame(all_jobs)

        col_search, col_status, col_score = st.columns([3, 2, 2])
        search_text = col_search.text_input("Search title / company", "")
        status_filter = col_status.multiselect(
            "Status",
            options=sorted(df_all["status"].unique()),
            default=[],
        )
        score_min, score_max = col_score.slider(
            "Score range", min_value=0, max_value=100, value=(0, 100)
        )

        mask = pd.Series([True] * len(df_all))
        if search_text:
            mask &= (
                df_all["title"].str.contains(search_text, case=False, na=False)
                | df_all["company"].str.contains(search_text, case=False, na=False)
            )
        if status_filter:
            mask &= df_all["status"].isin(status_filter)
        mask &= df_all["score"].fillna(0).between(score_min, score_max)

        display_cols = ["title", "company", "location", "score", "status",
                        "salary_text", "work_type", "source", "fetched_at"]
        st.dataframe(
            df_all[mask][display_cols].reset_index(drop=True),
            width="stretch",
            hide_index=True,
        )
        st.caption(f"Showing {mask.sum()} of {len(df_all)} jobs")

# ── Analytics ─────────────────────────────────────────────────────────────────
with tab_analytics:
    col_hist, col_line = st.columns(2)

    with col_hist:
        st.subheader("Score Distribution")
        try:
            scores = _query_score_distribution()
        except Exception as e:
            st.error(f"Could not load scores: {e}")
            scores = []
        if scores:
            fig_hist = px.histogram(
                x=scores, nbins=20,
                labels={"x": "Score", "y": "Count"},
                color_discrete_sequence=["#4f8bf9"],
            )
            fig_hist.add_vline(x=85, line_dash="dash", line_color="green",
                               annotation_text="Human review (85)")
            fig_hist.add_vline(x=75, line_dash="dash", line_color="orange",
                               annotation_text="Auto-apply (75)")
            st.plotly_chart(fig_hist, width="stretch")
        else:
            st.info("No scored jobs yet.")

    with col_line:
        st.subheader("Applications Over Time")
        try:
            apps_over_time = _query_applications_over_time()
        except Exception as e:
            st.error(f"Could not load application history: {e}")
            apps_over_time = []
        if apps_over_time:
            df_time = pd.DataFrame(apps_over_time)
            df_time["date"] = pd.to_datetime(df_time["date"])
            fig_line = px.line(df_time, x="date", y="count",
                               labels={"date": "Date", "count": "Applications"})
            st.plotly_chart(fig_line, width="stretch")
        else:
            st.info("No applications over time yet.")
