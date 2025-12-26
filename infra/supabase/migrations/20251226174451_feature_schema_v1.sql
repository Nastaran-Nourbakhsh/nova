-- ---------------------------------------------
-- feature_schema_v1
-- 1) Drop unused table diamond_image_features
-- 2) Replace diamond_features columns with the ones we actually need
-- ---------------------------------------------

-- 1) Drop table we won’t use
drop table if exists public.diamond_image_features cascade;

-- (optional) Drop enum type that was only used by diamond_image_features
-- If you get "cannot drop because other objects depend on it", comment these out.
drop type if exists public.roi_shape_type cascade;

-- 2) Reshape diamond_features to match new plan
-- We keep: id, diamond_id, model_version, created_at
-- We remove: source, size_mm, color_score, clarity_score, embedding
alter table public.diamond_features
  drop column if exists source,
  drop column if exists size_mm,
  drop column if exists color_score,
  drop column if exists clarity_score,
  drop column if exists embedding;

alter table public.diamond_features
  add column if not exists aset_embedding real[] null,
  add column if not exists uv_free_embedding real[] null,
  add column if not exists diamond_type text null, -- 'CIRCULAR' | 'PRINCESS' (we enforce later)
  add column if not exists boundary jsonb null,    -- {kind:'circle',cx,cy,r} or {kind:'rect',x,y,w,h}
  add column if not exists area_px bigint null,
  add column if not exists table_size_px bigint null,
  add column if not exists face_up_color jsonb null; -- {r,g,b} or {mean:[r,g,b]}

-- Optional: ensure one feature row per diamond+model_version (recommended)
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'uq_diamond_features_diamond_model'
  ) then
    alter table public.diamond_features
      add constraint uq_diamond_features_diamond_model unique (diamond_id, model_version);
  end if;
end $$;

-- Optional: indexes for faster matching reads
create index if not exists idx_diamond_features_diamond_id on public.diamond_features(diamond_id);
create index if not exists idx_diamond_features_model_version on public.diamond_features(model_version);

