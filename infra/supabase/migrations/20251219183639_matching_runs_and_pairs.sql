-- ---------------------------------------------------------
-- Matching runs (store UI parameters + run history)
-- ---------------------------------------------------------
create table if not exists matching_runs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  created_by uuid, -- auth.users.id (optional)
  status text not null default 'CREATED', -- CREATED/RUNNING/DONE/FAILED
  params jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_matching_runs_job on matching_runs(job_id);

-- ---------------------------------------------------------
-- Pairs (store each pair once; enforce 1-to-1 usage)
-- ---------------------------------------------------------
create table if not exists diamond_pairs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references matching_runs(id) on delete cascade,
  job_id uuid not null references jobs(id) on delete cascade,

  diamond1_id uuid not null references diamonds(id) on delete cascade,
  diamond2_id uuid not null references diamonds(id) on delete cascade,

  -- canonical ordering (generated)
  diamond_min_id uuid generated always as (least(diamond1_id, diamond2_id)) stored,
  diamond_max_id uuid generated always as (greatest(diamond1_id, diamond2_id)) stored,

  confidence numeric not null,
  locked boolean not null default false,
  source text not null default 'ALGO',   -- ALGO / PREMIUM / MANUAL
  created_at timestamptz not null default now(),

  constraint chk_no_self_pair check (diamond1_id <> diamond2_id),

  -- ensure stable uniqueness regardless of order
  constraint uq_pair_unique unique (run_id, diamond_min_id, diamond_max_id)
);

-- enforce "each diamond used at most once per run"
create unique index if not exists uq_diamond1_once_per_run on diamond_pairs(run_id, diamond1_id);
create unique index if not exists uq_diamond2_once_per_run on diamond_pairs(run_id, diamond2_id);

create index if not exists idx_pairs_job on diamond_pairs(job_id);
create index if not exists idx_pairs_run on diamond_pairs(run_id);


