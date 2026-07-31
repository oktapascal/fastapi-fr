import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from . import face
from .config import settings
from .db import get_db
from .schemas import Challenge, EnrollResponse, VerifyResponse

router = APIRouter(prefix="/api/face", tags=["face"])


def _read_image(file: UploadFile) -> Image.Image:
    """Validasi ukuran & format upload, lalu konversi jadi gambar RGB."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "File harus berupa gambar")

    data = file.file.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(400, "File gambar kosong")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Ukuran gambar melebihi batas")

    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except UnidentifiedImageError:
        raise HTTPException(400, "Format gambar tidak valid")


@router.post("/enroll", response_model=EnrollResponse)
def enroll(
    nik: str = Form(...),
    kode_lokasi: str = Form(...),
    file: UploadFile = File(...),
    conn=Depends(get_db),
):
    """Daftarkan wajah untuk satu NIK.

    Tanpa liveness — sama seperti /register di implementasi referensi. Enroll
    dilakukan petugas secara terawasi, jadi risikonya berbeda dari verifikasi.

    Validasi bahwa NIK ada di master karyawan dilakukan oleh Lumen — service ini
    sengaja tidak menyentuh tabel hr_karyawan sama sekali.
    """
    try:
        vec = face.embed(_read_image(file))
    except ValueError as e:
        raise HTTPException(422, str(e))

    payload = json.dumps(vec.tolist())
    now = datetime.now()

    # Upsert: constraint IX_karyawan_vector membatasi satu wajah per NIK.
    # UPDATE-lalu-cek-rowcount dipilih ketimbang MERGE karena lebih mudah dibaca
    # (pola yang sama dipakai referensi di database_sqlserver.save_user).
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE karyawan_vector SET face_vector = ?, kode_lokasi = ?, updated_at = ? "
        "WHERE nik_karyawan = ?",
        payload,
        kode_lokasi,
        now,
        nik,
    )
    if cursor.rowcount == 0:
        cursor.execute(
            "INSERT INTO karyawan_vector "
            "(nik_karyawan, kode_lokasi, face_vector, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            nik,
            kode_lokasi,
            payload,
            now,
            now,
        )
    conn.commit()

    face.refresh_cache(conn)  # sinkronkan cache di memori dengan DB
    return EnrollResponse(nik=nik, kode_lokasi=kode_lokasi)


@router.post("/verify", response_model=VerifyResponse)
def verify(
    nik: str = Form(...),
    challenge: Challenge = Form(...),
    file_a: UploadFile = File(..., description="Frame netral"),
    file_b: UploadFile = File(..., description="Frame setelah gerakan challenge"),
):
    """Verifikasi 1:1 wajah terhadap NIK, dengan liveness check dua frame.

    Tidak menyentuh DB — seluruh vektor sudah ada di memori. Pencatatan absensi
    adalah urusan Lumen.
    """
    image_a = _read_image(file_a)
    image_b = _read_image(file_b)

    # Cek keterdaftaran lebih dulu: percuma menjalankan model kalau NIK-nya
    # memang belum punya wajah tersimpan.
    if not face.is_enrolled(nik):
        return VerifyResponse(matched=False, reason="not_enrolled")

    try:
        landmarks_a = face.landmarks_of(image_a)
        landmarks_b = face.landmarks_of(image_b)
    except ValueError as e:
        raise HTTPException(422, str(e))

    if not face.check_liveness(landmarks_a, landmarks_b, challenge):
        return VerifyResponse(matched=False, reason="liveness_failed")

    try:
        vec = face.embed(image_a)  # embedding diambil dari frame netral
    except ValueError as e:
        raise HTTPException(422, str(e))

    score = face.similarity(nik, vec)
    if score is None:
        return VerifyResponse(matched=False, reason="not_enrolled")

    if score < settings.match_threshold:
        return VerifyResponse(matched=False, score=round(score, 4), reason="face_mismatch")

    return VerifyResponse(matched=True, score=round(score, 4))


@router.delete("/{nik}")
def delete_face(nik: str, conn=Depends(get_db)):
    """Hapus wajah terdaftar milik satu NIK."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM karyawan_vector WHERE nik_karyawan = ?", nik)
    if cursor.rowcount == 0:
        raise HTTPException(404, "Wajah untuk NIK tersebut tidak ditemukan")
    conn.commit()

    face.refresh_cache(conn)
    return {"nik": nik, "deleted": True}
