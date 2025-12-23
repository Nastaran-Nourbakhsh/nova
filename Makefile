reset:
	cd infra && npx supabase db reset
	sleep 2
	conda run -n nova python scripts/dev_seed_storage.py

