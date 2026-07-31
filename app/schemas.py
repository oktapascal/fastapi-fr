from typing import Literal

from pydantic import BaseModel

# Divalidasi FastAPI/Pydantic secara otomatis -> 422 bila di luar daftar,
# tidak perlu pengecekan manual di endpoint.
Challenge = Literal["smile", "look_left", "look_right"]


class EnrollResponse(BaseModel):
    nik: str
    kode_lokasi: str


class VerifyResponse(BaseModel):
    """Verifikasi 1:1. Lumen sudah tahu NIK-nya, jadi cukup kirim hasilnya.

    `reason` hanya terisi saat matched=False, agar Lumen bisa membedakan
    liveness gagal (kemungkinan spoofing) dari wajah tidak cocok (orang lain).
    """

    matched: bool
    score: float | None = None
    reason: Literal["liveness_failed", "face_mismatch", "not_enrolled"] | None = None
