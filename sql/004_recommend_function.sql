-- Generic nearest-neighbor retrieval used by the recommendation engine
-- (scripts/common/taste_engine.py). It returns a candidate pool that the
-- Python layer re-ranks with dislike penalty, quality floor, availability
-- and diversity -- keeping that tunable logic out of SQL.
-- The return columns changed (added poster_url), which needs a drop, not
-- just CREATE OR REPLACE -- Postgres won't replace a function's return
-- table shape in place.
drop function if exists nearest_by_vector(vector, uuid[], text[], int);

create function nearest_by_vector(
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
  poster_url text,
  similarity float
)
language sql stable
as $$
  select t.id, t.title, t.type, t.year, t.genres, t.vote_avg, t.vote_count, t.embedding, t.poster_url,
         1 - (t.embedding <=> query_vector) as similarity
  from titles t
  where t.embedding is not null
    and not (t.id = any(exclude_ids))
    and (candidate_types is null or t.type = any(candidate_types))
  order by t.embedding <=> query_vector
  limit match_count;
$$;
