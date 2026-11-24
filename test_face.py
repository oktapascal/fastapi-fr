"""Pengecekan logika verifikasi. Jalankan: python test_face.py"""

import numpy as np

from app import face


def test_similarity():
    """Verifikasi 1:1 terhadap vektor tersimpan."""
    face._vectors = {"SAI01": {
        "A001": np.array([1, 0, 0], dtype=np.float32),
        "A002": np.array([0, 1, 0], dtype=np.float32),
    }}

    # Wajah sendiri -> similarity 1.0
    assert face.similarity("SAI01", "A001", np.array([1, 0, 0], np.float32)) == 1.0
    # Wajah orang lain -> ortogonal, 0.0
    assert face.similarity("SAI01", "A001", np.array([0, 1, 0], np.float32)) == 0.0
    # NIK belum terdaftar -> None, bukan exception
    assert face.similarity("SAI01", "TIDAK_ADA", np.array([1, 0, 0], np.float32)) is None

    assert face.is_enrolled("SAI01", "A001") and not face.is_enrolled("SAI01", "TIDAK_ADA")
    assert face.enrolled_count() == 2

    face._vectors = {}
    assert face.enrolled_count() == 0
    assert face.similarity("SAI01", "A001", np.array([1, 0, 0], np.float32)) is None


def test_identify():
    """Pencarian 1:N: selalu mengembalikan kandidat terdekat, bukan keputusan."""
    face._vectors = {"SAI01": {
        "A001": np.array([1, 0, 0], dtype=np.float32),
        "A002": np.array([0, 1, 0], dtype=np.float32),
    }}

    assert face.identify("SAI01", np.array([0.9, 0.1, 0], np.float32))[0] == "A001"
    assert face.identify("SAI01", np.array([0.1, 0.9, 0], np.float32))[0] == "A002"

    # Wajah asing tetap dapat kandidat dengan skor rendah — justru inilah alasan
    # pemanggil wajib menyaring dengan threshold, bukan percaya nik-nya saja.
    nik, score = face.identify("SAI01", np.array([0, 0, 1], np.float32))
    assert nik in ("A001", "A002") and score == 0.0

    face._vectors = {}
    assert face.identify("SAI01", np.array([1, 0, 0], np.float32)) is None


def test_kesetaraan_threshold():
    """Kunci konversi metrik terhadap implementasi referensi.

    Referensi (workspace-face-fr) memakai euclidean distance < 0.83. Di sini
    dipakai cosine similarity >= threshold. Untuk vektor ternormalisasi berlaku
    d^2 = 2 - 2*cos, jadi kedua ambang harus menghasilkan keputusan yang sama.
    Tes ini gagal kalau ada yang menyalin 0.83 mentah-mentah ke MATCH_THRESHOLD.
    """
    from app.config import settings

    d_referensi = 0.83
    cos_setara = 1 - d_referensi**2 / 2

    assert abs(settings.match_threshold - cos_setara) < 0.01, (
        f"MATCH_THRESHOLD={settings.match_threshold} tidak setara dengan "
        f"euclidean {d_referensi} (seharusnya ~{cos_setara:.4f})"
    )

    # Keputusan match harus identik lewat kedua metrik
    rng = np.random.default_rng(0)
    for _ in range(200):
        a, b = rng.normal(size=(2, 512))
        a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
        cocok_cosine = float(a @ b) >= cos_setara
        cocok_euclidean = float(np.linalg.norm(a - b)) < d_referensi
        assert cocok_cosine == cocok_euclidean


def test_endpoints_accept_single_frame_without_challenge():
    """New contract: one frame, no liveness challenge."""
    import io

    from fastapi.testclient import TestClient
    from PIL import Image

    from app import main
    from app.config import settings

    def frame() -> io.BytesIO:
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32)).save(buffer, "JPEG")
        buffer.seek(0)
        return buffer

    kode_lokasi = next(iter(settings.db_location_mapping))
    face._vectors = {kode_lokasi: {"A001": np.array([1, 0, 0], dtype=np.float32)}}
    original_embed = face.embed
    face.embed = lambda _image: np.array([1, 0, 0], dtype=np.float32)
    try:
        client = TestClient(main.app)
        headers = {"X-API-Key": settings.api_key}
        verified = client.post(
            "/api/face/verify",
            data={"nik": "A001", "kode_lokasi": kode_lokasi},
            files={"file": ("face.jpg", frame(), "image/jpeg")},
            headers=headers,
        )
        identified = client.post(
            "/api/face/identify",
            data={"kode_lokasi": kode_lokasi},
            files={"file": ("face.jpg", frame(), "image/jpeg")},
            headers=headers,
        )
    finally:
        face.embed = original_embed
        face._vectors = {}

    assert verified.status_code == 200, verified.text
    assert verified.json()["matched"] is True and verified.json()["score"] == 1.0
    assert identified.status_code == 200, identified.text
    assert identified.json()["nik"] == "A001"


def test_identity_check_rejects_other_face():
    """Single-frame verification still compares against the enrolled NIK."""
    import io

    from fastapi.testclient import TestClient
    from PIL import Image

    from app import main
    from app.config import settings

    def frame() -> io.BytesIO:
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32)).save(buffer, "JPEG")
        buffer.seek(0)
        return buffer

    kode_lokasi = next(iter(settings.db_location_mapping))
    face._vectors = {kode_lokasi: {"A001": np.array([1, 0, 0], dtype=np.float32)}}
    original_embed = face.embed
    face.embed = lambda _image: np.array([0, 1, 0], dtype=np.float32)
    try:
        client = TestClient(main.app)
        headers = {"X-API-Key": settings.api_key}
        verified = client.post(
            "/api/face/verify",
            data={"nik": "A001", "kode_lokasi": kode_lokasi},
            files={"file": ("face.jpg", frame(), "image/jpeg")},
            headers=headers,
        )
        identified = client.post(
            "/api/face/identify",
            data={"kode_lokasi": kode_lokasi},
            files={"file": ("face.jpg", frame(), "image/jpeg")},
            headers=headers,
        )
    finally:
        face.embed = original_embed
        face._vectors = {}

    assert verified.status_code == 200, verified.text
    assert verified.json()["matched"] is False
    assert verified.json()["reason"] == "face_mismatch"
    assert identified.status_code == 200, identified.text
    assert identified.json()["matched"] is False
    assert identified.json()["nik"] is None


def test_health_db_tidak_bocorkan_kredensial():
    """/health/db publik — kegagalan tidak boleh membocorkan kredensial DB.

    Pesan error pyodbc bisa memuat host, user, bahkan potongan connection
    string. Endpoint ini tanpa auth, jadi hanya SQLSTATE yang boleh keluar.
    """
    import pyodbc
    from fastapi.testclient import TestClient

    from app import main

    rahasia = "Saku3@ws1979Sql"
    pesan = f"[08001] TCP Provider: gagal konek ke 52.220.100.190 UID=sa;PWD={rahasia}"

    def connect_gagal(*args, **kwargs):
        raise pyodbc.Error("08001", pesan)

    asli = pyodbc.connect
    pyodbc.connect = connect_gagal
    try:
        # TestClient tanpa `with` sengaja tidak menjalankan lifespan, jadi
        # model dan cache DB tidak ikut dimuat saat tes.
        resp = TestClient(main.app).get(
            "/health/db", headers={"X-Kode-Lokasi": "SAI01"}
        )
    finally:
        pyodbc.connect = asli

    assert resp.status_code == 503, resp.status_code
    assert resp.json() == {"connected": False, "sqlstate": "08001"}
    # Yang paling penting: tidak ada jejak kredensial atau IP di respons
    assert rahasia not in resp.text
    assert "52.220.100.190" not in resp.text
    assert "PWD" not in resp.text


def test_database_dipilih_dari_header():
    """Setiap kode lokasi harus memilih nama DB dari konfigurasi."""
    import pyodbc
    from fastapi.testclient import TestClient

    from app import db, main

    dsn_terakhir = ""

    class KoneksiPalsu:
        def close(self):
            pass

    def connect_palsu(dsn, **kwargs):
        nonlocal dsn_terakhir
        dsn_terakhir = dsn
        return KoneksiPalsu()

    asli = pyodbc.connect
    pyodbc.connect = connect_palsu
    try:
        koneksi = next(db.get_db("41"))
        koneksi.close()
    finally:
        pyodbc.connect = asli

    assert "DATABASE=dbyagk;" in dsn_terakhir
    client = TestClient(main.app)
    assert client.get("/health/db").status_code == 400
    assert client.get(
        "/health/db", headers={"X-Kode-Lokasi": "TIDAK_ADA"}
    ).status_code == 400


def test_read_image_ikuti_exif_orientation():
    """Foto HP miring + EXIF Orientation=6 harus diputar tegak sebelum dideteksi."""
    import io

    from fastapi import UploadFile
    from PIL import Image
    from starlette.datastructures import Headers

    from app.routes import _read_image

    # Piksel tersimpan landscape 200x100, EXIF menyatakan seharusnya diputar 90°
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (200, 100)).save(buf, "JPEG", exif=exif)
    buf.seek(0)

    file = UploadFile(buf, headers=Headers({"content-type": "image/jpeg"}))
    assert _read_image(file).size == (100, 200)


if __name__ == "__main__":
    test_similarity()
    test_identify()
    test_kesetaraan_threshold()
    test_endpoints_accept_single_frame_without_challenge()
    test_identity_check_rejects_other_face()
    test_health_db_tidak_bocorkan_kredensial()
    test_database_dipilih_dari_header()
    test_read_image_ikuti_exif_orientation()
    print("OK")
