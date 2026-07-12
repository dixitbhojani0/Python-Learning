"""
admin/pages/04_evaluation.py

Evaluation Dashboard — view retrieval precision, faithfulness, and relevancy scores.

What this page shows:
  - Aggregate metrics across all evaluation runs (latest run + all-time averages)
  - Per-query breakdown table (sortable, filterable by category)
  - Score distribution charts (precision / faithfulness / relevancy)
  - Lowest-scoring queries to investigate
  - Run the evaluator directly from the browser (with mode selection)
"""
import subprocess
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st

st.set_page_config(page_title="Evaluation", page_icon="📊", layout="wide")
st.title("📊 Evaluation Dashboard")
st.caption("Retrieval precision · Faithfulness · Answer relevancy — measured against eval_set.json")


# ── Load results ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=5)
def _load_results() -> list[dict]:
    sys.path.insert(0, str(_project_root))
    from backend.core.metrics import load_eval_results
    return load_eval_results()


@st.cache_data(ttl=60)
def _load_eval_set() -> list[dict]:
    import json
    path = _project_root / "data" / "eval_set.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


results   = _load_results()
eval_set  = _load_eval_set()

# ── Run Evaluation panel ──────────────────────────────────────────────────────

with st.expander("▶ Run Evaluation", expanded=not results):
    mode = st.radio(
        "Mode",
        ["Full pipeline (RAG + LLM)", "Retrieval-only (fast, no LLM)"],
        horizontal=True,
    )
    query_filter = st.selectbox(
        "Run single query (optional)",
        ["All queries"] + [item["id"] for item in eval_set],
    )

    if st.button("Run Evaluation", type="primary"):
        cmd = [sys.executable, str(_project_root / "scripts" / "evaluate.py")]
        if "Retrieval-only" in mode:
            cmd.append("--retrieval-only")
        if query_filter != "All queries":
            cmd += ["--query", query_filter]

        with st.spinner("Running evaluation..."):
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(_project_root),
            )

        if proc.returncode == 0:
            st.success("Evaluation complete!")
            st.code(proc.stdout, language="text")
        else:
            st.error("Evaluation failed.")
            st.code(proc.stderr or proc.stdout, language="text")

        st.cache_data.clear()
        st.rerun()

# ── No data yet ───────────────────────────────────────────────────────────────

if not results:
    st.info("No evaluation results yet. Run the evaluation above to get started.")
    st.stop()

# ── Pull out the latest run for the headline metrics ─────────────────────────

import pandas as pd

df = pd.DataFrame(results)
df["timestamp"] = pd.to_datetime(df["timestamp"])

# Latest run = the most recent batch (group by run_id → pick the latest)
latest_run_id = df.sort_values("timestamp").iloc[-1]["run_id"]
latest_df     = df[df["run_id"] == latest_run_id]

# ── Headline metric cards ─────────────────────────────────────────────────────

st.subheader("Latest Run")

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    val = latest_df["precision"].mean()
    delta_color = "normal" if val >= 0.70 else "inverse"
    st.metric("Retrieval Precision", f"{val:.2f}", delta="target ≥ 0.70", delta_color=delta_color)

with col2:
    val = latest_df["faithfulness"].mean()
    delta_color = "normal" if val >= 0.80 else "inverse"
    st.metric("Faithfulness", f"{val:.2f}", delta="target ≥ 0.80", delta_color=delta_color)

with col3:
    val = latest_df["relevancy"].mean()
    delta_color = "normal" if val >= 0.60 else "inverse"
    st.metric("Answer Relevancy", f"{val:.2f}", delta="target ≥ 0.60", delta_color=delta_color)

with col4:
    val = latest_df["composite"].mean()
    delta_color = "normal" if val >= 0.70 else "inverse"
    st.metric("Composite Score", f"{val:.2f}", delta="target ≥ 0.70", delta_color=delta_color)

with col5:
    intent_ok_pct = latest_df["intent_correct"].mean() * 100
    st.metric("Intent Accuracy", f"{intent_ok_pct:.0f}%", delta="target 100%",
              delta_color="normal" if intent_ok_pct >= 80 else "inverse")

st.divider()

# ── Per-query breakdown table ─────────────────────────────────────────────────

st.subheader("Per-Query Scores — Latest Run")

_display_cols = ["eval_id", "category", "precision", "faithfulness", "relevancy",
                 "composite", "intent_correct", "chunks_retrieved", "intent_detected"]
_available = [c for c in _display_cols if c in latest_df.columns]
table_df   = latest_df[_available].copy()

# Colour coding: highlight low composite scores
def _highlight_composite(val):
    if isinstance(val, float):
        if val >= 0.70:
            return "background-color: #d4edda; color: #155724"
        elif val >= 0.50:
            return "background-color: #fff3cd; color: #856404"
        else:
            return "background-color: #f8d7da; color: #721c24"
    return ""

st.dataframe(
    table_df.style.applymap(_highlight_composite, subset=["composite"]).format(
        {c: "{:.2f}" for c in ["precision", "faithfulness", "relevancy", "composite"]}
    ),
    use_container_width=True,
    hide_index=True,
)

# ── Worst-performing queries ──────────────────────────────────────────────────

st.subheader("Lowest Composite Scores (investigate these)")

worst = latest_df.nsmallest(3, "composite")
for _, row in worst.iterrows():
    with st.expander(f"{row['eval_id']}  composite={row['composite']:.2f}  [{row.get('category','')}]"):
        st.markdown(f"**Query:** {row.get('query', '')}")
        st.markdown(f"**Response snippet:** {row.get('response_snippet', '')}")
        cols = st.columns(4)
        cols[0].metric("Precision",    f"{row['precision']:.2f}")
        cols[1].metric("Faithfulness", f"{row['faithfulness']:.2f}")
        cols[2].metric("Relevancy",    f"{row['relevancy']:.2f}")
        cols[3].metric("Chunks",       str(int(row.get('chunks_retrieved', 0))))

st.divider()

# ── Historical trend chart ────────────────────────────────────────────────────

if len(df["run_id"].unique()) > 1:
    st.subheader("Score Trends Across Runs")

    # Aggregate by timestamp (one point per run)
    trend = (
        df.groupby("timestamp")[["precision", "faithfulness", "relevancy", "composite"]]
        .mean()
        .reset_index()
        .sort_values("timestamp")
    )

    st.line_chart(trend.set_index("timestamp")[["precision", "faithfulness", "relevancy", "composite"]])
else:
    st.caption("Run the evaluation multiple times to see score trends here.")

# ── Score distribution ────────────────────────────────────────────────────────

st.subheader("Score Distribution — Latest Run")

dist_col1, dist_col2, dist_col3 = st.columns(3)

with dist_col1:
    st.markdown("**Retrieval Precision**")
    st.bar_chart(latest_df["precision"].value_counts(bins=5, sort=False))

with dist_col2:
    st.markdown("**Faithfulness**")
    st.bar_chart(latest_df["faithfulness"].value_counts(bins=5, sort=False))

with dist_col3:
    st.markdown("**Answer Relevancy**")
    st.bar_chart(latest_df["relevancy"].value_counts(bins=5, sort=False))

# ── All-time aggregate ────────────────────────────────────────────────────────

with st.expander("All-time aggregate (all runs)"):
    all_time = df[["precision", "faithfulness", "relevancy", "composite"]].mean()
    st.dataframe(all_time.to_frame("average").T.style.format("{:.3f}"), use_container_width=True)
    st.caption(f"Based on {len(df)} total evaluation records across {len(df['run_id'].unique())} run(s).")
