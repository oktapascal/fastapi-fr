# Setup Guide — Face Recognition Service

Layanan ini hanya menjawab satu pertanyaan: *"benarkah wajah ini milik NIK
tersebut?"* — verifikasi **1:1**, bukan pencarian 1:N. Lumen mengirim NIK
beserta dua frame wajah; service memeriksa liveness lalu mencocokkan.
Dipanggil oleh Lumen (core API), tidak pernah oleh browser secara langsung.

```
Browser → Laravel → Lumen → Python (service ini)
```

Untuk integrasi sisi PHP, lihat [`docs/integrasi-laravel.md`](docs/integrasi-laravel.md)
dan [`docs/integrasi-lumen.md`](docs/integrasi-lumen.md).

---

## Prasyarat Umum

| Komponen | Versi | Catatan |
|---|---|---|
| Python | 3.11.x | |
| ODBC Driver for SQL Server | 17 atau 18 | **wajib**, pyodbc tidak jalan tanpa ini |
| SQL Server | 2019 | tabel `karyawan_vector` sudah ada |

Tabel yang dipakai hanya `karyawan_vector`. Tabel `hr_karyawan` **tidak** disentuh
oleh service ini — validasi NIK ada di Lumen.

---

## A. Local Development (Windows)

### 1. Cek ODBC Driver

```powershell
Get-OdbcDriver -Name "*SQL Server*"
```

Kalau kosong, unduh **Microsoft ODBC Driver 17 for SQL Server** dari situs
Microsoft dan install. Ini penyebab error paling umum saat pertama setup:

```
('01000', "[01000] [unixODBC][Driver Manager]Can't open lib ... file not found")
```

### 2. Virtual environment & dependensi

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Kalau `Activate.ps1` ditolak execution policy:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 3. Konfigurasi

```powershell
Copy-Item .env.example .env
```

Isi `.env`:

```ini
DB_DSN=DRIVER={ODBC Driver 17 for SQL Server};SERVER=52.220.100.190,8282;DATABASE=dbproduk;UID=user;PWD=password
API_KEY=string-acak-panjang
MATCH_THRESHOLD=0.6556
MAX_UPLOAD_BYTES=5242880
```

Generate API key:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> **Driver 18 vs 17.** Driver 18 memakai `Encrypt=yes` sebagai default dan akan
> menolak sertifikat self-signed. Kalau pakai 18 ke SQL Server tanpa sertifikat
> tepercaya, tambahkan `TrustServerCertificate=yes;` di DSN.

### 4. Jalankan

```powershell
venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 3000
```

Startup pertama mengunduh bobot `vggface2` (~100 MB) ke
`C:\Users\<user>\.cache\torch\hub\checkpoints`. Sabar, sekali saja.

### 5. Verifikasi

```powershell
# Health check — tanpa auth, menampilkan jumlah wajah yang termuat
curl http://127.0.0.1:3000/health

# Verify (1:1) — dua frame: netral + setelah gerakan challenge
curl -X POST http://127.0.0.1:3000/api/face/verify `
  -H "X-API-Key: <api-key>" `
  -F "nik=12345" -F "challenge=smile" `
  -F "file_a=@netral.jpg" -F "file_b=@senyum.jpg"
```

Dokumentasi interaktif: <http://127.0.0.1:3000/docs>

Jalankan self-check logika pencocokan:

```powershell
venv\Scripts\python.exe test_face.py
```

---

## B. Server Deployment (Debian + PM2)

### 1. Sistem & ODBC Driver

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv curl gnupg

# Repositori Microsoft (ganti 12 dengan versi Debian Anda)
curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
  | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc > /dev/null
curl -sSL https://packages.microsoft.com/config/debian/12/prod.list \
  | sudo tee /etc/apt/sources.list.d/mssql-release.list

sudo apt update
sudo ACCEPT_EULA=Y apt install -y msodbcsql18 unixodbc-dev
```

Verifikasi driver terdaftar:

```bash
odbcinst -q -d
```

Sesuaikan nama driver di `.env` dengan hasil perintah di atas
(`{ODBC Driver 18 for SQL Server}`), termasuk `TrustServerCertificate=yes;`
bila perlu.

### 2. Deploy kode

```bash
sudo mkdir -p /opt/face-api && sudo chown $USER:$USER /opt/face-api
cd /opt/face-api
# salin kode ke sini (git clone / rsync)

python3.11 -m venv venv
source venv/bin/activate
```

### 3. Install dependensi

```bash
pip install -r requirements.txt
```

Satu perintah saja. `requirements.txt` sudah menyematkan `torch==2.2.2+cpu`
beserta `--extra-index-url` ke index CPU PyTorch, jadi paket CUDA (~2 GB) tidak
akan ikut terunduh — tidak ada langkah manual yang bisa terlupa.

Verifikasi setelah install:

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda)"
# harus mencetak: 2.2.2+cpu None
```

Kalau `torch.version.cuda` bukan `None`, berarti ada yang menginstal ulang torch
dari PyPI tanpa pin `+cpu`.

### 4. Konfigurasi

```bash
cp .env.example .env
nano .env          # isi DB_DSN dan API_KEY (sama dengan yang di config Lumen)
chmod 600 .env     # berisi kredensial DB, jangan world-readable
```

### 5. Pre-download bobot model

Lakukan **sebelum** `pm2 start`. Kalau tidak, unduhan 100 MB terjadi saat
startup pertama di bawah PM2 dan mudah disalahartikan sebagai proses hang:

```bash
source venv/bin/activate
python -c "from facenet_pytorch import InceptionResnetV1; InceptionResnetV1(pretrained='vggface2')"
```

### 6. PM2

`ecosystem.config.js`:

```js
module.exports = {
  apps: [{
    name: "face-api",
    cwd: "/opt/face-api",
    script: "venv/bin/python",
    args: "-m uvicorn app.main:app --host 127.0.0.1 --port 3000",
    interpreter: "none",          // script sudah executable, jangan lewat node
    instances: 1,                 // WAJIB 1 — lihat catatan di bawah
    autorestart: true,
    max_memory_restart: "2G",
    error_file: "/var/log/face-api/error.log",
    out_file: "/var/log/face-api/out.log",
  }]
};
```

```bash
sudo mkdir -p /var/log/face-api && sudo chown $USER:$USER /var/log/face-api

pm2 start ecosystem.config.js
pm2 save
pm2 startup        # jalankan perintah yang ditampilkan agar survive reboot
```

> **`instances: 1` bukan opsional.** Cache embedding hidup di memori proses.
> Dengan 2 worker atau lebih, enroll di worker A tidak terlihat oleh worker B
> sampai restart — karyawan yang baru didaftarkan akan gagal dikenali secara
> acak, tergantung worker mana yang kebagian request. Cluster mode PM2 juga
> tidak berlaku untuk proses Python.

### 7. Verifikasi

```bash
pm2 logs face-api --lines 50
curl http://127.0.0.1:3000/health
```

`faces_loaded` harus sama dengan jumlah baris di `karyawan_vector`. Kalau `0`
padahal tabel berisi, artinya koneksi DB gagal saat startup — cek log.

---

## Keamanan Jaringan

Service ini **bind ke `127.0.0.1`**, jadi tidak terjangkau dari luar server.

- **Lumen satu server dengan Python:** selesai, tidak ada yang perlu dibuka.
- **Lumen di server berbeda:** ubah bind ke `0.0.0.0` lalu batasi di firewall —
  jangan mengandalkan API key sendirian.

  ```bash
  sudo ufw allow from <IP-server-lumen> to any port 3000 proto tcp
  ```

Jangan pernah menaruh service ini langsung di internet. Endpoint `/verify`
menerima gambar sembarang dan mengonfirmasi identitas karyawan — terbuka
berarti siapa pun bisa menjadikannya oracle untuk menguji wajah terhadap NIK.

---

## Batas Ukuran Upload

Gambar melewati tiga hop. Batasnya harus dinaikkan di **semua** titik, bukan satu:

| Titik | Setting |
|---|---|
| Nginx (depan Laravel & Lumen) | `client_max_body_size 10M;` |
| PHP (Laravel & Lumen) | `upload_max_filesize`, `post_max_size` |
| Python | `MAX_UPLOAD_BYTES` di `.env` |

Kalau salah satu ketinggalan, gejalanya membingungkan: upload gagal dengan
error generik di hop yang sama sekali tidak menyebut ukuran file.

---

## Update Kode

```bash
cd /opt/face-api
git pull
source venv/bin/activate
pip install -r requirements.txt      # hanya bila requirements berubah
pm2 restart face-api
```

Restart memuat ulang model (~2-5 detik) dan seluruh cache vektor dari DB.
Selama jendela itu request akan gagal — lakukan di luar jam absensi.

---

## Troubleshooting

| Gejala | Penyebab |
|---|---|
| `Can't open lib ... file not found` | ODBC driver belum terinstall / nama driver di `DSN` salah |
| `SSL Provider: certificate verify failed` | Driver 18 tanpa `TrustServerCertificate=yes;` |
| `TCP Provider: Error code 0x2746 (10054)` saat connect — jalan di Windows, gagal di Debian | Kebijakan TLS OpenSSL di Debian — lihat di bawah |
| `/health` → `faces_loaded: 0` | Koneksi DB gagal saat startup, atau `karyawan_vector` memang kosong |
| Karyawan baru kadang dikenali kadang tidak | PM2 jalan lebih dari 1 instance |
| Startup pertama seperti hang | Sedang mengunduh bobot model — cek `pm2 logs` |
| Semua wajah `matched: false` | `MATCH_THRESHOLD` terlalu tinggi — cek juga apakah ada yang menyalin `0.83` dari referensi |
| Orang salah lolos verifikasi | `MATCH_THRESHOLD` terlalu rendah — naikkan bertahap |
| `reason: "not_enrolled"` | NIK belum pernah di-enroll, atau cache belum ter-refresh |
| `reason: "liveness_failed"` terus-menerus | Ambang gerakan tidak cocok dengan resolusi kamera — lihat Kalibrasi Liveness |

### Error 10054 di Debian (jalan di Windows lokal)

```
pyodbc.OperationalError: ('08001', '[08001] [Microsoft][ODBC Driver 17 for SQL Server]
TCP Provider: Error code 0x2746 (10054) (SQLDriverConnect)')
```

`10054` = koneksi diputus paksa oleh server, **bukan** timeout. TCP-nya sudah
nyambung; yang gagal adalah TLS handshake. SQL Server selalu mengenkripsi paket
login, jadi handshake tetap terjadi walau `Encrypt=no`.

Penyebabnya beda TLS stack: Windows memakai SChannel yang permisif, Debian 12
memakai OpenSSL 3.x dengan `SECLEVEL=2` + `MinProtocol=TLSv1.2` di
`/etc/ssl/openssl.cnf`. Sertifikat self-signed SQL Server 2019 (SHA-1 / RSA
1024) ditolak OpenSSL sebelum sempat divalidasi.

`TrustServerCertificate=yes` **tidak menolong** — flag itu melewati validasi
trust chain, yang baru berjalan *setelah* handshake sukses.

`Encrypt=yes` adalah **pemicu**, bukan akar masalah: `.env` lokal memakai flag
yang sama dan jalan normal di Windows. Flag itu menaikkan cakupan TLS dari
"hanya paket login" ke "seluruh koneksi", dan negosiasi penuh itulah yang
ditolak OpenSSL Debian.

Kerjakan berurutan, berhenti di langkah yang berhasil.

**1. Pastikan bukan masalah jaringan** (harus `succeeded`):

```bash
nc -vz <ip-sql-server> 8282
```

Untuk membaca SQLSTATE-nya tanpa mengulik log, service menyediakan `GET
/health/db` (tanpa auth, terpisah dari `/health`):

```bash
curl -i http://127.0.0.1:3000/health/db
# 200 {"connected":true}
# 503 {"connected":false,"sqlstate":"08001"}  <- gagal konek / TLS
# 503 {"connected":false,"sqlstate":"28000"}  <- kredensial salah
```

Pesan driver lengkap sengaja tidak dikembalikan (memuat IP & versi driver);
lihat `pm2 logs` untuk detailnya. Endpoint ini membuka koneksi sungguhan —
jangan dipakai sebagai target liveness check, pakai `/health` untuk itu.

**2. Turunkan cakupan TLS** — hapus `Encrypt=yes;TrustServerCertificate=yes`
dari `DB_DSN` di `.env`, lalu `pm2 restart face-api`. Driver 17 default-nya
`Encrypt=no` (hanya paket login yang dienkripsi). Kalau ini menyelesaikan
masalah, `openssl.cnf` tidak perlu disentuh sama sekali.

**3. Kalau masih `10054`** — handshake login-only pun ditolak. Longgarkan
kebijakan OpenSSL di `/etc/ssl/openssl.cnf`:

```ini
[openssl_init]
ssl_conf = ssl_sect

[ssl_sect]
system_default = system_default_sect

[system_default_sect]
CipherString = DEFAULT@SECLEVEL=0
MinProtocol = TLSv1
```

Lalu `pm2 restart face-api` (OpenSSL baca config saat proses start, tidak perlu
reboot).

> **Utang teknis — keduanya menukar keamanan dengan kemudahan.** DB diakses
> lewat IP publik. Langkah 2 membuat seluruh query dan hasilnya (NIK, vektor
> wajah) melintas **plaintext di internet**; langkah 3 melemahkan TLS untuk
> semua proses di server. Solusi permanennya di sisi SQL Server: pasang
> sertifikat RSA-2048/SHA-256, aktifkan TLS 1.2, lalu kembalikan `Encrypt=yes`
> dan `SECLEVEL=2`. Butuh koordinasi dengan pemegang server SQL.

### Kalibrasi Threshold

> **`MATCH_THRESHOLD` adalah cosine similarity, bukan euclidean distance.**
> Implementasi referensi (`workspace-face-fr`) memakai euclidean `0.83` dengan
> arah perbandingan terbalik. **Jangan menyalin angka 0.83 ke sini** — itu
> setara euclidean `0.58`, jauh lebih ketat, dan karyawan sah akan ditolak
> massal. Konversinya: `cosine = 1 − d² / 2`, jadi `d < 0.83` ⟺ `cos > 0.6556`
> (nilai default sekarang). `test_face.py` akan gagal kalau aturan ini dilanggar.

`0.6556` diwarisi dari kalibrasi referensi — titik awal yang wajar, **bukan
nilai final untuk lingkungan Anda**. Kumpulkan ~20 foto uji dengan kamera dan
pencahayaan yang sebenarnya dipakai, panggil `/verify`, dan amati `score`:

- Skor untuk **orang yang benar** biasanya berkumpul di atas ~0.7
- Skor untuk **orang yang salah** biasanya di bawah ~0.5

Ambil threshold di lembah antara dua kelompok itu. Kondisi lapangan (kamera
murah, backlight, karyawan berkacamata) menggeser angka ini — model di atas
kertas tidak bisa menebaknya untuk Anda.

### Kalibrasi Liveness

Ambang liveness ada di `app/face.py` (`check_liveness`), disalin apa adanya dari
referensi: pelebaran mulut **>3%** untuk `smile`, selisih yaw **8 piksel** untuk
`look_left`/`look_right`. Ambang piksel bergantung resolusi frame — kalau
resolusi kamera Anda jauh berbeda dari yang dipakai saat referensi dikalibrasi,
angka 8 itu perlu diuji ulang. Terlalu kecil: foto yang digoyang lolos. Terlalu
besar: karyawan harus menoleh berlebihan.
