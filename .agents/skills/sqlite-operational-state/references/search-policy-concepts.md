# Search Policy Concepts

- **Quota:** number of accepted runs in the current operational day.
- **Operational day:** project-defined day boundary (06:00 Asia/Jakarta), not necessarily midnight.
- **Cooldown:** minimum elapsed time after the last accepted run.
- **Atomic reservation:** check eligibility and reserve quota in one transaction so concurrent requests cannot both pass.
- **Idempotency:** repeat delivery of one logical request returns the same run rather than creating another.
- **Interrupted run:** execution did not reach a terminal state before the process stopped; it is not silently resumed in MVP.
