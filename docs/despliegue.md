# Guía de despliegue — Render + Supabase (todo gratuito)

Arquitectura: **Supabase** guarda la base de datos (PostgreSQL + pgvector) y los archivos (Storage privado); **Render** corre la app (Docker con Tesseract); **GitHub Actions** hace de cron gratuito. No se necesita tarjeta de crédito.

```
Navegador / celular ──HTTPS──▶ Render (FastAPI + Tesseract) ──▶ Supabase Postgres + pgvector
                                     │                      └──▶ Supabase Storage (privado)
GitHub Actions (cada 14 min) ───────┘──▶ Gemini (gemini-3.8-flash → gemini-3.5-flash-lite) → Groq
```

Tiempo estimado: 30 minutos. Al terminar quedará una URL tipo `https://gestion-documental-ia.onrender.com`.

---

## 1. Supabase: base de datos y archivos

1. Entre a https://supabase.com, inicie sesión con GitHub → **New project**.
   - Nombre: `gestion-documental` · Región: **East US (North Virginia)** (la más cercana a Render Oregon/Virginia) · Genere y **guarde** la contraseña de la base de datos.
2. Cuando el proyecto esté listo, copie tres datos:
   | Dato | Dónde | Variable en Render |
   |---|---|---|
   | URL del proyecto | Project Settings → API (o Data API) → *Project URL* | `SUPABASE_URL` |
   | Clave secreta | Project Settings → API Keys → *service_role* / *secret key* (**no** la `anon`/publishable) | `SUPABASE_SERVICE_KEY` |
   | Cadena de conexión | botón **Connect** (arriba) → *Connection string* → **Transaction pooler** (puerto **6543**) | `DATABASE_URL` |
3. Ajuste la cadena de conexión:
   - Cambie el inicio `postgresql://` por **`postgresql+psycopg://`**
   - Reemplace `[YOUR-PASSWORD]` por la contraseña del paso 1.
   - Debe quedar así: `postgresql+psycopg://postgres.abcdefgh:SuClave@aws-0-us-east-1.pooler.supabase.com:6543/postgres`

> **Por qué el pooler:** la conexión directa de Supabase (`db.xxx.supabase.co:5432`) solo funciona por IPv6 y Render no tiene salida IPv6; el *Transaction pooler* sí funciona por IPv4. La app ya viene configurada para el pooler (sin sentencias preparadas, `app/core/db.py`).

No hay que crear tablas ni el bucket a mano: al arrancar, la app aplica `migrations/*.sql` (incluye `CREATE EXTENSION vector`) y crea el bucket privado `documentos`.

## 2. Render: el servidor

1. Entre a https://render.com, inicie sesión con GitHub y autorice el acceso al repositorio `Gesti-n-Documental-Worflows`.
2. **New + → Blueprint** → elija el repositorio → Render lee `render.yaml` y propone el servicio `gestion-documental-ia` (plan **Free**).
3. Render pide las variables marcadas como secretas:
   | Variable | Valor |
   |---|---|
   | `DATABASE_URL` | la cadena ajustada del paso 1.3 |
   | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | del paso 1.2 |
   | `GEMINI_API_KEY` | su clave de https://aistudio.google.com/apikey |
   | `GROQ_API_KEY` | opcional pero recomendado (plan B): https://console.groq.com/keys |
   | `ADMIN_PASSWORD` | contraseña del usuario `admin` (mínimo 10 caracteres) |
   | `ALLOWED_ORIGINS` | `https://gestion-documental-ia.onrender.com` (la URL del servicio, sin `/` final; si Render le asigna otra, corríjala después en *Environment*) |

   `JWT_SECRET` y `CRON_SECRET` los genera Render solo.
4. **Apply**. El primer despliegue tarda unos 5–8 minutos (construye la imagen con Tesseract). En *Logs* debe ver:
   ```
   Migración aplicada: 001_schema.sql … 005_indice_hnsw.sql
   Application startup complete.
   ```
5. Verifique `https://<su-servicio>.onrender.com/health` → `{"ok":true,"bd":"ok","ia":["gemini","gemini_lite",...],"almacenamiento":"supabase"}`.
   Si `almacenamiento` dice `local` o la app no arranca, revise las variables: en producción la app **se niega a arrancar** con secretos por defecto, sin Postgres o sin Supabase, y el log dice qué falta.

## 3. Cron gratuito (GitHub Actions)

El plan gratuito de Render duerme el servicio tras 15 minutos sin visitas; dormido, el scheduler interno no corre. `.github/workflows/cron.yml` lo despierta cada 14 minutos (lunes a sábado, 6:00–19:00 hora Colombia, para no pasar de las 750 horas gratis al mes) y dispara los barridos de indexación y de facturas.

1. En Render → servicio → **Environment**, copie el valor de `CRON_SECRET`.
2. En GitHub → repositorio → **Settings → Secrets and variables → Actions → New repository secret**:
   - `APP_URL` = `https://<su-servicio>.onrender.com`
   - `CRON_SECRET` = el valor copiado
3. Pestaña **Actions** → *Barridos programados* → **Run workflow** para probarlo. Debe terminar en verde.
   (Para pausarlo: Settings → Variables → `CRON_ACTIVO` = `false`.)

## 4. Usuarios de prueba (para la entrega)

Entre con `admin` y la `ADMIN_PASSWORD`, pestaña **Usuarios**, y cree uno por rol: `operador_demo` (operador) y `consulta_demo` (consulta). Esas son las "credenciales de prueba" que pide la guía.

## 5. Checklist de verificación en producción

- [ ] `/health` responde `ok` con `almacenamiento: supabase`.
- [ ] Iniciar sesión como admin, operador y consulta: cada uno ve solo lo suyo.
- [ ] Subir un PDF/DOCX → a los pocos minutos queda `procesado` con categoría y resumen.
- [ ] Buscar y preguntar al chatbot sobre ese documento → responde citando el archivo.
- [ ] Desde el celular, pestaña Facturas → **Tomar foto** → la factura se lee y aparece la alerta de proveedor nuevo.
- [ ] Enviar una foto por el chat (📎) → el chatbot responde con el resultado.
- [ ] `GET /api/admin/ejecuciones` muestra las ejecuciones; Actions corre el cron en verde.
- [ ] Redesplegar (Manual Deploy) y comprobar que los documentos siguen ahí (prueba de que el almacenamiento es permanente).

## Probar lo mismo en local (opcional)

```bash
docker compose up --build        # Postgres + pgvector + la app con Tesseract en http://localhost:8000
```
Lee las claves de IA de `.env`. Es el mismo contenedor que corre en Render.

## Problemas conocidos

| Síntoma | Causa | Solución |
|---|---|---|
| La primera visita tarda ~1 minuto | El plan gratuito de Render "despierta" el servicio | Normal; el cron lo mantiene despierto en horario laboral |
| `Configuración insegura para producción: …` en el log | Falta una variable o tiene el valor de ejemplo | Corregir en *Environment*; el mensaje dice cuál |
| `connection refused` / `Network is unreachable` a Supabase | Se usó la conexión directa (IPv6) | Usar la del **Transaction pooler** (6543) |
| `prepared statement … already exists` | Pooler en modo transacción | Ya resuelto en `app/core/db.py` (`prepare_threshold=None`) |
| La IA responde 503 "no está disponible" | Gemini saturado ("high demand") | El sistema pasa a `gemini-3.5-flash-lite` y luego a Groq; con `GROQ_API_KEY` el plan B es más robusto |
| Ollama | No cabe en el plan gratuito de Render | Solo para uso local (`OLLAMA_BASE_URL`) |
