import pyodbc

from .config import settings


def get_db():
    """Dependency: satu koneksi per request, ditutup otomatis.

    pyodbc sudah melakukan connection pooling secara internal, jadi tidak
    perlu pool manual di sini.
    """
    conn = pyodbc.connect(settings.db_dsn)
    try:
        yield conn
    finally:
        conn.close()
