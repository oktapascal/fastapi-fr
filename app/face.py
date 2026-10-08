"""Seluruh logika ML: deteksi wajah, embedding, liveness, dan pencocokan.

Verifikasi bersifat 1:1 — Lumen mengirim NIK, service membandingkan wajah
dengan satu vektor milik NIK tersebut. Bukan pencarian 1:N.

Embedding disimpan di DB sebagai JSON string (nvarchar(max)) karena SQL Server
2019 tidak punya tipe vector maupun pencarian similarity.

Parameter model dan ambang liveness mengikuti implementasi referensi
(workspace-face-fr/face_api/api.py) agar kalibrasi di sana tetap berlaku.
"""

import json

import numpy as np
import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image

# Dimensi embedding InceptionResnetV1 (pretrained vggface2)
EMBED_DIM = 512

# Challenge liveness yang didukung; dipakai juga untuk validasi di schemas.py
CHALLENGES = ("smile", "look_left", "look_right")

_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

_mtcnn: MTCNN | None = None
_resnet: InceptionResnetV1 | None = None

# Cache embedding, dipisah per lokasi lalu dikunci per NIK. Query + parse JSON tiap request terlalu
# mahal, jadi seluruh isi tabel dimuat sekali saat startup.
_vectors: dict[str, dict[str, np.ndarray]] = {}


def load_models() -> None:
    """Muat model sekali saja saat startup (~2-5 detik, ~100MB).

    Parameter MTCNN sengaja dibiarkan default (image_size=160, margin=0), sama
    persis dengan implementasi referensi. Mengubah margin menggeser crop wajah
    -> menggeser embedding -> membatalkan kalibrasi threshold di sana.
    """
    global _mtcnn, _resnet
    _mtcnn = MTCNN(keep_all=False, device=_device)
    _resnet = InceptionResnetV1(pretrained="vggface2").eval().to(_device)


def embed(image: Image.Image) -> np.ndarray:
    """Deteksi wajah lalu hasilkan embedding 512-dim yang sudah ternormalisasi.

    Raises:
        ValueError: bila tidak ada wajah terdeteksi pada gambar.
    """
    face = _mtcnn(image)
    if face is None:
        raise ValueError(
            "Wajah tidak terdeteksi. Pastikan wajah terlihat jelas dan pencahayaan cukup."
        )
    with torch.inference_mode():
        vec = _resnet(face.unsqueeze(0).to(_device))[0].cpu().numpy()
    # InceptionResnetV1 sudah mengeluarkan vektor ternormalisasi L2 (norm = 1.0,
    # sudah diverifikasi). Pembagian ini penjaga agar asumsi "dot product ==
    # cosine similarity" tetap sah kalau modelnya suatu saat diganti.
    return vec / np.linalg.norm(vec)


def landmarks_of(image: Image.Image, frame: str) -> np.ndarray:
    """Ambil 5 titik landmark wajah pertama: 2 mata, hidung, 2 sudut mulut.

    `frame` disebut di pesan error agar log bisa membedakan foto bermasalah
    (frame netral) dari kepala yang menoleh terlalu jauh (frame gerakan).

    Raises:
        ValueError: bila tidak ada wajah terdeteksi.
    """
    _, _, landmarks = _mtcnn.detect(image, landmarks=True)
    if landmarks is None:
        raise ValueError(f"Wajah tidak terdeteksi pada frame {frame}.")
    return landmarks[0]


def check_liveness(landmarks_a, landmarks_b, challenge: str) -> bool:
    """Bandingkan dua frame: apakah gerakan sesuai challenge benar-benar terjadi?

    Disalin dari implementasi referensi, termasuk ambangnya (3% pelebaran mulut
    untuk senyum, 8 piksel selisih yaw untuk menoleh) — angka-angka itu sudah
    teruji di sana, jangan diubah tanpa pengujian ulang.
    """

    def mouth_width(points) -> float:
        return float(points[4][0] - points[3][0])

    def yaw_difference(points) -> float:
        left_eye, right_eye, nose = points[0], points[1], points[2]
        return float((nose[0] - left_eye[0]) - (right_eye[0] - nose[0]))

    if challenge == "smile":
        width_a = mouth_width(landmarks_a)
        width_b = mouth_width(landmarks_b)
        return width_a > 0 and ((width_b - width_a) / width_a) > 0.03
    if challenge == "look_right":
        return yaw_difference(landmarks_b) < yaw_difference(landmarks_a) - 8
    if challenge == "look_left":
        return yaw_difference(landmarks_b) > yaw_difference(landmarks_a) + 8
    return False


def refresh_cache(conn, kode_lokasi: str) -> None:
    """Muat ulang seluruh vektor satu lokasi dari DB ke memori."""
    rows = conn.execute(
        "SELECT nik_karyawan, face_vector "
        "FROM karyawan_vector WHERE face_vector IS NOT NULL"
    ).fetchall()
    _vectors[kode_lokasi] = {
        r.nik_karyawan: np.array(json.loads(r.face_vector), dtype=np.float32)
        for r in rows
    }


def enrolled_count() -> int:
    """Jumlah wajah yang termuat di cache."""
    return sum(map(len, _vectors.values()))


def is_enrolled(kode_lokasi: str, nik: str) -> bool:
    """Apakah NIK sudah punya wajah terdaftar?"""
    return nik in _vectors.get(kode_lokasi, {})


def similarity(kode_lokasi: str, nik: str, vec: np.ndarray) -> float | None:
    """Cosine similarity wajah terhadap vektor tersimpan milik NIK.

    Mengembalikan None bila NIK belum punya wajah terdaftar. Karena kedua vektor
    ternormalisasi, dot product setara cosine similarity.
    """
    tersimpan = _vectors.get(kode_lokasi, {}).get(nik)
    if tersimpan is None:
        return None
    return float(tersimpan @ vec)


def identify(kode_lokasi: str, vec: np.ndarray) -> tuple[str, float] | None:
    """Cari NIK dengan similarity tertinggi — pencarian 1:N.

    Mengembalikan None hanya bila cache kosong. Selain itu SELALU ada kandidat
    terbaik, sedekat apa pun: penyaringan terhadap threshold adalah tugas
    pemanggil, bukan fungsi ini.

    ponytail: scan linear atas dict, cukup untuk ribuan karyawan. Kalau jumlah
    wajah membengkak, tumpuk vektor jadi satu matriks (N, 512) dan ganti loop
    ini dengan satu perkalian matriks.
    """
    vectors = _vectors.get(kode_lokasi, {})
    if not vectors:
        return None
    nik = max(vectors, key=lambda k: vectors[k] @ vec)
    return nik, float(vectors[nik] @ vec)
