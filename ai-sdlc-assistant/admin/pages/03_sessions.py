"""
admin/pages/03_sessions.py

Session Log — browse recent conversation turns from SQLite.

What this page does:
  - Reads the SQLite session database (data/sessions.db)
  - Shows recent conversation turns in a browseable table
  - Lets you filter by user, role, or search the query text
  - Shows the full query + response for any selected turn

Why this is useful:
  Debug what users actually asked, what the system responded, and whether
  the session memory is accumulating correctly across turns.
"""
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st

st.set_page_config(page_title="Session Log", page_icon="💬", layout="wide")
st.title("💬 Session Log")
st.caption("Recent conversation turns from SQLite session store.")

_DB_PATH = _project_root / "data" / "sessions.db"


@st.cache_data(ttl=10)
def _load_turns(limit: int = 100) -> list[dict]:
    """Load the most recent N turns from sessions.db."""
    if not _DB_PATH.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT session_id, user_id, user_role, query, response, created_at
            FROM conversation_turns
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        st.error(f"Failed to read sessions.db: {exc}")
        return []


turns = _load_turns()

if not turns:
    st.info(f"No turns found. Database path: `{_DB_PATH}`\n\nSend a message through the Chainlit UI first.")
    st.stop()

import pandas as pd

df = pd.DataFrame(turns)

# ── Summary metrics ───────────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Turns", len(df))
col2.metric("Unique Sessions", df["session_id"].nunique())
col3.metric("Unique Users", df["user_id"].nunique())
col4.metric("Roles Seen", df["user_role"].nunique())

st.markdown("---")

# ── Filters ────────────────────────────────────────────────────────────────────

col_a, col_b, col_c = st.columns(3)

filter_user = col_a.selectbox(
    "Filter by user",
    options=["(all)"] + sorted(df["user_id"].unique().tolist()),
)
filter_role = col_b.selectbox(
    "Filter by role",
    options=["(all)"] + sorted(df["user_role"].unique().tolist()),
)
search_query = col_c.text_input("Search query text", placeholder="e.g. dashboard")

filtered = df.copy()
if filter_user != "(all)":
    filtered = filtered[filtered["user_id"] == filter_user]
if filter_role != "(all)":
    filtered = filtered[filtered["user_role"] == filter_role]
if search_query:
    filtered = filtered[
        filtered["query"].str.contains(search_query, case=False, na=False)
    ]

st.caption(f"Showing {len(filtered)} of {len(df)} turns")

# ── Turn table ─────────────────────────────────────────────────────────────────

st.subheader("Turns")

display_cols = ["created_at", "user_id", "user_role", "session_id", "query"]
# Truncate query column for table display
filtered_display = filtered[display_cols].copy()
filtered_display["query"] = filtered_display["query"].str[:80] + "…"

selected_indices = st.dataframe(
    filtered_display,
    use_container_width=True,
    height=300,
    on_select="rerun",
    selection_mode="single-row",
)

# ── Detail view for selected row ─────────────────────────────────────────────

if selected_indices and selected_indices.get("selection", {}).get("rows"):
    row_idx    = selected_indices["selection"]["rows"][0]
    actual_idx = filtered.index[row_idx]
    row        = filtered.loc[actual_idx]

    st.markdown("---")
    st.subheader("Turn Detail")

    meta_col, _ = st.columns([1, 2])
    with meta_col:
        st.markdown(f"**User:** `{row['user_id']}`  ·  **Role:** `{row['user_role']}`")
        st.markdown(f"**Session:** `{row['session_id']}`")
        st.markdown(f"**Time:** `{row['created_at']}`")

    st.markdown("**Query:**")
    st.info(row["query"])

    st.markdown("**Response:**")
    st.success(row["response"])
