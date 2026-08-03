import logging
from contextlib import asynccontextmanager
from secrets import compare_digest

import pyodbc
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from . import face
from .config import settings
from .db import db
from .routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Muat model dan cache vektor sekali di awal, bukan per-request.

    Kegagalan DB tidak mematikan proses. Service tetap naik dengan cache kosong
    supaya /health/db bisa dipakai mendiagnosis penyebabnya, dan supaya PM2
    tidak masuk restart-loop selama DB bermasalah. Konsekuensinya semua
    verifikasi menjawab not_enrolled sampai cache terisi — persis perilaku yang
    sudah didokumentasikan (`faces_loaded: 0` saat koneksi DB gagal).
    """
    face.load_models()
    try:
        conn = db.connect()
        try:
            face.refresh_cache(conn)
        finally:
            conn.close()
    except pyodbc.Error:
        logger.exception("Cache vektor gagal dimuat; service naik dengan cache kosong")
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


@app.get("/health/db")
def health_db():
    """Tanpa auth — diagnostik koneksi DB saat setup server baru.

    Sengaja dipisah dari /health. /health dipanggil supervisor tiap beberapa
    detik dan harus tetap murah; endpoint ini membuka koneksi sungguhan, jadi
    jangan dijadikan target liveness check.

    Respons hanya memuat SQLSTATE, bukan pesan driver lengkap: pesan itu
    mengandung IP server dan versi driver, sementara endpoint ini publik.
    Detail lengkap masuk log (`pm2 logs`).
    """
    try:
        conn = db.connect()
        try:
            # Bukan sekadar connect: pastikan sesi benar-benar bisa dipakai.
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"connected": True}
    except pyodbc.Error as exc:
        # args[0] = SQLSTATE, mis. '08001' (gagal konek/TLS), '28000' (kredensial).
        sqlstate = exc.args[0] if exc.args else None
        logger.exception("Cek koneksi DB gagal (sqlstate=%s)", sqlstate)
        return JSONResponse(
            status_code=503,
            content={"connected": False, "sqlstate": sqlstate},
        )
