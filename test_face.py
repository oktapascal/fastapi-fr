"""Pengecekan logika verifikasi & liveness. Jalankan: python test_face.py"""

import numpy as np

from app import face


def test_similarity():
    """Verifikasi 1:1 terhadap vektor tersimpan."""
    face._vectors = {
        "A001": np.array([1, 0, 0], dtype=np.float32),
        "A002": np.array([0, 1, 0], dtype=np.float32),
    }

    # Wajah sendiri -> similarity 1.0
    assert face.similarity("A001", np.array([1, 0, 0], np.float32)) == 1.0
    # Wajah orang lain -> ortogonal, 0.0
    assert face.similarity("A001", np.array([0, 1, 0], np.float32)) == 0.0
    # NIK belum terdaftar -> None, bukan exception
    assert face.similarity("TIDAK_ADA", np.array([1, 0, 0], np.float32)) is None

    assert face.is_enrolled("A001") and not face.is_enrolled("TIDAK_ADA")
    assert face.enrolled_count() == 2

    face._vectors = {}
    assert face.enrolled_count() == 0
    assert face.similarity("A001", np.array([1, 0, 0], np.float32)) is None


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


def test_liveness():
    """Landmark: [mata_kiri, mata_kanan, hidung, mulut_kiri, mulut_kanan]."""
    # Wajah netral menghadap depan; hidung di tengah antara kedua mata
    netral = np.array([[40, 50], [60, 50], [50, 60], [42, 75], [58, 75]], dtype=float)

    # -- smile: mulut melebar >3% --
    senyum = netral.copy()
    senyum[3][0], senyum[4][0] = 40, 60  # lebar 16 -> 20 (+25%)
    assert face.check_liveness(netral, senyum, "smile")
    # Frame identik bukan senyum
    assert not face.check_liveness(netral, netral, "smile")
    # Pelebaran 1% saja belum cukup
    tipis = netral.copy()
    tipis[4][0] = 58.16
    assert not face.check_liveness(netral, tipis, "smile")

    # -- menoleh: hidung bergeser terhadap kedua mata --
    # yaw netral = (50-40) - (60-50) = 0
    kanan = netral.copy()
    kanan[2][0] = 44  # yaw = 4 - 16 = -12, selisih -12 < -8
    assert face.check_liveness(netral, kanan, "look_right")
    assert not face.check_liveness(netral, kanan, "look_left")

    kiri = netral.copy()
    kiri[2][0] = 56  # yaw = 16 - 4 = +12
    assert face.check_liveness(netral, kiri, "look_left")
    assert not face.check_liveness(netral, kiri, "look_right")

    # Gerakan terlalu kecil (di bawah ambang 8 piksel) ditolak
    sedikit = netral.copy()
    sedikit[2][0] = 48  # yaw = -4
    assert not face.check_liveness(netral, sedikit, "look_right")

    # Challenge tak dikenal selalu gagal (defense in depth; Pydantic sudah
    # menyaringnya lebih dulu lewat tipe Literal di schemas.py)
    assert not face.check_liveness(netral, senyum, "menari")


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
        resp = TestClient(main.app).get("/health/db")
    finally:
        pyodbc.connect = asli

    assert resp.status_code == 503, resp.status_code
    assert resp.json() == {"connected": False, "sqlstate": "08001"}
    # Yang paling penting: tidak ada jejak kredensial atau IP di respons
    assert rahasia not in resp.text
    assert "52.220.100.190" not in resp.text
    assert "PWD" not in resp.text


if __name__ == "__main__":
    test_similarity()
    test_kesetaraan_threshold()
    test_liveness()
    test_health_db_tidak_bocorkan_kredensial()
    print("OK")
