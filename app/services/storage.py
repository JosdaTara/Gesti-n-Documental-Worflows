import os, hashlib, uuid
from app.core.config import settings

def _local_dir():
    d = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
    os.makedirs(d, exist_ok=True)
    return d

_cliente = None


def _sb():
    """Cliente de Supabase reutilizado (crear uno por petición es lento)."""
    global _cliente
    if _cliente is None:
        from supabase import create_client
        _cliente = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return _cliente


def usa_supabase() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


def asegurar_bucket() -> None:
    """Crea el bucket privado si no existe (los archivos solo se entregan con URL firmada)."""
    if not usa_supabase():
        return
    import logging
    log = logging.getLogger("motor.storage")
    try:
        _sb().storage.get_bucket(settings.SUPABASE_BUCKET)
    except Exception:
        try:
            _sb().storage.create_bucket(settings.SUPABASE_BUCKET, options={"public": False})
            log.info("Bucket privado creado: %s", settings.SUPABASE_BUCKET)
        except Exception as e:
            log.error("No se pudo verificar ni crear el bucket %s: %s", settings.SUPABASE_BUCKET, e)


def save_bytes(data: bytes, mime: str, prefix: str = "doc") -> tuple[str, str]:
    h = hashlib.sha256(data).hexdigest()
    key = f"{prefix}/{uuid.uuid4()}-{h[:8]}.bin"
    if usa_supabase():
        sb = _sb()
        sb.storage.from_(settings.SUPABASE_BUCKET).upload(key, data, {"content-type": mime})
    else:
        path = os.path.join(_local_dir(), key.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return key, h

def read_bytes(key: str) -> bytes:
    if usa_supabase():
        sb = _sb()
        return sb.storage.from_(settings.SUPABASE_BUCKET).download(key)
    path = os.path.join(_local_dir(), key.replace("/", os.sep))
    with open(path, "rb") as f:
        return f.read()

def signed_url(key: str) -> str:
    if usa_supabase():
        sb = _sb()
        r = sb.storage.from_(settings.SUPABASE_BUCKET).create_signed_url(key, 3600)
        return r.get("signedURL") or r.get("signedUrl") or ""
    return f"/files/{key}"
