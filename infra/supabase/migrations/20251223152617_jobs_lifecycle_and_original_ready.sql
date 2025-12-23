-- =========================================================
-- Jobs lifecycle fields
-- =========================================================
alter table public.jobs
  add column if not exists started_at timestamptz,
  add column if not exists ended_at timestamptz,
  add column if not exists paused_at timestamptz;

-- Helpful indexes
create index if not exists idx_jobs_org_created_at on public.jobs (org_id, created_at desc);
create index if not exists idx_jobs_status on public.jobs (status);

-- =========================================================
-- Diamond images: track originals readiness separately
-- =========================================================
alter table public.diamond_images
  add column if not exists original_ready boolean not null default false,
  add column if not exists original_uploaded_at timestamptz;

create index if not exists idx_diamond_images_original_ready
  on public.diamond_images (original_ready);

-- =========================================================
-- Optional: simple integrity helper
-- If original_ready=true then original_uploaded_at should be set (best effort)
-- =========================================================
create or replace function public._enforce_original_uploaded_at()
returns trigger as $$
begin
  if new.original_ready is true and new.original_uploaded_at is null then
    new.original_uploaded_at := now();
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_enforce_original_uploaded_at on public.diamond_images;
create trigger trg_enforce_original_uploaded_at
before insert or update on public.diamond_images
for each row
execute function public._enforce_original_uploaded_at();

