# Table Schema Standardization Plan (v0.3.0)

## Goal
Make the Table Storage schema and access patterns consistent, typed, and testable across API gateway, workers, and training worker.

## Scope
- `api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py`
- All call sites of TableManager methods (function app blueprints + workers + training worker)
- Public exports in `api/v0.3.0/shared/src/cloudfolio_shared/table/__init__.py`
- Tests in `api/v0.3.0/shared/src/cloudfolio_shared/table/tests/`

## Current Issues Observed
- Contract drift across modules (e.g., repo languages queried by `job_id` in `TableManager`, but some call sites appear to expect `(username, repo_name)`).
- Stale tests that don’t model the real Azure Table API surface (`query_entities`, `delete_entity`) and mismatched field names.
- Inconsistent naming across rows/fields (`stars` vs `stars_count`, `homepage` vs `homepage_url`).
- Timestamp encoding is Azure-safe in storage, but some data paths/tests assume plain ISO strings.

## Standardization Rules (Canonical)
### 1) TableNames
- Treat `TableNames()` as the single source of truth.
- Never instantiate `TableNames` with ad-hoc fields in tests or call sites.

### 2) Keys
- PartitionKey should represent the highest-cardinality and most common query dimension.
- Prefer these conventions:
  - Candidate-scoped tables: `PartitionKey = username`
  - Job-scoped tables: `PartitionKey = job_id`
  - Composite RowKey: stable, deterministic, and *unique within partition*.

### 3) Timestamps
- Store timestamps using `_azure_safe_timestamp()` when persisting to Table.
- Restore timestamps using `_restore_iso_timestamp()` in all deserializers.
- Always use ISO 8601 strings in the public API layer.

### 4) JSON fields
- Any field stored as JSON must:
  - Serialize via `_safe_json_dump_limited()`
  - Deserialize with `json.loads()` guarded by `try/except` returning a safe default (e.g., `{}` / `[]`).

### 5) Field naming
- Prefer explicit suffixes:
  - Counts: `*_count` (e.g., `stars_count`)
  - URLs: `*_url` (e.g., `homepage_url`)
  - Booleans: `is_*`

### 6) TableManager API contracts
- Each public method must document (in docstring) its key contract:
  - What is the PartitionKey and RowKey?
  - Which fields are required?
  - What types are returned?

## Work Items
1. **Inventory call sites**
   - Find all usages of `TableManager.query_repo_languages` / `RepoLanguagesRow`.
   - Decide the canonical query shape (job-scoped vs username+repo scoped).

2. **Resolve contract drift**
   - If job-scoped is canonical: update any call sites expecting `(username, repo_name)`.
   - If repo-scoped is needed: add a dedicated helper method (without changing the existing one) and migrate call sites intentionally.

3. **Normalize row dataclasses**
   - Ensure every table has a dataclass row and a single deserializer.
   - Ensure `cloudfolio_shared.table` exports are complete and accurate.

4. **Strengthen tests**
   - Keep the fake table client surface aligned with the production usage:
     - `upsert_entity`, `get_entity`, `list_entities`, `query_entities`, `delete_entity`.
   - Add tests whenever a new table/method is introduced.

5. **Add lightweight linting checks (optional)**
   - Add `python -m compileall` and `pytest` to CI/dev scripts.
   - Consider `ruff` rules for unused imports and common mistakes.

## Success Criteria
- Tests cover all TableManager public APIs.
- No call site relies on stale signatures/fields.
- Timestamp and JSON handling are consistent end-to-end.
- Adding a new table requires: dataclass + serializer/deserializer + tests + export.
