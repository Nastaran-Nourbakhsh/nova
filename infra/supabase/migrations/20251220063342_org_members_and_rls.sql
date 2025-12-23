-- ---------------------------------------------------------
-- Org members (user ↔ org)
-- ---------------------------------------------------------
create table if not exists org_members (
  org_id uuid not null references orgs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'member', -- 'admin'/'member'
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);

create index if not exists idx_org_members_user on org_members(user_id);

-- Helper function for policies
create or replace function is_org_member(p_org_id uuid)
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from org_members m
    where m.org_id = p_org_id
      and m.user_id = auth.uid()
  );
$$;

-- ---------------------------------------------------------
-- Enable RLS
-- ---------------------------------------------------------
alter table orgs enable row level security;
alter table devices enable row level security;
alter table jobs enable row level security;
alter table rings enable row level security;
alter table diamonds enable row level security;
alter table diamond_images enable row level security;
alter table diamond_image_features enable row level security;
alter table diamond_features enable row level security;
alter table matching_runs enable row level security;
alter table diamond_pairs enable row level security;
alter table org_members enable row level security;

-- ---------------------------------------------------------
-- Policies (SELECT only for now; we’ll add INSERT/UPDATE later)
-- ---------------------------------------------------------

-- ORGS
drop policy if exists orgs_select on orgs;
create policy orgs_select on orgs
for select using (is_org_member(id));

-- ORG MEMBERS
drop policy if exists org_members_select on org_members;
create policy org_members_select on org_members
for select using (user_id = auth.uid());

-- DEVICES
drop policy if exists devices_select on devices;
create policy devices_select on devices
for select using (is_org_member(org_id));

-- JOBS
drop policy if exists jobs_select on jobs;
create policy jobs_select on jobs
for select using (is_org_member(org_id));

-- RINGS (via job → org)
drop policy if exists rings_select on rings;
create policy rings_select on rings
for select using (
  exists (
    select 1
    from jobs j
    join org_members m on m.org_id = j.org_id
    where j.id = rings.job_id and m.user_id = auth.uid()
  )
);

-- DIAMONDS (via job → org)
drop policy if exists diamonds_select on diamonds;
create policy diamonds_select on diamonds
for select using (
  exists (
    select 1
    from jobs j
    join org_members m on m.org_id = j.org_id
    where j.id = diamonds.job_id and m.user_id = auth.uid()
  )
);

-- IMAGES (via diamond → job → org)
drop policy if exists diamond_images_select on diamond_images;
create policy diamond_images_select on diamond_images
for select using (
  exists (
    select 1
    from diamonds d
    join jobs j on j.id = d.job_id
    join org_members m on m.org_id = j.org_id
    where d.id = diamond_images.diamond_id and m.user_id = auth.uid()
  )
);

-- IMAGE FEATURES (via diamond_images → diamond → job → org)
drop policy if exists diamond_image_features_select on diamond_image_features;
create policy diamond_image_features_select on diamond_image_features
for select using (
  exists (
    select 1
    from diamond_images di
    join diamonds d on d.id = di.diamond_id
    join jobs j on j.id = d.job_id
    join org_members m on m.org_id = j.org_id
    where di.id = diamond_image_features.diamond_image_id and m.user_id = auth.uid()
  )
);

-- DIAMOND FEATURES (via diamond → job → org)
drop policy if exists diamond_features_select on diamond_features;
create policy diamond_features_select on diamond_features
for select using (
  exists (
    select 1
    from diamonds d
    join jobs j on j.id = d.job_id
    join org_members m on m.org_id = j.org_id
    where d.id = diamond_features.diamond_id and m.user_id = auth.uid()
  )
);

-- MATCHING RUNS (via job → org)
drop policy if exists matching_runs_select on matching_runs;
create policy matching_runs_select on matching_runs
for select using (
  exists (
    select 1
    from jobs j
    join org_members m on m.org_id = j.org_id
    where j.id = matching_runs.job_id and m.user_id = auth.uid()
  )
);

-- PAIRS (via job → org)
drop policy if exists diamond_pairs_select on diamond_pairs;
create policy diamond_pairs_select on diamond_pairs
for select using (
  exists (
    select 1
    from jobs j
    join org_members m on m.org_id = j.org_id
    where j.id = diamond_pairs.job_id and m.user_id = auth.uid()
  )
);

