"""Phase 1 demo page: search a title, see 'more like this'.

Usage:
    streamlit run web/search.py
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.common.db import get_client

st.set_page_config(page_title="Sono AI — Similar Titles", page_icon="🎬")

sb = get_client(use_service_key=False)

st.title("Search a title")
query = st.text_input("Title", placeholder="e.g. Breaking Bad")

if query:
    results = (
        sb.table("titles")
        .select("id,title,type,year,vote_avg")
        .ilike("title", f"%{query}%")
        .order("vote_count", desc=True)
        .limit(10)
        .execute()
        .data
    )

    if not results:
        st.info("No titles found.")
    else:
        labels = [f"{r['title']} ({r['year']}) — {r['type']}" for r in results]
        idx = st.selectbox("Pick a title", range(len(results)), format_func=lambda i: labels[i])
        selected = results[idx]

        st.subheader(f"More like {selected['title']}")
        similar = (
            sb.rpc("similar_to", {"target_id": selected["id"], "match_count": 10})
            .execute()
            .data
        )

        if not similar:
            st.warning("This title doesn't have an embedding yet. Run scripts/embed_titles.py first.")
        else:
            for s in similar:
                st.markdown(
                    f"**{s['title']}** ({s['year']}) — *{s['type']}* "
                    f"— similarity {s['similarity']:.3f}"
                )
