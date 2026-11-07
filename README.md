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

## Motor de automatización (reemplazo de n8n)
| Flujo n8n | Ahora en código | Disparador |
|---|---|---|
| WF1 Indexación masiva | `app/services/indexing_service.py` (`sweep_pending`, `index_one`) | APScheduler cada `INDEXER_CRON_MINUTES` · `POST /api/admin/run-indexer` · botón Reintentar |
| WF2 API de gestión | `app/services/document_service.py` + `auth_service.py` | Endpoints REST `/api/...` |
| WF3 Agente RAG | `app/services/rag_service.py` | `POST /api/search`, `POST /api/chat` |

- **Registro:** cada ejecución queda en la tabla `ejecuciones` (fecha, disparador, entrada, resultado, error, duración, n.º de reintentos). Consulta: `GET /api/admin/ejecuciones?flujo=&estado=ok|error` (solo admin).
- **Reintentos:** las llamadas a Gemini y al Storage se reintentan `RETRY_ATTEMPTS` veces con espera exponencial (`app/core/logging.py`, `con_reintentos`). Los errores de datos no se reintentan.
- **Errores controlados:** si un documento falla queda en estado `error` con el detalle; el barrido continúa con los demás. Los documentos en `error` no se reprocesan solos (para no agotar la cuota gratuita); se reprocesan con Reintentar.
- **Cron externo (opcional):** `POST /api/admin/run-indexer` con cabecera `X-Cron-Token: $CRON_SECRET`.

## IA gratuita con plan B y chatbot (Fase 2)
- **Capa única de IA** (`app/services/llm.py`): todas las tareas de IA (clasificar documentos, responder RAG, chatbot) pasan por `con_fallback`, que prueba los proveedores en el orden de `LLM_PROVIDERS`: **Gemini → Groq → Ollama**. Solo se usan los que tengan clave/URL.
- **Si todos fallan** (cuota agotada, sin red): la API responde **503** con un mensaje claro y la ejecución queda registrada con el error. Los documentos que no se pudieron clasificar quedan en `error` para reintentarlos.
- **Embeddings:** Gemini `gemini-embedding-001` (768 dims) con `google-genai`. No tienen fallback a otro proveedor porque vectores de modelos distintos no son comparables.
- **Chatbot** (`app/services/chatbot.py`): agente de LangChain/LangGraph (`create_agent`) con herramientas `buscar_documentos` (RAG), `listar_documentos` y `consultar_facturas`. Memoria persistente por usuario y sesión en la tabla `conversaciones` (últimos `CHAT_HISTORY_TURNS` mensajes).
- **Auditoría:** cada llamada a la IA se registra en `auditoria` (acción `ia:*`) con proveedor, modelo, proveedores que fallaron antes, herramientas usadas, entrada y salida recortadas y duración.
- **Endpoints:** `POST /api/chat` (chatbot), `GET|DELETE /api/chat/historial?sesion=`, `POST /api/rag/consultar` (WF3 original, sin memoria), `GET /health` (muestra los proveedores de IA activos).
- **Frontend:** `app/static/index.html` ya habla con la API (antes llamaba a los webhooks de n8n). Se abre en `http://localhost:8000/`.
- **Base de datos existente:** ejecutar `migrations/002_conversaciones.sql`.

## Despliegue
Dockerfile incluido. En Render: Web Service (Docker); el scheduler interno ejecuta la indexación. Si el servicio se duerme, un Cron Job puede llamar a `/api/admin/run-indexer` con `X-Cron-Token`. Supabase para DB + Storage.

## Pruebas
```bash
pytest -q
```
