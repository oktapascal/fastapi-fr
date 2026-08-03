import pyodbc

from .config import settings


def connect(timeout: int = 5) -> pyodbc.Connection:
    """Buka koneksi baru ke SQL Server.

    timeout wajib: tanpa ini connect ke host yang tidak menjawab menggantung
    15-30 detik — cukup untuk membekukan startup dan menghabiskan worker lewat
    /health/db yang tanpa auth.
    """
    dsn = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={settings.db_host1},{settings.db_port1};"
        f"DATABASE={settings.db_name1};"
        f"UID={settings.db_user1};PWD={settings.db_password1};"
    )
    return pyodbc.connect(dsn, timeout=timeout)


def get_db():
    """Dependency: satu koneksi per request, ditutup otomatis.

    pyodbc sudah melakukan connection pooling secara internal, jadi tidak
    perlu pool manual di sini.
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
