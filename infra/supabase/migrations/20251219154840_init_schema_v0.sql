-- =========================================================
-- init_schema.sql (v0) — Nova / GemAI
-- Supports:
--  - 2 images per diamond (UV_FREE + ASET)
--  - Segmentation stored per image (linked to diamond_images)
--  - Features can be produced on-device (Option 1) or in cloud (Option 2)
-- =========================================================

-- Extensions
create extension if not exists "pgcrypto";

-- ---------------------------------------------------------
-- ENUMs
-- ---------------------------------------------------------
do $$ begin
  create type image_type as enum ('UV_FREE', 'ASET');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type job_status as enum ('CREATED', 'SCANNING', 'PROCESSING', 'DONE', 'FAILED');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type roi_shape_type as enum ('RECT', 'CIRCLE');
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------
-- CORE: Orgs, Devices, Jobs, Rings, Diamonds
-- ---------------------------------------------------------
create table if not exists orgs (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists devices (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  name text not null,
  device_key_hash text, -- later: for device auth
  created_at timestamptz not null default now()
);

create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  device_id uuid references devices(id) on delete set null,
  external_ref text, -- optional: customer job id
  status job_status not null default 'CREATED',
  created_at timestamptz not null default now()
);

create table if not exists rings (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  ring_label text not null, -- e.g. "A", "B", "C" or QR content
  position_count int,       -- optional: number of slots/pockets on ring
  created_at timestamptz not null default now(),
  unique(job_id, ring_label)
);

create table if not exists diamonds (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  ring_id uuid references rings(id) on delete set null,
  slot_index int not null, -- position on the ring
  captured_at timestamptz not null default now(),
  notes text,
  unique(job_id, ring_id, slot_index)
);

-- ---------------------------------------------------------
-- IMAGES: two rows per diamond (UV_FREE + ASET)
-- ---------------------------------------------------------
create table if not exists diamond_images (
  id uuid primary key default gen_random_uuid(),
  diamond_id uuid not null references diamonds(id) on delete cascade,
  image_type image_type not null,
  storage_path text not null, -- Supabase Storage path (or URL)
  width int,
  height int,
  created_at timestamptz not null default now(),
  unique(diamond_id, image_type)
);

-- ---------------------------------------------------------
-- SEGMENTATION / ROI: stored per image (recommended)
-- This holds the rectangle/circle ROI and derived area per image.
-- ---------------------------------------------------------
create table if not exists diamond_image_features (
  id uuid primary key default gen_random_uuid(),
  diamond_image_id uuid not null references diamond_images(id) on delete cascade,

  -- who/what produced it
  source text not null default 'UNKNOWN', -- 'DEVICE' or 'CLOUD'
  model_version text not null default 'seg_v1',

  -- ROI geometry
  roi_shape roi_shape_type not null,

  -- rectangle fields (used when roi_shape='RECT')
  rect_x int,
  rect_y int,
  rect_w int,
  rect_h int,

  -- circle fields (used when roi_shape='CIRCLE')
  circle_cx int,
  circle_cy int,
  circle_r int,

  -- derived measurement(s)
  area_px bigint, -- can be computed from mask/ROI
  created_at timestamptz not null default now(),

  -- allow multiple versions per image (e.g., seg_v1, seg_v2)
  unique(diamond_image_id, model_version)
);

-- ---------------------------------------------------------
-- DIAMOND-LEVEL FEATURES: embeddings + metrics
-- (Option 1: device writes these; Option 2: cloud worker writes these)
-- ---------------------------------------------------------
create table if not exists diamond_features (
  id uuid primary key default gen_random_uuid(),
  diamond_id uuid not null references diamonds(id) on delete cascade,

  source text not null default 'UNKNOWN', -- 'DEVICE' or 'CLOUD'
  model_version text not null default 'emb_v1',

  -- simple metrics (optional now, useful later)
  size_mm numeric,
  color_score numeric,
  clarity_score numeric,

  -- embedding vector (simple v0 storage)
  embedding real[],

  created_at timestamptz not null default now(),
  unique(diamond_id, model_version)
);

-- ---------------------------------------------------------
-- MATCHING OUTPUT: diamond ↔ diamond within a job
-- ---------------------------------------------------------
create table if not exists diamond_matches (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  diamond_id uuid not null references diamonds(id) on delete cascade,
  matched_diamond_id uuid not null references diamonds(id) on delete cascade,
  confidence numeric not null,
  created_at timestamptz not null default now(),
  unique(job_id, diamond_id)
);

-- ---------------------------------------------------------
-- INDEXES
-- ---------------------------------------------------------
create index if not exists idx_devices_org on devices(org_id);

create index if not exists idx_jobs_org on jobs(org_id);
create index if not exists idx_jobs_device on jobs(device_id);

create index if not exists idx_rings_job on rings(job_id);

create index if not exists idx_diamonds_job on diamonds(job_id);
create index if not exists idx_diamonds_ring on diamonds(ring_id);

create index if not exists idx_images_diamond on diamond_images(diamond_id);
create index if not exists idx_images_type on diamond_images(image_type);

create index if not exists idx_imgfeat_image on diamond_image_features(diamond_image_id);
create index if not exists idx_feat_diamond on diamond_features(diamond_id);

create index if not exists idx_matches_job on diamond_matches(job_id);
create index if not exists idx_matches_diamond on diamond_matches(diamond_id);

