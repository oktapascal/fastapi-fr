import pyodbc

from .config import settings


class Database:
    """Pembangun koneksi SQL Server.

    Bentuknya mengikuti pola fastapi-simkug/fastapi-sima supaya konfigurasi
    antar proyek seragam. Getter privat (`_get_host` dsb.) sengaja tidak
    disalin — metode yang hanya mengembalikan atributnya sendiri tidak
    menambah apa pun di atas `self.host`.
    """

    def __init__(
        self,
        username: str = "test",
        password: str = "test",
        host: str = "test",
        database: str = "test",
        port: str = "8282",
    ) -> None:
        self.username = username
        self.password = password
        self.host = host
        self.database = database
        self.port = port

    def connect(self, timeout: int = 5) -> pyodbc.Connection:
        """timeout wajib: tanpa ini connect ke host mati menggantung 15-30 detik,
        cukup lama untuk menggantung startup dan menghabiskan worker.
        """
        config = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            f"SERVER={self.host},{self.port};DATABASE={self.database};"
            f"UID={self.username};PWD={self.password};"
        )
        return pyodbc.connect(config, timeout=timeout)


# Satu instance dari .env, dipakai get_db() dan health check.
db = Database(
    username=settings.db_user1,
    password=settings.db_password1,
    host=settings.db_host1,
    database=settings.db_name1,
    port=settings.db_port1,
)


def get_db():
    """Dependency: satu koneksi per request, ditutup otomatis.

    pyodbc sudah melakukan connection pooling secara internal, jadi tidak
    perlu pool manual di sini.
    """
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()
