from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Konfigurasi dibaca dari file .env."""

    # Connection string ODBC, contoh:
    # DRIVER={ODBC Driver 17 for SQL Server};SERVER=host,port;DATABASE=dbproduk;UID=user;PWD=pass
    db_dsn: str

    # Shared secret dengan Lumen (core API)
    api_key: str

    # Ambang COSINE SIMILARITY (makin besar makin mirip, cocok bila score >= nilai ini).
    #
    # PERHATIAN: implementasi referensi (workspace-face-fr) memakai EUCLIDEAN
    # DISTANCE dengan ambang 0.83 dan arah perbandingan terbalik (cocok bila
    # distance < 0.83). Jangan menyalin angka 0.83 ke sini.
    # Karena embedding ternormalisasi, konversinya persis: cos = 1 - d^2 / 2,
    # sehingga distance < 0.83 setara dengan cosine > 0.6556 (nilai default di bawah).
    match_threshold: float = 0.6556

    # Batas ukuran upload; FastAPI tidak membatasi apa pun secara default.
    max_upload_bytes: int = 5 * 1024 * 1024

    model_config = {"env_file": ".env"}


settings = Settings()
