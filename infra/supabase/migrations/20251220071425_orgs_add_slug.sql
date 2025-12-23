-- Add slug column
alter table orgs
add column slug text;

-- Backfill slug for existing rows (simple version)
update orgs
set slug = lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'))
where slug is null;

-- Enforce uniqueness
alter table orgs
add constraint orgs_slug_unique unique (slug);

-- Enforce not-null after backfill
alter table orgs
alter column slug set not null;

