"""Streamlit dashboard — metrics, apply queue, applied funnel, all jobs, analytics."""

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
        interviews = session.scalar(
            select(func.count(Job.id)).where(Job.status == "interview")
        ) or 0
    return dict(scanned=scanned, high_match=high_match, applied=applied, interviews=interviews)


def _query_apply_queue() -> list[dict]:
    """All jobs ready for manual apply, sorted by score descending."""
    with get_session() as session:
        rows = session.scalars(
            select(Job)
            .where(Job.status.in_(["queued_apply", "human_review"]))
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

def _skip_job(job_id: str) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "skipped"


def _mark_applied(job_id: str) -> None:
    """Set job status to applied, record applied_at, and create an Application row."""
    from datetime import timezone as _tz
    with get_session() as session:
        job = session.get(Job, job_id)
        if job and job.status != "applied":
            job.status = "applied"
            job.applied_at = datetime.now(_tz.utc)
            job.apply_method = "manual"
            existing = session.scalars(
                select(Application).where(Application.job_id == job_id)
            ).first()
            if not existing:
                session.add(Application(job_id=job_id, method="manual"))


# ---------------------------------------------------------------------------
# Metrics row
# ---------------------------------------------------------------------------

try:
    metrics = _query_metrics()
    _db_ok = True
except Exception as _db_err:
    _db_ok = False
    st.error(f"Database unavailable — {_db_err.__class__.__name__}: {_db_err}")
    metrics = dict(scanned=0, high_match=0, applied=0, interviews=0)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Scanned", metrics["scanned"])
c2.metric("High Match (≥85)", metrics["high_match"])
c3.metric("Applied", metrics["applied"])
c4.metric("Interviews", metrics["interviews"])

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_queue, tab_applied, tab_all, tab_analytics = st.tabs(
    ["Apply Queue", "Applied", "All Jobs", "Analytics"]
)

# ── Apply Queue ───────────────────────────────────────────────────────────────
with tab_queue:
    st.subheader("Apply Queue")
    st.caption("Jobs scored ≥75 — open and apply manually, or skip to dismiss permanently.")

    try:
        apply_jobs = _query_apply_queue()
    except Exception as e:
        st.error(f"Could not load apply queue: {e}")
        apply_jobs = []

    if not apply_jobs:
        st.info("No jobs in the apply queue.")
    else:
        for job in apply_jobs:
            posted = ""
            if job["posted_at"]:
                posted = f"  |  Posted: {job['posted_at'].strftime('%b %d')}"
            label = f"**{job['title']}** @ {job['company']}  |  Score: {job['score']}{posted}"
            with st.expander(label):
                col_l, col_r = st.columns([3, 1])
                with col_l:
                    st.write(f"**Location:** {job['location']}  |  **Work type:** {job['work_type']}")
                    st.write(f"**Salary:** {job['salary_text'] or 'Not listed'}")
                    if job["score_reasoning"]:
                        st.caption(job["score_reasoning"])
                with col_r:
                    if job["url"]:
                        st.link_button("Open & Apply", job["url"], type="primary")
                    else:
                        st.write("No URL")
                    if st.button("Mark Applied", key=f"applied_{job['id']}"):
                        _mark_applied(job["id"])
                        st.success("Marked as applied.")
                        st.rerun()
                    if st.button("Skip", key=f"skip_{job['id']}"):
                        _skip_job(job["id"])
                        st.warning("Skipped.")
                        st.rerun()

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
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No applications yet.")

    st.caption(f"Rejected: {rejected_count}")

    st.subheader("Application Log")
    if applied_jobs:
        df = pd.DataFrame(applied_jobs)[
            ["title", "company", "location", "score", "status", "applied_at", "url"]
        ]
        df["applied_at"] = pd.to_datetime(df["applied_at"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")
        st.dataframe(
            df,
            column_config={"url": st.column_config.LinkColumn("Link", display_text="Open")},
            use_container_width=True,
            hide_index=True,
        )
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
                        "salary_text", "work_type", "source", "fetched_at", "url"]
        st.dataframe(
            df_all[mask][display_cols].reset_index(drop=True),
            column_config={"url": st.column_config.LinkColumn("Link", display_text="Open")},
            use_container_width=True,
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
                               annotation_text="High match (85)")
            fig_hist.add_vline(x=75, line_dash="dash", line_color="orange",
                               annotation_text="Apply queue (75)")
            st.plotly_chart(fig_hist, use_container_width=True)
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
            st.plotly_chart(fig_line, use_container_width=True)
        else:
            st.info("No applications over time yet.")
