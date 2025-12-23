-- =====================================================
-- seed.sql  (LOCAL DEVELOPMENT ONLY)
--
-- This file is executed automatically by:
--   npx supabase db reset
--
-- It creates:
--   - one local org
--   - one local admin user
--   - org membership
--
-- SAFETY:
--   This script will FAIL if app.environment = 'production'
-- =====================================================

-- -----------------------------------------------------
-- Environment guard (LOCAL DEV ONLY)
-- -----------------------------------------------------
do $$
begin
  -- In local supabase, explicitly mark this session as local
  perform set_config('app.environment', 'local', true);

  -- Safety: refuse to run if someone manually sets production
  if current_setting('app.environment', true) = 'production' then
    raise exception 'seed.sql must NOT be run in production';
  end if;
end $$;


-- -----------------------------------------------------
-- 1) Create organization (idempotent)
-- -----------------------------------------------------
insert into public.orgs (slug, name)
values ('first-customer', 'First Customer')
on conflict (slug) do nothing;

-- -----------------------------------------------------
-- 2) Create local dev user in auth.users
-- Email: admin@nova.local
-- Password: Nova123!test
--
-- NOTE:
-- This is ONLY for local Supabase.
-- Never do this in production.
-- -----------------------------------------------------
insert into auth.users (
  id,
  instance_id,
  aud,
  role,
  email,
  encrypted_password,
  email_confirmed_at,
  raw_app_meta_data,
  raw_user_meta_data,
  confirmation_token,
  email_change,
  email_change_token_new,
  recovery_token,
  created_at,
  updated_at
)
values (
  'e2888697-7acd-4721-8e36-a699b26cc43a',
  '00000000-0000-0000-0000-000000000000',
  'authenticated',
  'authenticated',
  'admin@nova.local',
  crypt('Nova123!test', gen_salt('bf')),
  now(),
  '{"provider":"email","providers":["email"]}',
  '{"name":"Local Admin"}',
  '',  -- confirmation_token
  '',  -- email_change
  '',  -- email_change_token_new
  '',  -- recovery_token
  now(),
  now()
)
on conflict (id) do nothing;



-- -----------------------------------------------------
-- 2b) Create matching identity for email provider
-- (required for signInWithPassword to work)
-- -----------------------------------------------------
insert into auth.identities (
  id,
  user_id,
  provider,
  provider_id,
  identity_data,
  last_sign_in_at,
  created_at,
  updated_at
)
values (
  gen_random_uuid(),
  'e2888697-7acd-4721-8e36-a699b26cc43a',
  'email',
  'admin@nova.local',
  jsonb_build_object(
    'sub', 'e2888697-7acd-4721-8e36-a699b26cc43a',
    'email', 'admin@nova.local'
  ),
  now(),
  now(),
  now()
)
on conflict (provider, provider_id) do nothing;

-- -----------------------------------------------------
-- 3) Attach user to org as admin (idempotent)
-- -----------------------------------------------------
insert into public.org_members (org_id, user_id, role)
values (
  (select id from public.orgs where slug = 'first-customer'),
  'e2888697-7acd-4721-8e36-a699b26cc43a',
  'admin'
)
on conflict (org_id, user_id) do nothing;

