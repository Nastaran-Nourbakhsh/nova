-- If preview becomes ready, set preview_updated_at automatically
create or replace function public.set_preview_updated_at()
returns trigger
language plpgsql
as $$
begin
  if new.preview_ready = true and (
      old.preview_ready is distinct from new.preview_ready
      or old.preview_storage_path is distinct from new.preview_storage_path
  ) then
    new.preview_updated_at = now();
  end if;

  return new;
end;
$$;

drop trigger if exists trg_set_preview_updated_at on public.diamond_images;

create trigger trg_set_preview_updated_at
before update on public.diamond_images
for each row
execute function public.set_preview_updated_at();

