# QA Automation — Build Scaffold

Verified working: backend installs, imports, boots, and responds on `/health`.
Not yet verified in this environment: Postgres connectivity (no Docker
available in the sandbox that built this — verify on your machine).

## What's here

```
qa-automation/
├── backend/
│   ├── app/
│   │   ├── config.py              # Settings loaded from .env
│   │   ├── db.py                  # SQLAlchemy engine/session, Base
│   │   ├── main.py                # FastAPI app + /health
│   │   ├── schemas/
│   │   │   └── check_result.py    # Evidence-first CheckResult contract
│   │   ├── services/
│   │   │   ├── storage/
│   │   │   │   └── audio_storage.py   # AudioStorage interface (Local now, Blob later)
│   │   │   └── calls/
│   │   │       └── processing_service.py  # Isolates BackgroundTasks from pipeline
│   │   ├── api/ models/ repositories/ workers/   # empty, next to fill in
│   ├── alembic/                   # migrations, wired to app.config.settings
│   ├── requirements.txt
│   └── .env.example
├── data/
│   ├── audio/{raw,redacted}/
│   ├── transcripts/
│   └── fixtures/
├── frontend/                      # to be scaffolded with `npm create vite@latest . -- --template react-ts`
└── docker-compose.yml             # Postgres only, on purpose
```

## Get running on your machine

```bash
# 1. Postgres
docker compose up -d postgres
docker compose ps    # wait for "healthy"

# 2. Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
# check: http://localhost:8000/health

# 3. Frontend
cd ../frontend
npm create vite@latest . -- --template react-ts
npm install
npm run dev
# check: http://localhost:5173
```

## Deliberately NOT included yet

- Celery / Redis — using FastAPI BackgroundTasks via `processing_service.py`
  until that proves insufficient. The swap point is isolated on purpose.
- Any STT/LLM provider SDK — add once the provider decision is made, nothing
  else in the codebase should need to change (transcription/extraction sit
  behind their own service interfaces, same pattern as AudioStorage).
- Business tables (Retailer, CheckLibrary, Check, Lead, Call, CheckResult,
  GateDecision, Override). This is deliberately the very next step —
  design these against the actual provided check-library export and
  sandbox payload, not from a generic guess.

## Next step

Domain model + first Alembic migration:
`Retailer → CheckLibrary → Check → Lead → Call → Recording → Transcript →
TranscriptSegment → CheckResult → GateDecision → Override`

Get versioning (`effective_from`/`effective_to`) and the four-status enum
(`PASS / FAIL / LOW_CONFIDENCE / NOT_CHECKABLE`) right in this pass —
retrofitting either later is expensive.


## Deploying the backend to Render

Web Service, **Root Directory** `backend`. Only `DATABASE_URL` is required; every other setting has a default
(full list in `backend/.env.example`).

| Setting | Value |
|---|---|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Pre-Deploy Command (migrations) | `alembic upgrade head` |
| Health Check Path | `/health` |
| `DATABASE_URL` (required) | the **Internal Database URL** of your Render Postgres (`postgres://` / `postgresql://` are fine) |
| `PYTHON_VERSION` | a version Render supports, 3.10 or newer |
| `CORS_ORIGINS` | only if a separately hosted frontend calls the API by absolute URL, e.g. `["https://your-frontend.onrender.com"]` |
| `AUDIO_STORAGE_PATH` | optional; default `/tmp/audio` is **ephemeral** on Render (recordings vanish on restart/deploy), so attach a Disk and set e.g. `/var/data/audio` if they must persist |

If your plan has no Pre-Deploy Command, run the migration once from your machine with the database's **External**
URL: `cd backend`, set `DATABASE_URL` to it (PowerShell `$env:DATABASE_URL="..."`), then `alembic upgrade head`.

A fresh database has no retailers, so seed one once the same way:
`python scripts/load_check_library.py data/fixtures/retailer1_check_library_v2.json` (demo library: synthetic wording).
