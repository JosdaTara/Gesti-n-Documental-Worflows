# Gestión Documental con IA — Tercer Corte

Automatización propia en código (FastAPI) + módulo de visión (facturas), migrado desde n8n.

## Stack
Python 3.12 + FastAPI, PostgreSQL (Supabase) + pgvector, Supabase Storage, Gemini (gemini-3.8-flash) con plan B Groq y Ollama, LangChain/LangGraph, APScheduler, Tesseract, JWT + bcrypt. Despliegue en Render.

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

## Módulo de visión: lectura de facturas (Fase 3)
**Proceso de negocio:** el usuario fotografía una factura o recibo de compra (o sube el PDF) y el sistema extrae proveedor, NIT, fecha, ítems y total, y crea el registro en `facturas` (módulo de compras/gastos).

| Paso | Dónde |
|---|---|
| Captura: cámara del celular (`capture="environment"`), imagen o PDF desde la web | pestaña **Facturas** de `app/static/index.html` |
| Normalización: rotación EXIF, JPEG, máx. 2000 px; PDF digital → texto, PDF escaneado → imagen | `app/services/imagen_utils.py` |
| Protección de datos (Ley 1581): se ocultan correos, teléfonos y cédulas antes de usar IA externa | `app/services/privacidad.py` |
| Extracción con plan B: visión (Gemini → Groq → Ollama) → OCR Tesseract + LLM → registro manual | `app/services/vision_service.py` |
| Validación y confianza por campo: DV del NIT (DIAN), fecha, total vs. ítems (con IVA) | `app/services/validacion_factura.py` |

- **Confianza:** cada campo combina la certeza del modelo con reglas verificables. Si la confianza global es ≥ `CONFIDENCE_THRESHOLD` y ningún campo obligatorio baja de 0,5, la factura queda `validada`; si no, `por_confirmar` y el usuario la revisa y corrige antes de confirmarla (`confirmada`).
- **Privacidad (`VISION_PRIVACY_MODE`):** `local` solo usa Ollama/Tesseract; `anonimizado` (por defecto) tapa los datos personales en la imagen con Tesseract antes de enviarla y, si Tesseract no está, **no** la envía a servicios externos; `externo` la envía solo si el usuario marcó la autorización. El NIT del proveedor no se oculta (identifica a la empresa).
- **Control de acceso:** las imágenes se guardan privadas; solo quien la subió o un admin puede verla (`GET /api/vision/images/{id}`). Se audita `subir_imagen`, `ver_imagen`, `confirmar_factura` (con los campos que el usuario corrigió) y cada llamada `ia:vision_*`.
- **Medición:** cada factura guarda `datos_ia` (lo que leyó la IA); al confirmarla se registran las correcciones. Esto alimenta el % de aciertos por campo (Fase 5).
- **Endpoints:** `POST /api/vision/invoices` (archivo + `consentimiento`), `GET /api/vision/invoices?estado=`, `GET /api/vision/invoices/{id}`, `POST /api/vision/invoices/{id}/confirm`.
- **Base de datos existente:** ejecutar `migrations/003_vision_facturas.sql`.
- **Tesseract en local (Windows):** instalar desde https://github.com/UB-Mannheim/tesseract/wiki con el idioma español. En Docker/Render ya viene instalado.

## Integración y seguridad (Fase 4)
### La visión dispara la automatización
Cuando una factura queda **validada** (alta confianza) o un usuario la **confirma**, se ejecuta el flujo `procesar_factura` (`app/services/automatizacion_facturas.py`) sin pasos manuales:
1. Registra o actualiza el **proveedor** por NIT (tabla `proveedores`, con número de facturas y total comprado).
2. Abre **alertas** (tabla `alertas`) según reglas de control: `proveedor_nuevo`, `nit_invalido`, `sin_nit`, `posible_duplicado`, `monto_alto` (≥ `MONTO_APROBACION`) y `monto_atipico` (> `MONTO_ATIPICO_FACTOR` × promedio del proveedor).
3. Marca la factura como procesada (idempotente). Si el flujo falla, la factura no se pierde: el barrido periódico (`barrido_facturas`) la reintenta, o un admin con `POST /api/admin/run-invoices`.

### Chatbot como canal del módulo de visión
- Botón 📎 en el chat → `POST /api/chat/imagen`: lee la factura, dispara el flujo y responde con proveedor, total, confianza y alertas. Queda en la memoria de la conversación.
- Nuevas herramientas del agente: `consultar_alertas` y `resumen_compras` (total por proveedor y periodo). Cada rol solo ve lo que le corresponde.

### Roles y permisos
| Permiso | admin | operador | consulta |
|---|:-:|:-:|:-:|
| Ver documentos, buscar, usar el chatbot | ✔ | ✔ | ✔ |
| Subir documentos e imágenes, confirmar facturas propias | ✔ | ✔ | |
| Ver facturas | todas | propias | todas (solo lectura) |
| Ver imágenes originales | todas | propias | |
| Ver alertas | ✔ | ✔ | |
| Atender alertas, eliminar documentos, ejecutar flujos, ver ejecuciones y auditoría, gestionar usuarios | ✔ | | |

La matriz está en `app/core/security.py` (`PERMISOS`). El usuario se consulta en cada petición, así que un cambio de rol o una desactivación aplica de inmediato. El frontend pide `GET /api/auth/me` y oculta lo que el rol no puede hacer (el backend lo valida igual).

### Endurecimiento
- Se eliminó `POST /api/auth/bootstrap` (era público y creaba un admin con contraseña conocida).
- La contraseña inicial del admin sale de `ADMIN_PASSWORD`; en producción, sin ella no se crea el admin.
- Con `APP_ENV=production` la app no arranca si `JWT_SECRET` es el de ejemplo o mide menos de 32 caracteres, o si `ALLOWED_ORIGINS` es `*`.
- Usuarios desactivados no pueden iniciar sesión ni usar tokens ya emitidos. Cerrar sesión recarga la página para no dejar datos en memoria.
- Auditoría de administración: `crear_usuario`, `editar_usuario`, `atender_alerta` (consulta en `GET /api/admin/auditoria`).
- **Base de datos existente:** ejecutar `migrations/004_integracion_roles.sql` (convierte el rol heredado `usuario` en `operador`).

## Despliegue (Fase 6)
**Render** (Docker con Tesseract, plan gratuito) + **Supabase** (PostgreSQL + pgvector y Storage privado) + **GitHub Actions** como cron gratuito. Guía paso a paso, variables y checklist de verificación: [`docs/despliegue.md`](docs/despliegue.md).

- `render.yaml`: blueprint del servicio (New + → Blueprint). Los secretos se piden al crearlo; `JWT_SECRET` y `CRON_SECRET` los genera Render.
- Al arrancar, la app aplica `migrations/*.sql` pendientes (tabla `schema_migrations`, con candado para que no se apliquen dos veces) y crea el bucket privado.
- `/health` verifica la base de datos y muestra los proveedores de IA activos y el almacenamiento.
- En producción la app no arranca sin Postgres, sin Supabase Storage o con secretos por defecto.
- `docker compose up --build` levanta lo mismo en local (Postgres + pgvector + la app).
- Las pruebas también corren contra PostgreSQL real: `TEST_DATABASE_URL=postgresql+psycopg://… pytest` (borra esa base: use una solo para pruebas).

## Pruebas (Fase 5)
```bash
pytest -q                                  # 92 pruebas, sin internet ni claves (IA simulada)
pytest -q --cov=app --cov=scripts          # cobertura: 88 %
```
Detalle de qué cubre cada archivo, el plan B y los criterios de acierto: [`docs/pruebas.md`](docs/pruebas.md).

### Evaluación del módulo de visión con fotos reales (≥ 20)
```bash
python scripts/evaluar_vision.py plantilla evaluacion/fotos      # crea verdad.csv para llenar a mano
python scripts/evaluar_vision.py evaluar evaluacion/fotos --consentimiento
python scripts/evaluar_vision.py bd                              # aciertos según facturas confirmadas en la app
```
Genera `reporte.md` con % de aciertos por campo, calibración de la confianza y facturas que se habrían validado con error. La carpeta `evaluacion/` no se sube a Git (las fotos pueden tener datos personales).
