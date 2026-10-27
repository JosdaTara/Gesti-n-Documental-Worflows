import os, hashlib, uuid
from app.core.config import settings

def _local_dir():
    d = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
    os.makedirs(d, exist_ok=True)
    return d

def save_bytes(data: bytes, mime: str, prefix: str = "doc") -> tuple[str, str]:
    h = hashlib.sha256(data).hexdigest()
    key = f"{prefix}/{uuid.uuid4()}-{h[:8]}.bin"
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY:
        from supabase import create_client
        sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        sb.storage.from_(settings.SUPABASE_BUCKET).upload(key, data, {"content-type": mime})
    else:
        path = os.path.join(_local_dir(), key.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return key, h

def read_bytes(key: str) -> bytes:
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY:
        from supabase import create_client
        sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        return sb.storage.from_(settings.SUPABASE_BUCKET).download(key)
    path = os.path.join(_local_dir(), key.replace("/", os.sep))
    with open(path, "rb") as f:
        return f.read()

def signed_url(key: str) -> str:
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY:
        from supabase import create_client
        sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        r = sb.storage.from_(settings.SUPABASE_BUCKET).create_signed_url(key, 3600)
        return r.get("signedURL") or r.get("signedUrl") or ""
    return f"/files/{key}"
