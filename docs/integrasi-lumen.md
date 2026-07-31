# Integrasi Sisi Lumen

Dokumen ini menjelaskan peran Lumen sebagai core API dalam alur verifikasi
wajah: apa yang ia terima dari Laravel, bagaimana ia memanggil service Python,
dan apa yang ia lakukan dengan hasilnya.

Pasangan dokumen ini: [`integrasi-laravel.md`](integrasi-laravel.md).

---

## Peran Lumen

Lumen adalah **pemilik data dan aturan bisnis**. Dalam alur ini:

1. Memvalidasi JWT dan **menentukan siapa user-nya dari klaim token**.
2. Memvalidasi bahwa NIK ada dan aktif di master `karyawan`.
3. Memanggil service Python untuk verifikasi wajah.
4. Mencatat absensi — atau mencatat kegagalannya.

Lumen adalah **satu-satunya** layer yang memegang kredensial service Python.

### Mengapa Lumen, bukan Laravel, yang memanggil Python

Kalau Laravel memanggil Python langsung, Laravel akan menerima "wajah ini cocok
dengan NIK X" lalu memberi tahu Lumen *"catat absensi untuk NIK X"*. Dari sudut
pandang Lumen, itu **klaim yang tidak bisa diverifikasi** — tidak ada cara
membedakan NIK hasil verifikasi wajah asli dari NIK yang dikarang oleh siapa pun
yang bisa memanggil endpoint absensi.

Dengan Lumen yang memanggil Python sendiri, identitas hasil verifikasi tidak
pernah keluar dari proses tepercaya. Verifikasi dan pencatatan terjadi dalam satu
request yang sama.

---

## Kontrak dengan Laravel

```
POST /api/absensi/verifikasi
Authorization: Bearer <JWT user>
X-Request-Id: <uuid>
Content-Type: multipart/form-data

challenge = smile | look_left | look_right
file_a    = <frame netral>
file_b    = <frame setelah gerakan>
```

**NIK tidak ada di kontrak ini.** Itu disengaja dan merupakan titik keamanan
paling penting di seluruh alur — lihat bagian berikutnya.

---

## 1. Identitas Datang dari Token, Bukan dari Input

```php
public function verifikasi(Request $request)
{
    $this->validate($request, [
        'challenge' => 'required|in:smile,look_left,look_right',
        'file_a'    => 'required|image|max:5120',
        'file_b'    => 'required|image|max:5120',
    ]);

    // NIK diambil dari klaim JWT. JANGAN dari $request->input('nik').
    $user = auth()->user();
    $nik = $user->nik;
    $kodeLokasi = $user->kode_lokasi;
```

Ini bukan detail gaya penulisan. Kalau NIK diambil dari input request, siapa pun
yang punya JWT valid bisa mengirim NIK orang lain dan mencatatkan absensi atas
nama orang itu — wajahnya sendiri akan gagal cocok, tetapi kegagalan itu
terjadi *setelah* NIK dipercaya, dan setiap jalur yang tidak memeriksa hasil
verifikasi dengan ketat menjadi celah.

> Aturan: **siapa** ditentukan token, **apa** ditentukan request body.

Sumber NIK yang sah, berurutan preferensi:

1. Klaim di dalam JWT (`$user->nik`) — paling aman, tidak bisa diubah klien.
2. Lookup dari `user_id` di klaim ke tabel user, kalau NIK tidak ada di token.

Yang tidak sah: `$request->input('nik')`, query string, header dari klien.

---

## 2. Validasi Master Karyawan

```php
    $karyawan = DB::table('karyawan')
        ->where('nik', $nik)
        ->where('kode_lokasi', $kodeLokasi)
        ->where('flag_aktif', 'Y')
        ->first(['nik', 'nama']);

    if (!$karyawan) {
        return response()->json([
            'status'  => 'error',
            'message' => 'Data karyawan tidak ditemukan atau tidak aktif.',
        ], 403);
    }
```

Tabel `karyawan` punya primary key komposit `(nik, kode_lokasi)`, jadi keduanya
diperlukan. Karyawan non-aktif harus ditolak di sini — service Python tidak tahu
apa-apa soal status kepegawaian, dan wajah orang yang sudah resign masih akan
cocok sampai vektornya dihapus.

---

## 3. Memanggil Service Python

```php
    $response = Http::withHeaders([
            'X-API-Key'    => env('FACE_API_KEY'),
            'X-Request-Id' => $request->header('X-Request-Id', (string) Str::uuid()),
        ])
        ->connectTimeout(5)
        ->timeout(15)   // inference CPU: 2x deteksi landmark + 1x embedding
        ->attach('file_a', file_get_contents($request->file('file_a')), 'a.jpg')
        ->attach('file_b', file_get_contents($request->file('file_b')), 'b.jpg')
        ->post(env('FACE_SERVICE_URL') . '/api/face/verify', [
            'nik'       => $nik,          // dari JWT, bukan dari input
            'challenge' => $request->input('challenge'),
        ]);

    if ($response->failed()) {
        Log::error('Face service gagal', [
            'status'     => $response->status(),
            'request_id' => $request->header('X-Request-Id'),
        ]);
        return response()->json([
            'status'  => 'error',
            'message' => 'Layanan verifikasi wajah tidak tersedia.',
        ], 503);
    }

    $hasil = $response->json();
```

### Respons dari Python

```json
{ "matched": true,  "score": 0.9134, "reason": null }
{ "matched": false, "score": 0.4210, "reason": "face_mismatch" }
{ "matched": false, "score": null,   "reason": "liveness_failed" }
{ "matched": false, "score": null,   "reason": "not_enrolled" }
```

Ketiga kegagalan itu dikembalikan dengan status **200**, bukan error — semuanya
adalah hasil bisnis yang sah, bukan kerusakan sistem. Yang berstatus non-200:

| Status | Arti | Sikap Lumen |
|---|---|---|
| 401 | API key salah/hilang | Konfigurasi rusak — alert, jangan retry |
| 422 | Wajah tidak terdeteksi di frame | Teruskan ke user, minta ulang |
| 400 / 413 | File bukan gambar / terlalu besar | Teruskan sebagai validasi |
| 503 / timeout | Service mati | Pesan generik |

---

## 4. Menindaklanjuti Hasil

```php
    if (!$hasil['matched']) {
        // liveness_failed layak dicatat terpisah: itu indikasi percobaan
        // spoofing, bukan sekadar wajah yang tidak cocok.
        $this->catatKegagalan($nik, $hasil['reason'], $hasil['score'] ?? null);

        return response()->json([
            'status'  => 'error',
            'matched' => false,
            'message' => match ($hasil['reason']) {
                'liveness_failed' => 'Gerakan tidak terdeteksi. Ulangi sesuai instruksi.',
                'face_mismatch'   => 'Wajah tidak cocok dengan data Anda.',
                'not_enrolled'    => 'Wajah Anda belum terdaftar. Hubungi HRD.',
                default           => 'Verifikasi gagal.',
            },
        ], 200);
    }

    // Idempotensi: absensi ganda pada hari yang sama bukan error
    if ($this->sudahAbsenHariIni($nik)) {
        return response()->json([
            'status'  => 'success',
            'matched' => true,
            'message' => 'Anda sudah melakukan absensi hari ini.',
        ]);
    }

    $this->catatAbsensi($nik, $kodeLokasi, $hasil['score']);

    return response()->json([
        'status'  => 'success',
        'matched' => true,
        'message' => 'Absensi berhasil direkam.',
        'nama'    => $karyawan->nama,
    ]);
}
```

Simpan `score` bersama catatan absensi. Ketika suatu saat perlu mengkalibrasi
ulang threshold, distribusi skor nyata dari lapangan adalah data terbaik yang
Anda punya — dan tidak bisa direkonstruksi setelah kejadian.

Catat `liveness_failed` secara terpisah dan pantau. Lonjakan mendadak pada satu
NIK adalah sinyal percobaan titip absen, dan itu justru kejadian yang paling
ingin Anda ketahui dari sistem ini.

---

## 5. Enroll Wajah

Pendaftaran wajah baru juga lewat Lumen, dengan pola yang sama tetapi otorisasi
berbeda: ini operasi **admin/HRD**, bukan operasi karyawan atas dirinya sendiri.

```php
// Wajib dicek: hanya petugas berwenang yang boleh mendaftarkan wajah.
$this->authorize('kelola-wajah-karyawan');

// NIK di sini datang dari input — sah, karena admin mendaftarkan orang lain.
// Karena itu validasi ke master karyawan wajib.
$karyawan = DB::table('karyawan')
    ->where('nik', $request->input('nik'))
    ->where('kode_lokasi', $request->input('kode_lokasi'))
    ->where('flag_aktif', 'Y')
    ->first();

if (!$karyawan) {
    return response()->json(['status' => 'error', 'message' => 'NIK tidak valid.'], 422);
}

$response = Http::withHeaders(['X-API-Key' => env('FACE_API_KEY')])
    ->timeout(15)
    ->attach('file', file_get_contents($request->file('foto')), 'face.jpg')
    ->post(env('FACE_SERVICE_URL') . '/api/face/enroll', [
        'nik'         => $request->input('nik'),
        'kode_lokasi' => $request->input('kode_lokasi'),
    ]);
```

Perhatikan perbedaannya dengan verifikasi: di sini NIK **memang** boleh dari
input, karena admin mendaftarkan wajah orang lain. Justru karena itu validasi ke
master `karyawan` menjadi wajib — service Python sengaja tidak menyentuh tabel
itu sama sekali dan akan menerima NIK apa pun yang dikirim.

Enroll tidak memakai liveness (satu frame saja). Prosesnya diawasi petugas, jadi
model ancamannya berbeda dari verifikasi harian.

### Saat karyawan resign

```php
Http::withHeaders(['X-API-Key' => env('FACE_API_KEY')])
    ->delete(env('FACE_SERVICE_URL') . "/api/face/{$nik}");
```

Menonaktifkan `flag_aktif` di `karyawan` **tidak** menghapus vektor wajah.
Selama vektor masih ada, wajahnya masih akan cocok — yang menahannya cuma
pengecekan `flag_aktif` di Lumen. Hapus vektornya juga.

---

## 6. Konfigurasi

```ini
# .env Lumen
FACE_SERVICE_URL=http://127.0.0.1:8000
FACE_API_KEY=<sama persis dengan API_KEY di .env service Python>
```

`FACE_API_KEY` harus identik dengan `API_KEY` di `.env` Python. Kalau
digenerate dengan `secrets.token_urlsafe()` (hanya `A-Z a-z 0-9 - _`), nilainya
aman disalin tanpa tanda kutip. Nilai base64 standar yang mengandung `/` dan
diakhiri `=` perlu dikutip di `.env` dan mudah ter-truncate diam-diam saat
disalin — gejalanya cuma `401`, tanpa petunjuk apa pun.

Kalau Python berjalan di server yang sama, biarkan ia bind ke `127.0.0.1` dan
tidak ada port yang perlu dibuka. Kalau beda server, batasi aksesnya di firewall
ke IP server Lumen saja — jangan mengandalkan API key sendirian.

---

## Checklist Keamanan

- [ ] NIK untuk verifikasi diambil dari klaim JWT, **tidak pernah** dari input
- [ ] `flag_aktif = 'Y'` dicek sebelum memanggil Python
- [ ] Endpoint enroll dilindungi otorisasi admin/HRD
- [ ] `FACE_API_KEY` hanya ada di `.env` Lumen, tidak di Laravel
- [ ] `connectTimeout` dan `timeout` diset eksplisit
- [ ] `score` disimpan bersama catatan absensi untuk kalibrasi di kemudian hari
- [ ] `liveness_failed` dicatat terpisah dan dipantau
- [ ] Vektor wajah dihapus saat karyawan resign
- [ ] `X-Request-Id` diteruskan ke Python dan masuk log
