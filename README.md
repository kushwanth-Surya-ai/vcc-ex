# Vehicle Counting & Classification (VCC) System

A full-stack, real-time vehicle detection, tracking, and analytics platform combining a Python YOLO inference pipeline, a FastAPI backend with PostgreSQL, and a React dashboard.

## 1. Prerequisites

- Python 3.11+
- Node 20+
- PostgreSQL 15+

## 2. Quick Start (Single Terminal)

### Database Setup
Ensure PostgreSQL is running locally or remotely. Create a database for VCC:
```sql
CREATE DATABASE vcc_db;
```

### Environment Variables
Copy `.env.example` to `.env` in the root folder, backend folder, and frontend folder.
```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```
Update the values as needed, especially `DATABASE_URL` in `backend/.env`.

### Automated Setup
To install all backend, frontend, and detection dependencies, and run database migrations, simply run this command once from the **root folder**:
```bash
npm run setup
```

### Start the Application
To start both the Backend API and the Frontend Dashboard concurrently in a **single terminal**:
```bash
npm run dev
```
- The Dashboard will be available at `http://localhost:5173`
- The Backend API will be available at `http://localhost:8000`

Login to the dashboard with the seeded admin credentials:
- **Email**: `admin@vcc.local`
- **Password**: `Admin1234!`

### Populate Demo Data
To test the UI without real cameras, run the demo seeder in a separate terminal (or before you start the app). It will generate 7 days of realistic traffic data:
```bash
npm run seed
```

## 3. Adding Camera Feeds
Edit `detection/config.py` and add entries to the `CAMERAS` list:
```python
CAMERAS = [
    {
        "camera_id": "cam_001",
        "location_id": 1,
        "lane_id": 1,
        "rtsp_url": "rtsp://your-camera-url",
        "counting_line_ratio": 0.55,
        "counting_line_axis": "y",
        "count_direction": "both"
    }
]
```

## 4. Auth Overview
- **Browser Users:** Login via dashboard, uses JWT `access_token` (memory) + `refresh_token` (HTTP-only cookie).
- **Service/M2M (Detection):** Authenticate using a static `X-API-Key` configured via `SERVICE_API_KEY` env var.

## 5. TimescaleDB Scaling Path
If write volumes grow large, you can easily install the TimescaleDB extension and convert the `events` table to a hypertable without changing application code:
```sql
SELECT create_hypertable('events', 'timestamp');
```

## 6. Fine-Tuning YOLO26
To support more granular classes (like separating jeeps and vans), fine-tune the model:
1. Label custom dataset (e.g., Roboflow).
2. Configure `data.yaml`.
3. Train with `YOLO().train(...)`.
4. Point `VCC_MODEL_PATH` to the new weights file.

## 7. Security Hardening Before Production
- Set `COOKIE_SECURE=true` in backend `.env`.
- Ensure `ALLOWED_ORIGINS` accurately reflects the production frontend URL.
- Host behind HTTPS.
- Set `RATE_LIMIT_STORAGE_URI=redis://...` if deploying multiple API workers.
- Rotate `JWT_SECRET` and `SERVICE_API_KEY`.

## 8. Known Limitations
- **Speed Analytics:** Requires real-world camera calibration (pixel distance mapping).
- **Sub-class Granularity:** COCO only supports general "car" or "truck". Finer distinction needs a custom-trained model.
- **Incident Correlation:** Current alerts are rule-based thresholds; cross-camera correlations require an advanced alerting engine.
