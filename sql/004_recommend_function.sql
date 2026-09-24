-- Generic nearest-neighbor retrieval used by the recommendation engine
-- (scripts/common/taste_engine.py). It returns a candidate pool that the
-- Python layer re-ranks with dislike penalty, quality floor, availability
-- and diversity -- keeping that tunable logic out of SQL.
create or replace function nearest_by_vector(
  query_vector vector(384),
  exclude_ids uuid[] default '{}',
  candidate_types text[] default null,
  match_count int default 200
)
returns table (
  id uuid,
  title text,
  type text,
  year integer,
  genres text[],
  vote_avg numeric,
  vote_count integer,
  embedding vector(384),
  similarity float
)
language sql stable
as $$
  select t.id, t.title, t.type, t.year, t.genres, t.vote_avg, t.vote_count, t.embedding,
         1 - (t.embedding <=> query_vector) as similarity
  from titles t
  where t.embedding is not null
    and not (t.id = any(exclude_ids))
    and (candidate_types is null or t.type = any(candidate_types))
  order by t.embedding <=> query_vector
  limit match_count;
$$;
