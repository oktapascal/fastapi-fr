import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from . import face
from .config import settings
from .db import get_db, get_kode_lokasi, get_db_form, get_kode_lokasi_form
from .schemas import Challenge, EnrollResponse, VerifyResponse

router = APIRouter(prefix="/api/face", tags=["face"])
logger = logging.getLogger(__name__)


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
        # Foto kamera HP sering disimpan miring dengan tag EXIF Orientation;
        # PIL tidak memutarnya otomatis, padahal MTCNN gagal pada wajah miring 90°.
        return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except UnidentifiedImageError:
        raise HTTPException(400, "Format gambar tidak valid")


def _image_stats(image: Image.Image) -> str:
    """Ringkasan frame untuk log: ukuran, kecerahan (0-255), ketajaman tepi.

    Frame hitam/gelap -> terang rendah; blur karena gerakan -> tajam jauh lebih
    rendah dibanding frame netral pada request yang sama.
    """
    gray = image.convert("L")
    terang = ImageStat.Stat(gray).mean[0]
    tajam = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    return f"{image.width}x{image.height} terang={terang:.0f} tajam={tajam:.1f}"


def _embed_after_liveness(image_a, image_b, challenge: str):
    """Cek liveness dua frame, lalu embed frame netral. None = liveness gagal.

    Raises:
        HTTPException: 422 bila wajah tidak terdeteksi di salah satu frame.
    """
    try:
        landmarks_a = face.landmarks_of(image_a, "netral")
        landmarks_b = face.landmarks_of(image_b, "gerakan")
        if not face.check_liveness(landmarks_a, landmarks_b, challenge):
            return None
        return face.embed(image_a)  # embedding diambil dari frame netral
    except ValueError as e:
        # Foto tidak disimpan (data biometrik); statistik ini cukup untuk membedakan
        # frame gelap, blur, atau kepala yang menoleh terlalu jauh.
        logger.warning(
            "%s challenge=%s | netral: %s | gerakan: %s",
            e, challenge, _image_stats(image_a), _image_stats(image_b),
        )
        raise HTTPException(422, str(e))


@router.post("/enroll", response_model=EnrollResponse)
def enroll(
    nik: str = Form(...),
    kode_lokasi: str = Depends(get_kode_lokasi_form),
    file: UploadFile = File(...),
    conn=Depends(get_db_form),
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

    face.refresh_cache(conn, kode_lokasi)  # sinkronkan cache lokasi dengan DB
    return EnrollResponse(nik=nik, kode_lokasi=kode_lokasi)


@router.post("/verify", response_model=VerifyResponse)
def verify(
    nik: str = Form(...),
    kode_lokasi: str = Depends(get_kode_lokasi_form),
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
    if not face.is_enrolled(kode_lokasi, nik):
        return VerifyResponse(matched=False, reason="not_enrolled")

    vec = _embed_after_liveness(image_a, image_b, challenge)
    if vec is None:
        return VerifyResponse(matched=False, reason="liveness_failed")

    score = face.similarity(kode_lokasi, nik, vec)
    if score is None:
        return VerifyResponse(matched=False, reason="not_enrolled")

    if score < settings.match_threshold:
        return VerifyResponse(matched=False, score=round(score, 4), reason="face_mismatch")

    return VerifyResponse(matched=True, score=round(score, 4))


@router.post("/identify", response_model=VerifyResponse)
def identify(
    kode_lokasi: str = Depends(get_kode_lokasi_form),
    challenge: Challenge = Form(...),
    file_a: UploadFile = File(..., description="Frame netral"),
    file_b: UploadFile = File(..., description="Frame setelah gerakan challenge"),
):
    """Identifikasi 1:N — cari NIK pemilik wajah, tanpa NIK sebagai input.

    Endpoint terpisah dari /verify, bukan `nik` opsional. Kalau `nik` dibuat
    opsional, satu field yang lupa terkirim dari Lumen akan diam-diam mengubah
    verifikasi 1:1 menjadi pencarian 1:N yang tetap membalas matched=true —
    gagal terbuka tanpa sinyal error sama sekali.

    Peringatan kalibrasi: MATCH_THRESHOLD dikalibrasi untuk 1:1. Pada 1:N,
    peluang false accept naik seiring jumlah wajah terdaftar karena skor
    tertinggi diambil dari N kandidat, bukan satu. Ambang untuk endpoint ini
    umumnya perlu lebih tinggi — ukur dengan data lapangan sebelum dipakai
    untuk keputusan absensi.
    """
    image_a = _read_image(file_a)
    image_b = _read_image(file_b)

    vec = _embed_after_liveness(image_a, image_b, challenge)
    if vec is None:
        return VerifyResponse(matched=False, reason="liveness_failed")

    hasil = face.identify(kode_lokasi, vec)
    if hasil is None:  # belum ada satu pun wajah terdaftar
        return VerifyResponse(matched=False, reason="not_enrolled")

    nik, score = hasil
    if score < settings.match_threshold:
        # NIK kandidat sengaja tidak dikembalikan saat gagal cocok.
        return VerifyResponse(matched=False, score=round(score, 4), reason="face_mismatch")

    return VerifyResponse(matched=True, nik=nik, score=round(score, 4))


@router.delete("/{nik}")
def delete_face(
    nik: str,
    kode_lokasi: str = Depends(get_kode_lokasi),
    conn=Depends(get_db),
):
    """Hapus wajah terdaftar milik satu NIK."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM karyawan_vector WHERE nik_karyawan = ?", nik)
    if cursor.rowcount == 0:
        raise HTTPException(404, "Wajah untuk NIK tersebut tidak ditemukan")
    conn.commit()

    face.refresh_cache(conn, kode_lokasi)
    return {"nik": nik, "deleted": True}
