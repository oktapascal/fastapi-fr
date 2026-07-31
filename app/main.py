from contextlib import asynccontextmanager
from secrets import compare_digest

import pyodbc
from fastapi import Depends, FastAPI, Header, HTTPException

from . import face
from .config import settings
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Muat model dan cache vektor sekali di awal, bukan per-request."""
    face.load_models()
    conn = pyodbc.connect(settings.db_dsn)
    try:
        face.refresh_cache(conn)
    finally:
        conn.close()
    yield


def verify_key(x_api_key: str | None = Header(None)) -> None:
    """Shared secret dengan Lumen. compare_digest untuk hindari timing attack.

    Header sengaja opsional di level tipe agar header yang absen tetap dijawab
    401, bukan 422 dari validasi Pydantic — kegagalan autentikasi harus terlihat
    sebagai kegagalan autentikasi di log Lumen.
    """
    if x_api_key is None or not compare_digest(x_api_key, settings.api_key):
        raise HTTPException(401, "API key tidak valid")


app = FastAPI(title="Face Recognition API", lifespan=lifespan)
app.include_router(router, dependencies=[Depends(verify_key)])


@app.get("/health")
def health():
    """Tanpa auth — dipakai untuk cek liveness oleh proses supervisor."""
    return {
        "status": "ok",
        "faces_loaded": face.enrolled_count(),
        "device": str(face._device),
        "threshold": settings.match_threshold,
    }
