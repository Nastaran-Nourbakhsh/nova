-- Create buckets (idempotent)
insert into storage.buckets (id, name, public)
values
  ('diamond-images', 'diamond-images', false),
  ('diamond-previews', 'diamond-previews', false)
on conflict (id) do nothing;

-- ---------- Policies: ORIGINALS bucket ----------
-- No web-user access to originals (device/worker only via service role)
-- (service role bypasses RLS anyway, so we simply do NOT add select policy for authenticated)

-- ---------- Policies: PREVIEWS bucket ----------
drop policy if exists "org_members_can_read_diamond_previews" on storage.objects;

create policy "org_members_can_read_diamond_previews"
on storage.objects
for select
to authenticated
using (
  bucket_id = 'diamond-previews'
  and exists (
    select 1
    from public.org_members om
    join public.orgs o on o.id = om.org_id
    where om.user_id = auth.uid()
      and storage.objects.name like (o.slug || '/%')
  )
);

