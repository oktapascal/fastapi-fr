import pyodbc
from fastapi import Depends, Header, HTTPException

from .config import settings


def get_kode_lokasi(x_kode_lokasi: str | None = Header(None)) -> str:
    """Validasi lokasi request sebelum memilih database."""
    if not x_kode_lokasi or x_kode_lokasi not in settings.db_location_mapping:
        raise HTTPException(400, "X-Kode-Lokasi tidak valid atau tidak tersedia")
    return x_kode_lokasi


def connect(kode_lokasi: str, timeout: int = 5) -> pyodbc.Connection:
    """Buka koneksi baru ke SQL Server.

    timeout wajib: tanpa ini connect ke host yang tidak menjawab menggantung
    15-30 detik — cukup untuk membekukan startup dan menghabiskan worker lewat
    /health/db yang tanpa auth.
    """
    dsn = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={settings.db_host1},{settings.db_port1};"
        f"DATABASE={settings.db_location_mapping[kode_lokasi]};"
        f"UID={settings.db_user1};PWD={settings.db_password1};"
    )
    return pyodbc.connect(dsn, timeout=timeout)


def get_db(kode_lokasi: str = Depends(get_kode_lokasi)):
    """Dependency: satu koneksi per request, ditutup otomatis.

    pyodbc sudah melakukan connection pooling secara internal, jadi tidak
    perlu pool manual di sini.
    """
    conn = connect(kode_lokasi)
    try:
        yield conn
    finally:
        conn.close()
