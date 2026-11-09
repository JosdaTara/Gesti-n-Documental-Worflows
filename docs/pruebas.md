# Plan y resultados de pruebas — Tercer corte

La guía pide pruebas unitarias de los servicios de automatización, pruebas del módulo de visión con al menos 20 imágenes reales con resultados medidos, y un plan B demostrable cuando el modelo gratuito falla. Este documento explica cómo se cubre cada punto y cómo repetir las pruebas.

## 1. Pruebas automatizadas (pytest)

```bash
pytest -q                                   # 103 pruebas (99 en SQLite + 4 solo para PostgreSQL)
pytest -q --cov=app --cov=scripts           # con cobertura
```

Corren sin internet y sin claves: la base de datos es SQLite temporal, y la IA y Tesseract se sustituyen por dobles de prueba (modelos falsos de LangChain). Así se prueban también los casos de falla, que con servicios reales no se pueden provocar a voluntad.

| Archivo | Qué prueba | Pruebas |
|---|---|--:|
| `tests/test_services.py` | Contraseñas (bcrypt) y login | 3 |
| `tests/test_motor.py` | Motor que reemplaza a n8n: registro de cada ejecución (entrada, resultado, error, duración), reintentos con espera exponencial, errores que no se reintentan (datos y 400/401/403/404), flujo WF1 de indexación (éxito, falla y barrido), permisos de los endpoints de administración | 13 |
| `tests/test_flujos_gestion.py` | WF2 (subir, listar, descargar, crear repositorio, reintentar, eliminar) y WF3 (RAG con fuentes, sin fragmentos, sin IA → 503); tarea programada que sigue aunque un barrido falle | 9 |
| `tests/test_ia.py` | Plan B Gemini → Groq → Ollama, todos caídos, sin configuración, auditoría de la IA, chatbot LangGraph (herramientas, memoria, historial, 503 claro) | 10 |
| `tests/test_vision.py` | Dígito de verificación del NIT (DIAN), normalización de números y fechas, confianza por campo, anonimización (texto e imagen), fotos rotadas del celular, cascada visión → OCR → manual, modos de privacidad, API con control de acceso | 28 |
| `tests/test_integracion.py` | Visión → `procesar_factura` → proveedores y alertas (las 6 reglas), cola de reintento, idempotencia, roles admin/operador/consulta, cambios de rol inmediatos, foto por el chatbot, endurecimiento de producción | 15 |
| `tests/test_evaluacion.py` | Script de medición de visión: comparación de campos, reporte completo y medición desde facturas confirmadas | 14 |
| **Total** | | **92** |

**Cobertura de código:** 88 % de `app/` y `scripts/`. Los servicios de automatización están entre 84 % y 100 %:

| Módulo | Cobertura |
|---|--:|
| `core/logging.py` (motor: registro y reintentos) | 98 % |
| `services/document_service.py` (WF2) | 96 % |
| `services/automatizacion_facturas.py` (procesar_factura) | 95 % |
| `services/vision_service.py` | 91 % |
| `services/validacion_factura.py` | 91 % |
| `services/indexing_service.py` (WF1) | 84 % |
| `services/rag_service.py` (WF3) | 80 % * |
| `workers/scheduler.py` | 100 % |

\* La consulta vectorial de pgvector (`<=>`) no existe en SQLite. Se prueba la lógica alrededor de ella, y la consulta misma se verifica sobre Supabase en el despliegue.

## 2. Plan B ante fallos del modelo gratuito

| Situación | Qué hace el sistema | Prueba |
|---|---|---|
| Gemini agota la cuota (429) o se cae | Reintenta y pasa a Groq, luego a Ollama | `test_plan_b_usa_groq_si_gemini_falla` |
| Ningún modelo responde | HTTP 503 con un mensaje claro; queda registrado en `ejecuciones` | `test_chat_responde_503_claro_si_no_hay_ia`, `test_rag_sin_ia_responde_503` |
| Error permanente (clave inválida, modelo retirado: 400/401/403/404) | No se reintenta: pasa de inmediato al siguiente proveedor | `logging._es_reintentable` |
| Falla la visión | OCR con Tesseract + modelo de texto; si tampoco, la factura queda para diligenciar a mano | `test_plan_b_ocr_con_texto_enmascarado`, `test_sin_tesseract_...` |
| Falla la indexación de un documento | Queda en `error` con el detalle; el barrido sigue con los demás; se reintenta con el botón | `test_indexar_documento_falla_marca_error`, `test_barrido_...` |
| Falla `procesar_factura` | La factura no se pierde; el barrido periódico la reintenta | `test_flujo_idempotente_y_cola_de_reintento` |

**Caso real encontrado en la verificación:** el modelo `gemini-2.5-flash` respondió 404 ("no longer available to new users"). El sistema no se cayó: la factura pasó a revisión manual. Se actualizó el modelo por defecto a `gemini-3.8-flash` y se dejó de reintentar este tipo de error.

## 3. Evaluación del módulo de visión con fotos reales

### Cómo tomar las fotos
- Mínimo 20 facturas o recibos **reales**, fotografiados con celular. No sirven imágenes descargadas de internet.
- Conviene variedad: facturas electrónicas impresas, tiquetes de caja, facturas a mano, con buena y mala luz, algo inclinadas, papel arrugado. Una evaluación solo con fotos perfectas no es honesta.
- Si una factura tiene datos personales de personas naturales (cédula, teléfono, correo), el sistema los oculta antes de enviarla a la IA externa (modo `anonimizado`, que requiere Tesseract) o se usa el modo `local`. Las fotos **no se suben a Git**: `evaluacion/` está en `.gitignore`.

### Procedimiento
```bash
# 1. Copiar las fotos a evaluacion/fotos/ y crear la plantilla de respuestas correctas
python scripts/evaluar_vision.py plantilla evaluacion/fotos
# 2. Llenar evaluacion/fotos/verdad.csv (se abre en Excel): proveedor, nit, fecha (AAAA-MM-DD), total
# 3. Ejecutar (pausa de 4 s entre imágenes para respetar el límite por minuto del nivel gratuito)
python scripts/evaluar_vision.py evaluar evaluacion/fotos --consentimiento
# 4. Resultado: evaluacion/fotos/resultados/reporte.md y resultados.csv
```

### Qué mide el reporte
- **% de aciertos por campo** (proveedor, NIT, fecha, total) y global. Esto es lo que pide la guía.
- **Facturas con todos los campos correctos.**
- **Calibración de la confianza:** % de acierto de los campos con confianza ≥ umbral frente a los de confianza menor. Si la confianza sirve, los primeros aciertan mucho más que los segundos. Con esto se justifica el umbral.
- **Validadas con algún error:** facturas que se habrían registrado solas estando mal. Es el riesgo real del sistema y la respuesta a la pregunta "¿qué pasa cuando la IA se equivoca?".
- Método usado por imagen (visión, OCR o manual) y tiempo promedio.

**Criterios de acierto:** el proveedor se compara sin tildes, mayúsculas ni sufijos societarios (S.A.S., LTDA) y con una similitud mínima de 85 %. El NIT se normaliza; si la respuesta correcta no trae dígito de verificación, se compara solo la base. La fecha debe coincidir exactamente. El total debe coincidir al peso (±1 por redondeo).

### Medición continua con uso real
```bash
python scripts/evaluar_vision.py bd
```
Compara lo que leyó la IA (`datos_ia`) con lo que cada usuario confirmó al revisar las facturas en la app.

### Resultados
> Pendiente: ejecutar con las 20 fotos reales del grupo y pegar aquí la tabla de `reporte.md`.

Verificación de integración (no cuenta como evaluación): una factura **ficticia** generada por computador, procesada con Gemini real (`gemini-3.8-flash`). Leyó bien los 4 campos (proveedor, NIT, fecha y total de $65.450, que incluye IVA), con confianza 1,0 y en 11,9 s, y quedó validada automáticamente.

## 4. Pruebas contra PostgreSQL + pgvector (Fase 6)

SQLite no detecta diferencias con la base de producción, así que la suite completa también se corre contra PostgreSQL 16 + pgvector (la misma que usa Supabase), en un contenedor:

```bash
docker run -d --name gd-pg-test -e POSTGRES_PASSWORD=pruebas -e POSTGRES_DB=gd_test -p 55432:5432 pgvector/pgvector:pg16
TEST_DATABASE_URL=postgresql+psycopg://postgres:pruebas@localhost:55432/gd_test pytest -q    # 103 passed
```

`tests/test_postgres.py` agrega pruebas que solo existen ahí: migraciones aplicadas e idempotentes, columna `vector(768)`, índice HNSW, búsqueda vectorial real con `<=>` y totales `NUMERIC`.

**Errores reales que encontró esta prueba (y que SQLite no mostraba):**

| Error | Efecto en producción | Corrección |
|---|---|---|
| El modelo llamaba `meta` a la columna que en la BD se llama `metadata` | Fallaba la indexación de **todos** los documentos | Atributo `meta` mapeado a la columna `metadata` |
| Índice `ivfflat` creado sobre la tabla vacía (lists=100, probes=1) | La búsqueda devolvía vacío para preguntas válidas | Migración `005`: índice HNSW |
| El ejecutor de migraciones abría la transacción dos veces | La app no arrancaba sobre Postgres | Una transacción por migración |

**Prueba de punta a punta del contenedor de producción** (misma imagen Docker que Render, contra PostgreSQL, con Gemini y Tesseract reales):
- Las 5 migraciones se aplicaron solas sobre la base vacía; al reiniciar no se repitieron. Memoria: 106 MB en reposo y 152 MB en uso (el límite gratuito de Render es 512 MB).
- Factura ficticia con datos personales ficticios: Tesseract tapó cédula, teléfono y correo. El NIT y los montos quedaron legibles, Gemini leyó los 4 campos y el flujo de compras abrió la alerta de proveedor nuevo.
  - Hallazgo: el OCR leía la "@" como "lO" o "(O" y **el correo se filtraba**. Se amplió el patrón y se agregaron pruebas de regresión.
  - Limitación conocida: el nombre de la persona no se oculta (requeriría un modelo de reconocimiento de entidades).
- Gemini `gemini-3.8-flash` respondió 503 *"high demand"* y 504 durante la prueba. Con el plan B, la clasificación y el chatbot respondieron con `gemini-3.5-flash-lite`: el chatbot contestó citando `politica_vacaciones.txt` en 13,5 s.
  - Si ni siquiera clasificar es posible, el documento igual queda buscable (degradación controlada).
