# Migrasi Face Recognition ke Sisi Client (Mobile & Web)

Dokumen ini berisi rangkuman arsitektur dan langkah-langkah yang diperlukan jika ingin memindahkan beban pemrosesan *Face Recognition* (Face Detection & Embedding) dari server backend ke sisi client (aplikasi mobile ataupun browser web).

## Library dan Model Saat Ini (Server-Side)
Saat ini, project (backend) menggunakan library **`facenet-pytorch`** dengan spesifikasi model:
1. **MTCNN** (*Multi-task Cascaded Convolutional Networks*): Digunakan untuk deteksi wajah (*face detection*) dan ekstraksi titik koordinat wajah (*landmarks*).
2. **InceptionResnetV1** (Pretrained: `vggface2`): Digunakan untuk mengekstraksi fitur wajah menjadi sebuah deretan angka/vektor 512 dimensi (*face embeddings*).

## Apakah Client Bisa Mengirim Vektor Secara Langsung?
**Bisa.** Jika client mengirimkan data berupa vektor (deretan angka 512 dimensi), maka server backend tidak lagi membutuhkan proses `facenet-pytorch` (pengolahan gambar) pada saat melakukan verifikasi.
Proses pencocokan (*face matching*) di backend hanyalah berupa operasi matematika sederhana (yakni *Cosine Similarity* atau *dot product*) yang dilakukan murni menggunakan library `numpy` atau database.

## Syarat Mutlak: "Model Harus Sama"
Syarat paling penting agar vektor yang dikirim dari mobile/web bisa dicocokkan dengan vektor yang tersimpan di server adalah **keduanya harus dihasilkan dari model yang sama persis** (*latent space* yang sama).

### Analogi
Bayangkan model AI itu seperti **Penerjemah Bahasa**.
Tugas model *face recognition* adalah menerjemahkan **"Gambar Wajah"** menjadi **"Deretan Angka"** (disebut *Vector* atau *Embedding*).

- Model AI **"A"** (contoh: *InceptionResnetV1*) menerjemahkan wajah Anda menjadi **Bahasa Inggris** (Vektor A).
- Model AI **"B"** (contoh: *Google ML Kit* atau *MobileFaceNet*) menerjemahkan wajah yang sama menjadi **Bahasa Mandarin** (Vektor B).

Saat karyawan didaftarkan, server menggunakan Model **A**, sehingga yang tersimpan di database adalah angka-angka dalam "Bahasa Inggris". Jika client menggunakan Model **B** dan mengirimkan "Bahasa Mandarin" ke server, server tidak akan bisa memahaminya dan akan mengembalikan hasil **TIDAK COCOK**, padahal itu orang yang sama.

Oleh karena itu, sisi client wajib menggunakan "Penerjemah" yang persis sama dengan yang digunakan server (`InceptionResnetV1`).

---

## Langkah-langkah Implementasi Sisi Mobile (Android/iOS)

Berikut adalah urutan kerja yang harus dilakukan oleh tim developer mobile:

### 1. Ekspor Model dari Server ke Format Mobile
Model `InceptionResnetV1` berbasis PyTorch yang ada di server harus di-konversi ke format yang ringan dan bisa dijalankan di perangkat mobile.
Format yang didukung umumnya adalah: **TensorFlow Lite** (`.tflite`), **ONNX** (`.onnx`), atau **PyTorch Mobile** (`.ptl`).
File model ini nantinya akan disisipkan (di-embed) ke dalam source code aplikasi.

### 2. Deteksi & Potong Wajah (Face Detection) di Kamera
Gunakan *tools* yang efisien, misalnya Google ML Kit Vision, Apple Vision, atau MTCNN versi mobile untuk menemukan posisi kotak (*bounding box*) wajah dan memotong (*crop*) bagian wajah tersebut.

### 3. Pre-processing Gambar (Krusial)
Ubah ukuran gambar potongan wajah (*resize*) menjadi persis **160x160 piksel** dan lakukan normalisasi sesuai standar `InceptionResnetV1`.

### 4. Ekstraksi Vektor (Inference) & Kirim ke API
Jalankan file `.tflite` / `.onnx` dengan input gambar 160x160 piksel tersebut. Hasilnya berupa array berisi 512 angka *float*.
Lakukan L2 Normalization pada vektor tersebut, lalu kirimkan ke server backend dalam bentuk *payload* JSON (contoh: `[0.012, -0.045, 0.088, ...]`) melalui REST API.

---

## Langkah-langkah Implementasi Sisi Web (Browser)

Jika verifikasi dilakukan langsung di dalam browser tanpa mengirim gambar ke server, berikut adalah arsitektur yang direkomendasikan:

### 1. Deteksi Wajah (Face Detection)
Gunakan library javascript yang cepat untuk mendeteksi letak wajah dari stream webcam. Rekomendasi terbaik saat ini:
- `@mediapipe/face_detection` dan `@mediapipe/camera_utils` (Standar industri dari Google).

*Catatan: MediaPipe HANYA mendeteksi letak wajah (bounding box), tapi TIDAK menghasilkan vektor identitas (embedding) yang dibutuhkan backend.*

### 2. Ekspor Model ke ONNX untuk Web
Sama seperti pada mobile, tim Backend harus mengekspor model PyTorch `InceptionResnetV1` menjadi file berformat `.onnx`. File ini lalu ditaruh di folder publik/statis web Anda (misal: `/models/inception_resnet_v1.onnx`).

### 3. Potong Wajah dan Ekstraksi Vektor (Inference)
Gunakan kombinasi Canvas HTML5 dan **ONNX Runtime Web** (`onnxruntime-web`):
1. **Canvas Crop:** Dari nilai *bounding box* yang didapat dari MediaPipe, gambar potongan wajah tersebut ke sebuah `<canvas>` tersembunyi yang di-set ukurannya persis **160x160 piksel**.
2. **Ubah ke Tensor:** Ambil data piksel dari canvas tersebut dan ubah menjadi *tensor array* sambil melakukan normalisasi warna.
3. **Inference ONNX:** Load file `.onnx` menggunakan `ort.InferenceSession.create()` dan masukkan tensor wajah ke dalamnya.
4. **Hasil Akhir:** Model akan mengembalikan array 512 angka (vektor). Lakukan L2 Normalization pada vektor tersebut lalu kirimkan ke Backend.

---

## Penyimpanan Vektor di Database (SQL Server vs PostgreSQL)

Ketika proses *face recognition* berpindah ke pengiriman vektor, arsitektur database untuk penyimpanan vektor tersebut menjadi sangat penting.

### 1. Kondisi Saat Ini (SQL Server)
Pada project ini, SQL Server menyimpan vektor sebagai teks biasa berformat JSON pada tipe data `NVARCHAR(MAX)` (contoh: `"[0.012, -0.054, ...]"`).
- **Kelemahan:** SQL Server tidak mengerti matematika vektor. Untuk mencari kemiripan (pencocokan wajah), server backend (FastAPI/Python) harus men-download seluruh data JSON dari database ke RAM, mengubahnya menjadi *array numpy*, dan menghitung *dot product* secara manual. Ini sangat tidak efisien dan akan menghabiskan memori RAM seiring bertambahnya jumlah data karyawan.

### 2. Rekomendasi Migrasi (PostgreSQL dengan `pgvector`)
Sangat disarankan untuk bermigrasi ke **PostgreSQL** (minimal versi 11, direkomendasikan versi 14-16) dan menggunakan *extension* resmi bernama **`pgvector`**.

- **Tipe Data Khusus:** PostgreSQL memiliki tipe data khusus `vector(512)` yang memang dirancang untuk menyimpan hasil model AI InceptionResnetV1.
- **Pencarian Langsung di Database:** Anda tidak perlu lagi men-download data ke RAM backend Python. PostgreSQL sendiri yang akan menghitung kemiripan vektor (*Cosine Distance*) menggunakan query SQL:
  ```sql
  SELECT nik_karyawan 
  FROM karyawan_vector 
  ORDER BY face_vector <=> '[0.012, -0.054, ...]' 
  LIMIT 1;
  ```
- **Kecepatan Tinggi (Indexing):** Pada `pgvector` versi 0.5.0+, Anda bisa menambahkan indeks **HNSW** (Hierarchical Navigable Small World). Indeks ini memungkinkan pencarian kemiripan wajah dari ratusan ribu data selesai hanya dalam hitungan milidetik tanpa membebani server backend sama sekali.

*Catatan: Cara termudah untuk menjalankan PostgreSQL ber-vector di lokal (Windows) adalah menggunakan Docker:*
```bash
docker run --name pg-vector -e POSTGRES_PASSWORD=password_bebas -p 5432:5432 -d pgvector/pgvector:pg16
```
*(Jangan lupa jalankan query `CREATE EXTENSION vector;` sebelum membuat tabel).*

---
**Kesimpulan Akhir:** 
Mengalihkan proses pengolahan gambar (AI) ke sisi client (baik Mobile maupun Web) akan sangat menghemat biaya CPU server. Namun, untuk membuat sistem ini bekerja optimal dalam skala besar, Backend juga harus di-upgrade dengan memindahkan beban perhitungan matematis vektor (*similarity search*) dari Python ke dalam database menggunakan **PostgreSQL (pgvector)**. Kombinasi ini akan menghasilkan arsitektur sistem *face recognition* yang sangat tangguh, murah, dan siap berskala besar.
