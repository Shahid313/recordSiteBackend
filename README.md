# Constellation Backend (Phase 1: Upload + Frame Extraction)

## Windows: Redis (no Docker)

### Option A (recommended): Memurai (Redis-compatible)
- Install Memurai (Developer edition) and start the service.
- Default endpoint: `localhost:6379`
- Set in `env.example`:
  - `REDIS_URL=redis://localhost:6379/0`

### Option B: Redis for Windows via WSL
- Install WSL2 + Ubuntu, then install Redis inside WSL and expose port 6379.

## Windows: MinIO (no Docker)

1. Download the MinIO Server binary for Windows from the official site.
2. Create a data folder, e.g. `C:\minio\data`
3. In PowerShell:

```powershell
setx MINIO_ROOT_USER "minioadmin"
setx MINIO_ROOT_PASSWORD "minioadmin123"
minio.exe server C:\minio\data --address ":9000" --console-address ":9001"
```

4. MinIO Console: `http://localhost:9001`
5. API endpoint: `http://localhost:9000`

The backend auto-creates buckets on startup:
- `videos`
- `panoramas`
- `thumbnails`
- `exports`

## Environment

Copy `env.example` to `.env` (same folder as `backend/run.py`) and adjust values.

Required for Phase 1:
- `REDIS_URL`
- `MINIO_ENDPOINT`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MINIO_SECURE`

## Running

### Backend API
From `backend/`:

```powershell
pip install -r requirements.txt
python run.py
```

### Celery worker
From `backend/`:

```powershell
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

`--pool=solo` is recommended on Windows.

## Notes
- Upload limit: 2GB (`MAX_VIDEO_SIZE_BYTES`)
- Frame extraction: 1 frame/second (`FRAME_EXTRACTION_INTERVAL_SECONDS`)
- JPEG quality: 90% (hardcoded in `app/services/video_processor.py`)

## Phase 2 (SfM / Constellation graph)

This phase uses OpenCV features (SIFT preferred, ORB fallback). On Windows:
- Install dependencies:

```powershell
pip install -r requirements.txt
```

If you run into OpenCV install issues, upgrade pip and retry:

```powershell
python -m pip install --upgrade pip
pip install opencv-contrib-python
```


