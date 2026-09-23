-- Phase 1 schema: titles catalog + pgvector similarity
-- Run this in the Supabase SQL editor.

create extension if not exists vector;

create table if not exists titles (
  id uuid primary key default gen_random_uuid(),
  tmdb_id integer,
  mal_id integer,
  type text not null check (type in ('movie', 'series', 'anime')),
  title text not null,
  overview text,
  genres text[] default '{}',
  keywords text[] default '{}',
  cast_names text[] default '{}',
  director text,
  year integer,
  runtime integer,
  vote_avg numeric,
  vote_count integer,
  tone_tags text[] default '{}',
  embedding vector(384),
  embedding_text text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (tmdb_id, type),
  unique (mal_id)
);

create index if not exists titles_embedding_idx
  on titles using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists titles_type_idx on titles(type);

-- Public read-only catalog: anon/authenticated clients can SELECT, but only
-- the service key (used by the sync/embed scripts) can insert or update,
-- since the service key bypasses RLS entirely.
alter table titles enable row level security;

drop policy if exists "Titles are publicly readable" on titles;
create policy "Titles are publicly readable"
  on titles for select
  using (true);

-- Returns the top N titles most similar to a given title's embedding.
create or replace function similar_to(target_id uuid, match_count int default 10)
returns table (
  id uuid,
  title text,
  type text,
  year integer,
  similarity float
)
language sql stable
as $$
  select t.id, t.title, t.type, t.year,
         1 - (t.embedding <=> (select embedding from titles where id = target_id)) as similarity
  from titles t
  where t.id != target_id
    and t.embedding is not null
    and (select embedding from titles where id = target_id) is not null
  order by t.embedding <=> (select embedding from titles where id = target_id)
  limit match_count;
$$;
