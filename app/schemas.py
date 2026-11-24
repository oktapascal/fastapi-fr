from typing import Literal

from pydantic import BaseModel


class EnrollResponse(BaseModel):
    nik: str
    kode_lokasi: str


class VerifyResponse(BaseModel):
    """Verifikasi 1:1. Lumen sudah tahu NIK-nya, jadi cukup kirim hasilnya.

    `reason` hanya terisi saat matched=False, agar Lumen bisa membedakan wajah
    tidak cocok (orang lain) dari NIK yang belum terdaftar.
    """

    matched: bool
    score: float | None = None
    reason: Literal["face_mismatch", "not_enrolled"] | None = None
    # Hanya terisi oleh /identify saat matched=True. Pada /verify selalu None
    # (Lumen sudah tahu NIK-nya), dan saat gagal cocok juga None — mengembalikan
    # kandidat terdekat pada wajah asing akan menjadikan endpoint ini oracle.
    nik: str | None = None
