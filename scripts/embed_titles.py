"""Embed every title that doesn't have an embedding yet, using all-MiniLM-L6-v2.

Resumable by design: it always queries for rows where `embedding is null`,
so an interrupted run just picks up where it left off next time.

Usage:
    python scripts/embed_titles.py
    python scripts/embed_titles.py --batch-size 32
"""
import argparse
import os
import sys

from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.common.db import get_client
from scripts.common.text_builder import build_embedding_text

MODEL_NAME = "all-MiniLM-L6-v2"
SELECT_FIELDS = "id,title,type,overview,genres,keywords,cast_names,director,year,runtime,tone_tags"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    sb = get_client(use_service_key=True)

    total_count = sb.table("titles").select("id", count="exact").execute().count
    remaining_count = (
        sb.table("titles").select("id", count="exact").is_("embedding", "null").execute().count
    )
    already_embedded = total_count - remaining_count

    print(f"Loading {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)

    print(f"Starting: {already_embedded}/{total_count} already embedded, {remaining_count} remaining.")

    processed = 0
    while True:
        resp = (
            sb.table("titles")
            .select(SELECT_FIELDS)
            .is_("embedding", "null")
            .limit(args.batch_size)
            .execute()
        )
        rows = resp.data
        if not rows:
            break

        texts = [build_embedding_text(r) for r in rows]
        vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

        # postgrest upsert builds a real INSERT ... ON CONFLICT statement, so
        # NOT NULL columns without defaults (title, type) must be included
        # even though this is always hitting the UPDATE branch on id.
        update_rows = [
            {
                "id": row["id"],
                "title": row["title"],
                "type": row["type"],
                "embedding": vec.tolist(),
                "embedding_text": text,
            }
            for row, vec, text in zip(rows, vectors, texts)
        ]
        sb.table("titles").upsert(update_rows).execute()

        processed += len(rows)
        print(f"{already_embedded + processed}/{total_count} embedded")

    print(f"\nDone. {already_embedded + processed}/{total_count} titles embedded.")


if __name__ == "__main__":
    main()
