alter table public.diamond_images
  add column if not exists preview_storage_path text,
  add column if not exists preview_ready boolean not null default false,
  add column if not exists preview_updated_at timestamptz;

-- Helpful index for UI queries
create index if not exists idx_diamond_images_preview_ready
  on public.diamond_images (preview_ready);

