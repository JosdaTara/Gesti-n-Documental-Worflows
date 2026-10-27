# Gestión Documental con IA — Tercer Corte

Automatización propia en código (FastAPI) + módulo de visión (facturas), migrado desde n8n.

## Stack
Python 3.12 + FastAPI, PostgreSQL (Supabase) + pgvector, Supabase Storage, Gemini 2.5 Flash, LangChain/LangGraph, APScheduler, Tesseract, JWT + bcrypt. Despliegue en Render.

## Requisitos
- Python 3.12
- PostgreSQL 15+ con extensión `vector`
- Tesseract (solo para el plan B de OCR)
- Variables de entorno (ver `.env.example`)

## Instalación local
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # completa las claves
alembic upgrade head          # o: psql $DATABASE_URL -f migrations/001_schema.sql
uvicorn app.main:app --reload
```
API en http://localhost:8000/docs

## Variables de entorno
Ver `.env.example`.

## Despliegue
Dockerfile incluido. En Render: Web Service (Docker) + Cron Job cada 5 min a `/api/admin/run-indexer`. Supabase para DB + Storage.

## Pruebas
```bash
pytest -q
```
