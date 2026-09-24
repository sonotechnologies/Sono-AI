-- Phase 2 schema: users, interactions, taste vectors, feeds support.
-- Run this in the Supabase SQL editor after enabling Auth (Email provider,
-- which is on by default for new projects).

-- Profile row per auth user, created automatically on signup.
create table if not exists users (
  id uuid primary key references auth.users(id) on delete cascade,
  country text,
  services text[] default '{}',
  onboarding_done boolean default false,
  created_at timestamptz default now()
);

alter table users enable row level security;

drop policy if exists "Users can view own profile" on users;
create policy "Users can view own profile"
  on users for select
  using (auth.uid() = id);

drop policy if exists "Users can update own profile" on users;
create policy "Users can update own profile"
  on users for update
  using (auth.uid() = id);

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.users (id)
  values (new.id)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Every rating / watch / watchlist add / swipe / dismiss a user makes on a title.
create table if not exists interactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  title_id uuid not null references titles(id) on delete cascade,
  kind text not null check (kind in ('rating', 'watched', 'watchlist', 'swipe', 'dismiss')),
  value numeric,
  source text not null default 'manual' check (source in ('manual', 'import')),
  visibility text not null default 'private' check (visibility in ('private', 'shared')),
  created_at timestamptz default now()
);

create index if not exists interactions_user_idx on interactions(user_id);
create index if not exists interactions_title_idx on interactions(title_id);

alter table interactions enable row level security;

drop policy if exists "Users manage own interactions" on interactions;
create policy "Users manage own interactions"
  on interactions for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Every title shown in a feed, and what the user did with it.
create table if not exists impressions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  title_id uuid not null references titles(id) on delete cascade,
  feed text not null check (feed in ('tonight', 'discover', 'similar', 'radar', 'assistant')),
  position int,
  action text not null default 'none' check (action in ('none', 'open', 'save', 'watched', 'dismiss')),
  created_at timestamptz default now()
);

create index if not exists impressions_user_idx on impressions(user_id);

alter table impressions enable row level security;

drop policy if exists "Users manage own impressions" on impressions;
create policy "Users manage own impressions"
  on impressions for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Streaming availability per title/country/service. Populated by
-- scripts/sync_availability.py using TMDB's watch-provider data (JustWatch).
create table if not exists availability (
  id uuid primary key default gen_random_uuid(),
  title_id uuid not null references titles(id) on delete cascade,
  country text not null,
  service text not null,
  kind text not null check (kind in ('stream', 'rent', 'buy')),
  updated_at timestamptz default now(),
  unique (title_id, country, service, kind)
);

create index if not exists availability_title_idx on availability(title_id);
create index if not exists availability_country_idx on availability(country);

alter table availability enable row level security;

drop policy if exists "Availability is publicly readable" on availability;
create policy "Availability is publicly readable"
  on availability for select
  using (true);

-- Weekly trending lists, labeled by source (tmdb / mal / netflix / app).
create table if not exists trending (
  id uuid primary key default gen_random_uuid(),
  list text not null check (list in ('tmdb', 'mal', 'netflix', 'app')),
  type text not null check (type in ('movie', 'series', 'anime')),
  rank int not null,
  title_id uuid not null references titles(id) on delete cascade,
  week date not null,
  unique (list, type, title_id, week)
);

alter table trending enable row level security;

drop policy if exists "Trending is publicly readable" on trending;
create policy "Trending is publicly readable"
  on trending for select
  using (true);

-- One like vector + one dislike vector per user (MVP; see BRIEF.md's
-- multi-cluster taste_profiles for the fuller design once there's usage
-- data to justify the added complexity).
create table if not exists user_taste_vectors (
  user_id uuid primary key references users(id) on delete cascade,
  like_vector vector(384),
  dislike_vector vector(384),
  interaction_count int not null default 0,
  updated_at timestamptz default now()
);

alter table user_taste_vectors enable row level security;

drop policy if exists "Users manage own taste vector" on user_taste_vectors;
create policy "Users manage own taste vector"
  on user_taste_vectors for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Which of the ~15 k-means taste clusters a title belongs to, for the
-- onboarding swipe round. Populated by scripts/cluster_titles.py.
alter table titles add column if not exists taste_cluster int;
