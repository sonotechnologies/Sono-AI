def build_embedding_text(row: dict) -> str:
    """Build the text description that gets embedded for a title row.

    `row` is expected to have the same shape as a `titles` table row
    (genres/keywords/cast_names as lists, everything else scalar).
    """
    parts = [row.get("title") or ""]

    type_ = row.get("type")
    if type_:
        parts.append(type_)

    year = row.get("year")
    if year:
        decade = f"{(year // 10) * 10}s"
        parts.append(f"{year} ({decade})")

    genres = row.get("genres") or []
    if genres:
        parts.append("Genres: " + ", ".join(genres))

    overview = row.get("overview")
    if overview:
        parts.append(overview)

    keywords = row.get("keywords") or []
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords))

    cast_names = row.get("cast_names") or []
    if cast_names:
        parts.append("Cast: " + ", ".join(cast_names))

    director = row.get("director")
    if director:
        parts.append(f"Director: {director}")

    runtime = row.get("runtime")
    if runtime:
        parts.append(f"Runtime: {runtime} minutes")

    tone_tags = row.get("tone_tags") or []
    if tone_tags:
        parts.append("Tone: " + ", ".join(tone_tags))

    return "\n".join(p for p in parts if p)
