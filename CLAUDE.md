# QA Automation — Working Context

Living project log. Claude Code auto-loads this file each session, so **update it
at the end of every task** (Status, Decisions, Next). Setup/run instructions live
in `README.md`; this file is for state and decisions.

## Environment
- Windows, project at `D:\qa-automation`. Backend venv: `backend/.venv` (Python 3.14, SQLAlchemy 2.0.x).
- Run Python from `backend/` with `PYTHONPATH=.` (or use the venv's `alembic.exe`, whose `env.py` adds the path itself).
- Postgres: `DATABASE_URL` in `backend/.env` points at `localhost:5432/qa_automation` (user `qa_user`). On this
  machine that is a **local PostgreSQL 18 install, not Docker** (`docker` isn't on PATH); `docker-compose.yml` is
  the intended path elsewhere. Verified reachable 2026-09-19.
- Migrations: run from `backend/` with `.venv/Scripts/alembic.exe <cmd>` (`upgrade head`, `current`,
  `revision --autogenerate -m "..."`). Migration files live in `backend/alembic/versions/`.

## Conventions
- SQLAlchemy 2.x typed style: `Mapped[...]` + `mapped_column`, models subclass `app.db.Base`.
- Every model gets `TimestampMixin` (`created_at`, `updated_at`, DB-side `now()`, timezone-aware).
- Integer autoincrement PKs; a separate `code` column is the stable business identifier.
- New model modules must be imported in `app/models/__init__.py` (that import is what
  registers them on `Base.metadata` for Alembic) **and** added to the explicit model import in `alembic/env.py`.
- Explicit constraint/index names for anything we declare (no naming convention is configured on `Base`).
  PK/FK/`retailers.code` unique get Postgres default names (`*_pkey`, `*_fkey`, `retailers_code_key`).
- Every schema change = a new Alembic revision; review autogenerate output before applying, never edit an applied one.
- Don't implement scoring/API logic until asked; keep changes scoped to the task.

## Status

### Done
- Scaffold (FastAPI `/health`, config, db, alembic wiring, `CheckResult` evidence contract).
- **Models** (`backend/app/models/`), exported from `__init__.py`:
  - `Retailer` (`retailers`): `code` unique, `name`, `is_active`.
  - `CheckLibrary` (`check_libraries`): FK `retailer_id`, `name`, `version`, `effective_from`,
    `effective_to` (NULL = open-ended). Unique `(retailer_id, version)`; CHECK
    `effective_to IS NULL OR effective_to > effective_from`; index `(retailer_id, effective_from, effective_to)`.
  - `Check` (`checks`): FK `library_id`, `code`, `name`, `description`, `check_type`
    (`CheckType` enum: VERBATIM/FACTUAL/BEHAVIOUR), `critical`, `configuration` (JSON; JSONB on Postgres).
    Unique `(library_id, code)`; index `(library_id, check_type)`.
  - Relationships: `Retailer.check_libraries` ⇄ `CheckLibrary.retailer`; `CheckLibrary.checks` ⇄ `Check.library`.
- `alembic/env.py`: explicitly imports `Check, CheckLibrary, Retailer` from `app.models` so autogenerate sees them.
- **Migration `6b4f24c07d74_create_retailer_check_library_check`** (Phase 2): generated, reviewed (no hand edits),
  applied with `alembic upgrade head`; DB is at head. Verified on live Postgres: 3 tables + `alembic_version`,
  all columns/FKs/unique/CHECK constraints/indexes present, `alembic` autogenerate diff vs models is empty, and
  constraints behave (bad `check_type`, duplicate code/version, inverted date range, and deleting a retailer that
  has libraries are all rejected; test data was rolled back).
- **`BaseRepository`** (`backend/app/repositories/base.py`, 34 lines): generic over a model, takes
  `(model, session)`; `get(id)`, `list(**filters)` (equality via `filter_by`), `create(**kwargs)` (add + flush,
  so the PK is set), `commit()`. No business logic; per-model repos should subclass it. Smoke-tested on SQLite.
- **`RetailerRepository`** (`backend/app/repositories/retailer_repository.py`, subclasses `BaseRepository`):
  `get_active_check_library(retailer_id, as_of)` -> `CheckLibrary | None`. Predicate is
  `retailer_id = ? AND effective_from <= as_of AND (effective_to IS NULL OR effective_to >= as_of)` (note the
  parentheses: the spec's literal precedence would also match not-yet-effective libraries). >1 match raises
  `AmbiguousCheckLibraryError`; naive `as_of` raises `ValueError`.
- **Tests**: `backend/tests/` -- `conftest.py` `session` fixture runs against the real Postgres in a transaction
  that's rolled back per test (needs the DB up; **run from `backend/`**: `.venv/Scripts/python.exe -m pytest tests -q`).
  `test_retailer_repository.py`: 14 tests (active, expired, not-yet-effective, gap/no-fallback, missing retailer,
  overlap, shared-boundary, naive datetime). Mutation-checked against the precedence bug.
- **Check-library loader** (`backend/app/services/checks/library_loader.py`, Phase 2.3):
  `load_check_library_from_json(session, source)` where `source` is a parsed dict or a path to a JSON file.
  Gets-or-creates the `Retailer` by `code` (an existing retailer is *not* updated), creates the `CheckLibrary`
  (version/effective dates taken from the JSON), creates every `Check`, commits, returns the library. All-or-nothing
  (`session.rollback()` + re-raise on any error). Only maps/coerces (ISO datetimes, `CheckType(...)`); no business
  rules. JSON shape is documented in the module docstring -- **no real export exists in the repo yet, so the shape
  is our own, mirroring the model columns; adapt the loader when the real check-library export arrives.**
  Spec said `Check.config`; the actual column is `Check.configuration`. `tests/test_library_loader.py`: 7 tests.
- **Demo fixture** (Phase 2.4): `backend/data/fixtures/retailer1_check_library_v1.json` (path as requested; note the
  project-root `data/fixtures/` also exists and the README points there -- consolidate later if desired).
  6 checks: `recording_disclaimer`, `dmo_verbatim` (VERBATIM, critical), `rate_match`, `email_match`, `dob_match`
  (FACTUAL, critical), `dead_air` (BEHAVIOUR, non-critical). Loads via the loader; `tests/test_demo_fixture.py`.
  **It is NOT the real export**: the handout (`~/Downloads/QA-Automation-Handout.pdf`, p.9) says a retailer
  check-library export is handed out on the day, and it isn't in the repo/handout. Built only from the handout's
  worked example (p.5) + check-type table (p.4). Unknowns left as `null`, deliberately: `approved_script` (both
  VERBATIM checks -- no script text provided; a check with a null script must resolve to NOT_CHECKABLE, never a
  guess), `dead_air.threshold_seconds`. Not in the data, so omitted: weights, "Account holder confirmed" (critical
  in the example, but its check type isn't stated), match strictness. `effective_from` 2026-01-01 is a placeholder
  (column is NOT NULL). DOB `critical: true` is inferred from "factual checks block the sale" (p.4).
  **When the real export arrives, replace this file / adapt the loader.**
- **Loader script** (Phase 2.5): `backend/scripts/load_check_library.py [path]` (default = the retailer1 fixture;
  run from `backend/`: `.venv/Scripts/python.exe scripts/load_check_library.py`; must be run from `backend/` so
  `.env` is found). Idempotent: if `(retailer code, version)` already exists it prints "Already loaded ... skipped"
  and writes nothing; if the existing library's check codes differ from the file it warns and exits 1 (bump the
  version instead of editing a loaded one). The existence check lives in the script, not the loader.
  **The dev DB now holds real data**: `retailer1` (id 102) / library v1 (id 120) / 6 checks, loaded + verified in
  Postgres (no duplicate check codes). Because of that, tests must NOT assume an empty DB: use baseline-relative
  counts and throwaway retailer codes (`acme`, `retailer1_test`) -- fixed in `test_library_loader.py` and
  `test_demo_fixture.py`. Sequence ids are high (tests roll back but consume ids); harmless.
- **Calls API** (Phase 3):
  - New models `Lead` (`leads`: `retailer_id`, `external_lead_id`, `crm_fields` JSONB; unique `(retailer_id,
    external_lead_id)`), `Call` (`calls`: `lead_id`, `status` = `CallStatus` PROCESSING/COMPLETED/FAILED, VARCHAR+CHECK),
    `Recording` (`recordings`: `call_id` UNIQUE = one recording per call, `storage_reference`, `original_filename`,
    `content_type`, `size_bytes`). Migration `4af4b1c68e72_create_lead_call_recording` applied (head), no drift.
    `alembic/env.py` import list extended.
  - `POST /api/v1/calls` (`app/api/calls.py`, schemas in `app/schemas/calls.py`): multipart form -- `retailer_code`,
    `external_lead_id`, `crm_fields` (JSON string, default `{}`), `audio` file. Returns **202** `{call_id, status:
    "PROCESSING"}`. Flow: retailer lookup (unknown -> 404) -> get/create Lead (a non-empty `crm_fields` *replaces* the
    stored snapshot; empty never wipes it) -> Call -> `AudioStorage.save` -> Recording -> commit ->
    `background_tasks.add_task(processing_service.process_call, str(call_id))`. The route never calls
    transcription/scoring. Errors: bad/non-object crm JSON or empty audio -> 422. Storage/DB failure rolls back and
    deletes the saved file. Filename is reduced to its basename (path-traversal guard).
  - `GET /api/v1/calls/{call_id}` -> id, status, retailer_code, external_lead_id, recording metadata, timestamps
    (deliberately omits `crm_fields` and `storage_reference`; no auth exists yet). 404 if missing.
  - Router registered in `main.py` under `settings.api_prefix`. `tests/test_calls_api.py`: 14 tests (36 total, passing);
    they replace `processing_service.process_call` with a recorder.
  - **Live-tested** on uvicorn with a synthetic WAV + synthetic lead 9000001 (call id 11): `/docs` and OpenAPI show
    the endpoints; row + file verified in Postgres/disk. That test data is still in the dev DB / `data/audio/raw/11/`.
  - **Known** (Phase 3 note; the stub part is obsolete -- `process_call` now runs the full pipeline, see Phase 7):
    `LocalAudioStorage` returns a path relative to the server's cwd (e.g. `../data/audio/raw/<id>/file`), so run the
    server from `backend/`. No upload size limit yet.
- **Fixture transcript ingestion** (Phase 4):
  - `services/transcription/base.py`: `TranscriptSegmentData(speaker, text, start_time, end_time, confidence)` (frozen
    dataclass; times = seconds) and `TranscriptionProvider` ABC (`name`, `transcribe(audio: bytes) -> list[...]`).
  - `fixture_provider.py`: `FixtureTranscriptionProvider(fixture_path=DEFAULT_FIXTURE)` -- ignores the audio and
    replays `backend/data/fixtures/synthetic_transcript.json` for EVERY call (13 segments, clearly-marked SYNTHETIC;
    values line up with the demo lead's CRM: email synthetic.test@example.com, dob 1990-01-01, peak 31.9c/kWh; wording
    is illustrative, NOT any retailer's script). It deliberately contains: a 47 s gap (38.0->85.0, dead-air case), a
    low-confidence email segment (0.62 -> LOW_CONFIDENCE case), and an overlapping customer interjection (99.6 < 100.2).
  - `transcription_service.py`: `transcribe_call(session, call_id, provider, storage)` reads the audio via
    `AudioStorage`, sorts segments by `start_time` (stable), creates ONE `Transcript` + `TranscriptSegment` rows with
    1-based `sequence`, commits (rollback on error). **Idempotent**: returns the existing transcript if the call has one.
    `get_transcription_provider()` is the swap point for a real STT provider.
  - Models `Transcript` (`transcripts`: `call_id` UNIQUE, `provider`) and `TranscriptSegment` (`transcript_segments`:
    `transcript_id`, `sequence`, `speaker`, `text`, `start_time`/`end_time` double precision, `confidence` nullable;
    unique `(transcript_id, sequence)`; CHECK `end_time >= start_time`; CHECK confidence in [0,1]). `Call.transcript`
    relationship added. Migration `75e7236a7dfb_create_transcript_transcript_segment` applied (head), no drift.
  - **`processing_service.process_call` is wired** (extended in Phase 7 to transcribe -> score -> gate; see below):
    own `SessionLocal()`, on ANY unexpected exception marks the call `FAILED` and re-raises. The API route still only
    calls `process_call`.
  - `tests/test_transcription.py`: 13 tests (provider, ordering/overlap, null confidence, idempotency, missing
    call/recording, rollback on provider error / bad segment, DB CHECKs, process_call success + FAILED path, POST
    end-to-end). 49 tests total, passing. **Live-verified**: POST -> call 54 -> transcript 17 with 13 segments in Postgres.
  - Leftover dev data: calls 11 (pre-pipeline, no transcript, stuck PROCESSING) and 54 (transcribed; it predates
    scoring so it stays PROCESSING) + their audio files.
- **Verbatim check engine** (Phase 5) -- `backend/app/services/checks/`:
  - `base.py`: `CheckRunner` ABC, `run(call, check, segments, crm_fields=None, retailer_plan=None) -> CheckResult`.
    Reuses the existing `schemas/check_result.py` contract unchanged (`CheckResult`/`Evidence`/`CheckStatus`).
    Runners are pure (never modify call/check/segments). **Phase 6 `FactualCheckRunner` uses this same interface**
    (it is the one that will consume `crm_fields` / `retailer_plan`; Verbatim ignores them).
  - `text_utils.py`: `normalize_text` (lowercase, apostrophes removed, other punctuation -> space, whitespace
    collapsed), `similarity(a, b)` (difflib `SequenceMatcher` ratio on normalized text), `best_window_match(phrase,
    words)` (best-matching run of consecutive words -> `WindowMatch(similarity, start, end)`).
  - `verbatim_runner.py`: `VerbatimCheckRunner`. **Config keys** (in `Check.configuration`):
    `required_phrases` (non-empty list of non-blank strings) and `match_threshold` (number, 0 < t <= 1). Only
    segments whose `speaker` is `AGENT` (case-insensitive; constant `AGENT_SPEAKER`) are searched; the agent's words are
    joined across consecutive agent segments, so a phrase split over turns (or interrupted by the customer) still matches.
    PASS = every phrase >= threshold; FAIL = any phrase below it. **NOT_CHECKABLE** = no agent speech, OR unusable
    config (missing/empty phrases or threshold) -- we never guess a script or a strictness. Raises `ValueError` if
    given a non-VERBATIM check. `check_id` = `check.code`; `check_version` = `check.library.version`.
    Evidence = the agent segment(s) holding the best-matching words (id, text, start/end); on FAIL it is the *closest*
    wording, or empty when nothing resembled the phrase. `asr_confidence` = min over evidence segments (None if none);
    `rule_confidence`/`extraction_confidence` left None. `expected_value` = phrases joined by ` | `; `actual_value` =
    the matched agent words (normalized form) joined by ` | ` (None if nothing matched).
  - **Speed/pruning**: exhaustive window scoring took 6-9 s per phrase on a ~2.5k-word agent transcript, so
    `best_window_match` only scores windows that start/end on an "anchor" word (resembles a phrase word, char ratio
    >= 0.75) and contain >= 40% of the phrase's word count as anchors: ~4-16 ms. Every scored window is exact, so
    pruning can only LOWER a score (never a false PASS). Measured vs exhaustive on 1,500 random perturbations: 0 higher;
    where exhaustive passed at threshold 0.9 (1,167 cases) pruning wrongly failed 2 (both phrases with ~half their words
    edited). Window length range: n - max(2, n//4) .. n + max(1, n//5) words.
  - **Known limits**: fuzzy similarity is not semantic -- one inserted "not" barely moves the score, so legally
    critical scripts need a HIGH threshold (or 1.0). No LOW_CONFIDENCE is produced (would need a defined ASR-confidence
    threshold); a phrase lost to a mis-heard word is a FAIL, not a QA route -- decide that policy with the gate work.
    Speaker labels are hardcoded to `AGENT` (matches the fixture; a real STT diarizer will need a mapping).
  - **The demo Retailer 1 fixture's verbatim checks have `{"approved_script": null}`** -- no `required_phrases` /
    `match_threshold`, so they evaluate to NOT_CHECKABLE (tested). Fixture and DB rows were deliberately left
    untouched; when the real script text arrives, bump the library version with the two keys populated.
  - Tests: `tests/services/checks/test_verbatim_runner.py` (33) and `test_text_utils.py` (20), in-memory objects, no DB.
    Mutation-checked (7 injected bugs all caught). **Full suite: 102 passing.** Nothing else in the app was modified.
- **Factual check engine** (Phase 6) -- `backend/app/services/checks/`: `source_resolver.py`, `extraction.py`,
  `normalization.py` (extra file: the pure normalizers + spoken-number/date parsing), `factual_check.py`;
  tests in `backend/tests/test_factual_check.py` (133). **235 tests total, all passing; Phase 1-5 files untouched.**
  - **Interface decision (needs owner confirmation):** the Phase 6 brief described a flat
    `run(check_id, check_version, critical, config, segments, crm_fields, retailer_plan)` "already implemented by
    VerbatimCheckRunner" -- it is NOT: Verbatim (and `base.py`) are `run(call, check, segments, crm_fields=None,
    retailer_plan=None)`. Phase 1-5 were left unmodified, so `FactualCheckRunner.run` has that same real signature
    (a test asserts `inspect.signature` equality with Verbatim and the ABC). The flat form exists as a pure method
    `FactualCheckRunner.evaluate(check_id, check_version, critical, config, segments, crm_fields, retailer_plan)`
    that `run` delegates to (`run` unpacks `check.code`, `check.library.version`, `check.critical`,
    `check.configuration`). If the flat signature is wanted on `run` itself, base.py + VerbatimCheckRunner + their
    tests must change together. Also: there is no `app/schemas/segment.py` (runners take `TranscriptSegment`); files
    were named literally as requested (`factual_check.py`, `tests/test_factual_check.py`) although Verbatim's are
    `verbatim_runner.py` / `tests/services/checks/test_verbatim_runner.py`.
  - **Check config** (`Check.configuration`): `field` (one of `extraction.FIELD_SPECS`: price, rate,
    total_minimum_cost, download_speed, upload_speed, modem_model, email, dob, name, phone, service_address,
    delivery_address) + `source_of_truth` (`"CRM.<path>"` / `"RETAILER_PLAN.<path>"`); optional `speaker`
    (agent|customer|any), `confidence_threshold` (0.7), `tolerance` (0.01), `fuzzy_threshold` (name .85 / address .8 /
    modem .95), `context_keywords`. **The demo Retailer 1 factual checks use the older shape
    `{compare_to, field}` with no `source_of_truth`, so they evaluate to NOT_CHECKABLE (tested)** -- a v2 fixture
    needs `source_of_truth` and real CRM/plan key names (not known yet).
  - **Flow/statuses**: unusable config -> NOT_CHECKABLE; no expected value -> NOT_CHECKABLE; value spoken but
    `[PLACEHOLDER]`-masked -> NOT_CHECKABLE ("redacted", includes expected_value); not said -> NOT_CHECKABLE ("not
    found in transcript"); several conflicting values for a single-mention field -> LOW_CONFIDENCE; extraction
    confidence < threshold -> LOW_CONFIDENCE (checked BEFORE comparing, so an unsure mismatch is not a critical FAIL);
    unreadable spoken value -> LOW_CONFIDENCE; unreadable expected value -> NOT_CHECKABLE; else PASS/FAIL.
    `extraction_confidence` = ASR confidence of the source segment (None = unknown, treated as not low);
    `rule_confidence` = 1.0 for exact/numeric, the similarity for fuzzy (name/address/modem), None if no comparison.
  - **Mention selection**: rate/price/total_minimum_cost = FIRST agent mention; addresses = LAST customer mention;
    everything else single-mention (same value repeated corroborates, different values -> conflict).
    Default speaker agent: price, rate, download/upload_speed, modem_model, total_minimum_cost; customer: email,
    dob, name, phone, service/delivery_address. Service and delivery addresses are extracted (by context keywords,
    including the previous segment) and compared independently.
  - **Normalization** (pure, `normalization.normalize_field`): email lower/trim; rate -> cents, money -> dollars
    ("$"/"dollars"/"cents"/"c" honoured), speed -> Mbps (gbps x1000); dob -> ISO; name/address -> `normalize_text`;
    modem -> spoken numbers as digits + short letter prefix joined ("CF forty" -> "cf40"); phone -> digits (+61 -> 0).
    Spoken numbers/dates are understood because the synthetic STT writes them out ("thirty one point nine",
    "the first of January nineteen ninety"). **DOB accepts ISO and DAY-first numeric (DD/MM/YYYY) only**; month-first
    or 2-digit-year dates are unreadable (None), never guessed -- the CRM export's real formats are still unknown.
    Fuzzy compare requires every number in the two strings to be identical (12 vs 21 smith st is a mismatch).
  - **Verified on the synthetic transcript**: DOB and rate PASS from spoken words; the email (ASR 0.62) is
    LOW_CONFIDENCE. 14 injected bugs all caught by the tests; extracting all 12 fields from an 800-segment call: 0.07 s.
  - **Known limits**: finders are heuristics for typical phrasing. An address said without its suburb/postcode
    compares against the full CRM address and will FAIL (safe direction: HOLD, not a false pass). Spelled-out street
    numbers are not converted when comparing addresses. A redacted mention AFTER a real one is not noticed. Rates
    without a "cents"/"c" unit need a rate/price/kWh word in the segment. No auth/redaction service exists yet.
- **Scoring, gate engine and pipeline** (Phase 7) -- `services/scoring/scoring_service.py`,
  `services/gate/gate_engine.py`, `services/gate/gate_service.py`, `services/calls/processing_service.py`.
  **299 tests passing** (`tests/services/scoring`, `tests/services/gate`, `tests/services/test_processing_pipeline.py`,
  `tests/test_calls_api_call_start.py`; shared builders in `tests/services/scenario.py`).
  - **Schema added (migration `e4105de01ace`)**: `Call.call_started_at` (nullable timestamptz; NULL = unknown, never
    guessed -- existing calls keep NULL), `check_results` (ORM `CheckResultRecord`; the name `CheckResult` is the
    dataclass contract; unique `(call_id, check_id)`, status VARCHAR+CHECK, `evidence` JSONB, code/version/type/critical
    snapshotted on the row) and `gate_decisions` (ORM `GateDecision`; `call_id` UNIQUE, `GateStatus`
    AUTO_PASSED/HELD/QA_REVIEW, `reason`, `decided_at`, nullable `check_library_id` + `check_library_version`).
    `Call.gate_decision` relationship added. **Because no service updates/deletes them, results and decisions are
    append-only audit records.**
  - **`POST /api/v1/calls`** gained an optional `call_started_at` form field (ISO 8601 WITH timezone; naive -> 422).
    **Superseded by the "Automatic call_started_at" entry below**: it is no longer required; when omitted the server
    sets it to the current UTC time. (Originally, omitted meant NULL and a QA_REVIEW routing.)
    `GET /api/v1/calls/{id}` now also returns `call_started_at` and `gate_decision` (status, reason, decided_at,
    check_library_version).
  - **ScoringService** `score_call(session, call_id, runners=None) -> ScoringOutcome(library, problem, results,
    records, errors, skipped)`: picks the library with `RetailerRepository.get_active_check_library(retailer,
    call_started_at)`; no start time / no active library / overlapping libraries => `problem` (no results, no
    fallback to another version). VERBATIM -> VerbatimCheckRunner, FACTUAL -> FactualCheckRunner (dispatch table
    `RUNNERS`); BEHAVIOUR has no runner: skipped, not persisted, never reaches the gate. `crm_fields` = `Lead.crm_fields`;
    `retailer_plan` = None (no plan source exists, so `RETAILER_PLAN.*` checks are NOT_CHECKABLE). A runner exception
    is isolated per check: that check is stored NOT_CHECKABLE with reason "Scoring error: ..." and reported in `errors`.
    Scoring a call twice raises `CallAlreadyScoredError`. Flushes, never commits.
  - **GateEngine** (pure) `decide(results, library_problem=None, errors=()) -> GateOutcome(status, reason)`:
    critical FAIL -> HELD (wins over the QA triggers; the summary mentions both); else any LOW_CONFIDENCE (critical or
    not), critical NOT_CHECKABLE, library problem, scoring error, or **nothing evaluated** -> QA_REVIEW; else
    AUTO_PASSED. Non-critical NOT_CHECKABLE/FAIL don't block (listed in the summary). BEHAVIOUR results are ignored.
    **Deliberate extra safety beyond the brief**: zero evaluated checks (e.g. a library of only behaviour checks) is
    QA_REVIEW, not a vacuous AUTO_PASSED.
  - **GateService** `decide_call(session, call_id, scoring)`: runs the engine, stores status + reasoning +
    `decided_at` + library id/version; raises `GateDecisionExistsError` if the call already has one (never overwrites).
    Flushes, never commits.
  - **ProcessingService**: transcribe -> (skip if a decision already exists: re-runs are no-ops) -> score -> gate;
    results + decision + final status are committed in ONE transaction. Statuses: **COMPLETED** = a decision was
    recorded (HELD / QA_REVIEW / no-library are successful outcomes: routed to a human). A per-check scoring error still
    records a QA_REVIEW decision but marks the call **FAILED**. Any other unexpected error: rollback, call FAILED, NO
    decision, re-raised. Nothing that failed is ever AUTO_PASSED. (FAILED calls without a decision need an ops
    process; not built.)
  - The Phase 4 test that asserted "call stays PROCESSING after process_call" was updated (that behaviour was
    replaced on purpose): it now expects COMPLETED + QA_REVIEW for a call with no start time/library.
  - **Live-verified** (uvicorn + real Postgres + fixture STT): retailer1 fixture -> QA_REVIEW (all 5 checks
    NOT_CHECKABLE); synthetic `demo_gate` retailer (library id 877, usable config) -> AUTO_PASSED, HELD (wrong CRM
    DOB), QA_REVIEW (no start time), QA_REVIEW (call before the library's effective_from). 20 injected bugs caught.
    **Leftover dev data**: retailer `demo_gate` + library v1, calls 679-683 (leads 7001-7005) and their results/decisions.
  - **Known limits**: the shipped Retailer 1 library can only ever produce QA_REVIEW until the real script text
    (`required_phrases`/`match_threshold`) and factual `field`+`source_of_truth` are supplied; no 5% human sampling
    yet; no redaction step; call_started_at defaults to the receipt time (see "Automatic call_started_at").
- **Evidence API and TL review page** (Phase 8):
  - **`GET /api/v1/calls/{call_id}/review`** (`app/api/review.py`, `app/schemas/review.py`): one payload =
    `call` (id, status, call_started_at, timestamps, `recording` metadata or null), `lead` (id, external_lead_id,
    retailer {id, code, name}), `gate_decision` (status, reason, decided_at, library id/version; null until decided) and
    `checks[]`: each `{check: {id, code, name, type, critical, version}, status, reason, expected_value,
    actual_value, confidence: {asr, extraction, rule, overall(min of present)}, evidence: [{segment_id, text,
    start_time, end_time}]}`. **Order is deterministic**: FAIL, LOW_CONFIDENCE, NOT_CHECKABLE, PASS; within a status
    critical first, then check code. 404 for an unknown call; a call with no results yet returns `gate_decision: null`
    and `checks: []`. **The CRM snapshot (`Lead.crm_fields`) and the storage path are deliberately NOT exposed** (no auth yet).
  - **`GET /api/v1/calls/{call_id}/audio`** (`app/api/audio.py`): serves the recording through the `AudioStorage`
    interface (`storage.get()` -> bytes; no path/stream API exists, so this stays backend-agnostic) with hand-written
    HTTP Range support: `bytes=a-b`, `a-`, `-n` -> 206 + Content-Range; start past the end or `-0` -> 416
    (`Content-Range: bytes */size`); malformed (`abc`, `5-2`, `-`, non-bytes unit) -> 400; multiple ranges -> whole file
    (RFC-allowed). 404 for unknown call / no recording / file missing from storage (no internal path leaked).
    Content-Type = the upload's type if it is `audio/*`, else guessed from the filename (a generic `octet-stream` guess
    never beats a specific stored type). `Cache-Control: private`. Reads the whole file per request (fine for call
    audio; revisit for large files/blob storage).
  - **Frontend** (`frontend/`, Vite + React 19 + TS scaffold; the requested files are `.js`/`.jsx`, so
    `allowJs`/`checkJs:false` were added to `tsconfig.app.json`): `src/api/client.js` (`getReview`, `audioUrl`,
    `ApiError` with friendly 404/network/server messages, `VITE_API_BASE` optional), `src/components/AudioPlayer.jsx`
    (exposes `seekAndPlay(seconds)` via `ref`: sets `currentTime`, calls `play()`, surfaces blocked/failed playback),
    `CheckResult.jsx` (status pill, type + critical badges, reason, expected vs actual, confidence chips, evidence
    buttons `m:ss-m:ss text`), `LeadReview.jsx` (gate banner, lead/call panel with status counts, sticky player,
    check list; **clicking evidence seeks to its `start_time` and plays**, and highlights the active item),
    plus `review.css` (light/dark), `src/lib/format.js` (small extra helper: m:ss, dates, labels) and a replacement
    `App.tsx` (the Vite demo App and `App.css` were removed): route `/calls/<id>/review` (or `?call=<id>`), else an
    "open a call" box. No router library.
  - **How to run it**: backend `cd backend && .venv/Scripts/uvicorn.exe app.main:app --port 8000`; frontend
    `cd frontend && npm run dev`, open `http://localhost:5173/calls/<id>/review`. **Vite proxies `/api` to
    `http://127.0.0.1:8000`** (override with env `VITE_BACKEND_URL`); the backend has NO CORS config, so a separately
    hosted frontend needs CORS added first. Demo call to open: **749** (2-minute synthetic audio; HELD with a FAIL, a
    NOT_CHECKABLE and a PASS; evidence at 0:00, 0:21). Note the fixture STT returns the same transcript (timestamps to
    ~107 s) for EVERY call, so audio shorter than ~110 s can't be sought to all evidence (older demo calls have 2 s audio).
  - **Tests**: backend `tests/test_review_api.py` (10) + `tests/test_audio_api.py` (32) -> **341 backend tests
    passing**. Frontend (new devDeps: vitest 5, jsdom, @testing-library/react + dom; `npm test`): 52 tests
    (`LeadReview.test.jsx`, `client.test.js`, `format.test.js`), `npm run lint` clean, `npm run build`
    (`tsc -b && vite build`) passes. 15 injected frontend bugs (wrong seek time, no play(), end_time instead of
    start_time, wrong URLs...) all caught. **Not done: a real-browser check** (no browser tooling was available);
    integration was verified with the real backend + Vite dev proxy via HTTP (review payload, 200/206/416/400/404).
  - **Housekeeping**: an orphaned uvicorn child left from the Phase 7 live test was holding port 8000 with old code and
    was stopped; on Windows kill servers with `taskkill /T` (whole tree) or they leave such orphans.
- **Automatic `call_started_at`** (post-Phase 8 change): `POST /api/v1/calls` needs only `retailer_code`,
  `external_lead_id` and `audio` (OpenAPI `required` confirms). If `call_started_at` is not sent, the route sets it to
  `datetime.now(timezone.utc)` when creating the `Call` (`app/api/calls.py`; there is no `call_service.py`, the Call is
  created in the route). The form field remains as an OPTIONAL override (still must carry a timezone offset, naive -> 422)
  so a late/backfilled upload can supply the true call time. The column stays nullable (no migration): old calls keep NULL
  and still route to QA_REVIEW ("start time is not set"). `CallRead`/`GET /calls/{id}` return it. Upload/processing flow
  untouched. Verified: Phase 7 scoring uses the generated value to choose the library (tests: auto-pass with the active
  version, the version in effect NOW beats an older one, a not-yet-effective library is NOT used and there is no
  fallback, an explicit start still selects the older version); live: calls 1105/1106 got `call_started_at` = server now
  and library v1. **Tradeoff to keep in mind**: the value is the time the call was RECEIVED, not when it started, so
  (a) a call ingested just after a library version changes over can be scored against the newer version, and (b) late or
  backfilled uploads are scored against TODAY's rules unless the caller sends the real start time -- which conflicts with
  "scored against the rules that were live on the call date" for those cases. Tests: 348 backend passing.

### Not done / next
- Remaining domain tables per README: `CheckResult → GateDecision → Override`
  (`Lead`, `Call`, `Recording`, `Transcript`, `TranscriptSegment` now exist).
- Frontend next steps (not started): a queue/list of held and QA calls (needs a list endpoint), override/resolve
  actions, auth. The review page is a single-call view only.
- Still open from Phase 6: confirm the `run` signature question (ORM `call, check` vs flat args; see Phase 6).
- **Behaviour runner** (dead air etc.), a **retailer plan / rate card** source for `RETAILER_PLAN.*`, real Retailer 1
  check config (v2 library), 5% clean-call human sampling, human override + audit (`Override` table), redaction step,
  ops handling for FAILED calls (no decision), dashboards/frontend.
- Real STT provider (Azure Speech / etc.) behind `TranscriptionProvider`; redaction.
- Still to do on the contract side: `Override` table (human decisions on a result/decision, logged not dropped).
- Per-model repositories (subclassing `BaseRepository`) as needed; more API routes (list/queue views) not started.

## Decisions & rationale
- **FKs use `ON DELETE RESTRICT`**, no ORM delete cascade: libraries/checks are versioned audit history
  that later results will reference, so they must not disappear when a parent is deleted.
- **`check_type` is VARCHAR + named CHECK constraint**, not a native Postgres enum, so adding a type later
  is a simple migration rather than `ALTER TYPE`. DB stores the enum *values* (`'VERBATIM'`, …).
- **Non-overlap of a retailer's library version date ranges is not enforced in the DB** (would need a
  `btree_gist` exclusion constraint). Left to the service layer; revisit if it matters.
- `effective_from`/`effective_to` are timezone-aware timestamps and **both ends are inclusive** (as specified for
  `get_active_check_library`). Consequence: two versions handing off at the *same instant* overlap at that instant
  and lookup raises `AmbiguousCheckLibraryError` -- set the old version's `effective_to` a hair before the new
  version's `effective_from` (or revisit and make `effective_to` exclusive). Missing library => `None`;
  overlap => error; never a silent fallback to another version.
- `Check.configuration` shape is intentionally free-form; validate per `check_type` in the check
  implementations, not in the schema. Has no DB-side default (ORM default is `{}`).
- Retailer's `code`/`is_active` and library `name` were not in the spec; added as minimal, conventional fields.

## Changelog
- 2026-09-19: `POST /calls` sets `call_started_at = now(UTC)` automatically when omitted (optional override kept);
  tests updated/added (348 backend passing); verified live and through the Phase 7 pipeline.
- 2026-09-19: Phase 8 -- review API (`/calls/{id}/review`), audio API with Range/seek (`/calls/{id}/audio`), React review
  page (gate banner, checks, evidence, audio player, click-to-seek); vitest added; 42 backend + 52 frontend tests
  (341 backend total); verified through the Vite proxy against the real backend.
- 2026-09-19: Phase 7 -- scoring service, gate engine + service, pipeline wiring (COMPLETED/FAILED, atomic commit),
  `Call.call_started_at`, `check_results` + `gate_decisions` tables (migration e4105de01ace), API `call_started_at` +
  decision on GET; 64 new tests, 299 total passing; live-verified.
- 2026-09-19: Phase 6 -- `FactualCheckRunner` (extraction, source resolver, normalization, evaluate/run), 133 new
  tests, 235 total passing; interface mismatch in the brief resolved by keeping Phase 1-5 untouched (see Phase 6).
- 2026-09-19: Phase 5 -- `CheckRunner` interface, text utils, `VerbatimCheckRunner` (agent-only, fuzzy, multi-phrase,
  evidence + confidence), anchor-pruned window search (~1000x faster); 53 new tests, 102 total passing.
- 2026-09-19: Phase 4 -- transcription provider interface + fixture provider + `transcribe_call`; Transcript /
  TranscriptSegment models + migration `75e7236a7dfb`; `process_call` wired (FAILED on error); 13 tests; live-verified.
- 2026-09-19: Phase 3 -- Lead/Call/Recording models + migration `4af4b1c68e72`; `POST/GET /api/v1/calls`;
  14 API tests; live-tested with a synthetic lead + WAV.
- 2026-09-19: Phase 2.5 -- idempotent `scripts/load_check_library.py`; ran it against dev Postgres (retailer1 v1,
  6 checks); made loader/fixture tests independent of DB contents (22 passing).
- 2026-09-19: Phase 2.4 -- Retailer 1 demo fixture (6 checks) from the handout + load test (22 tests total).
- 2026-09-19: Phase 2.3 -- check-library JSON loader + 7 tests (21 total, all passing).
- 2026-09-19: Phase 2.2 -- `RetailerRepository.get_active_check_library`, pytest infra + 14 tests; docs now say
  `effective_to` is inclusive.
- 2026-09-19: Added `repositories/base.py` (`BaseRepository`). `create()` flushes but does not commit — callers
  (services) own the transaction via `commit()`.
- 2026-09-19: Phase 2 — generated + applied migration `6b4f24c07d74` for the three tables; made env.py model
  imports explicit; verified against live PostgreSQL (see Done).
- 2026-09-19: Added `Retailer`, `CheckLibrary`, `Check` models + `_mixins.py`; wired `app.models` into
  `alembic/env.py`; created this file.
