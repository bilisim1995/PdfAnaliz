# PdfAnalyzerRAG - VPS Kurulum Kılavuzu

Bu kılavuz, PdfAnalyzerRAG projesini bir VPS (Virtual Private Server) üzerine kurmak için adım adım talimatlar içerir.

## 📋 Gereksinimler

- Ubuntu 20.04+ veya Debian 11+ (diğer Linux dağıtımları için paket isimleri farklı olabilir)
- Root veya sudo yetkisi
- En az 2GB RAM (4GB+ önerilir)
- En az 10GB disk alanı
- Python 3.11 veya üzeri

## 🚀 Hızlı Kurulum

### 1. Projeyi İndirin

```bash
# Git ile klonlayın
git clone <repository-url> PdfAnalyzerRAG
cd PdfAnalyzerRAG

# Veya dosyaları manuel olarak yükleyin
```

### 2. Kurulum Scriptini Çalıştırın

```bash
# Script'e çalıştırma izni verin
chmod +x install.sh

# Kurulumu başlatın
./install.sh
```

Script otomatik olarak:
- Sistem paketlerini güncelleyecek
- Python, Poppler, Tesseract OCR kurulumunu yapacak
- Playwright bağımlılıklarını kuracak
- Python virtual environment oluşturacak
- Tüm Python paketlerini kuracak

### 3. Ortam Değişkenlerini Yapılandırın

`.env` dosyası oluşturun:

```bash
cp .env.example .env  # Eğer örnek dosya varsa
# Veya manuel olarak oluşturun
nano .env
```

Gerekli değişkenler:

```env
# MongoDB Atlas Configuration
MONGODB_CONNECTION_STRING=mongodb+srv://user:password@cluster.mongodb.net/?retryWrites=true&w=majority
MONGODB_DATABASE=mevzuatgpt
MONGODB_METADATA_COLLECTION=metadata
MONGODB_CONTENT_COLLECTION=content

# Bunny.net Storage Configuration
BUNNY_STORAGE_API_KEY=your-api-key
BUNNY_STORAGE_ZONE=mevzuatgpt
BUNNY_STORAGE_REGION=storage.bunnycdn.com
BUNNY_STORAGE_ENDPOINT=https://cdn.mevzuatgpt.org
BUNNY_STORAGE_FOLDER=portal

# DeepSeek API (Opsiyonel)
DEEPSEEK_API_KEY=your-deepseek-api-key

# MevzuatGPT API Configuration
# config.json dosyasında da yapılandırılabilir
```

### 4. Sunucuyu Başlatın

#### Geliştirme Modu

```bash
source venv/bin/activate
uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
```

#### Production Modu (systemd service)

`/etc/systemd/system/pdfanalyzerrag.service` dosyası oluşturun:

```ini
[Unit]
Description=PdfAnalyzerRAG API Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/PdfAnalyzerRAG
Environment="PATH=/path/to/PdfAnalyzerRAG/venv/bin"
ExecStart=/path/to/PdfAnalyzerRAG/venv/bin/uvicorn api_server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Service'i başlatın:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pdfanalyzerrag
sudo systemctl start pdfanalyzerrag
sudo systemctl status pdfanalyzerrag
```

## 🔒 Güvenlik Yapılandırması

### Firewall Kuralları

```bash
# UFW kullanarak
sudo ufw allow 8000/tcp
sudo ufw enable

# Veya sadece belirli IP'lerden erişim
sudo ufw allow from YOUR_IP_ADDRESS to any port 8000
```

### Nginx Reverse Proxy (Önerilir)

`/etc/nginx/sites-available/pdfanalyzerrag` dosyası:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Nginx'i aktif edin:

```bash
sudo ln -s /etc/nginx/sites-available/pdfanalyzerrag /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### SSL/TLS Sertifikası (Let's Encrypt)

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 📊 Sistem İzleme

### Logları Görüntüleme

```bash
# Systemd service logları
sudo journalctl -u pdfanalyzerrag -f

# Manuel çalıştırma durumunda
tail -f /path/to/logs/app.log
```

### Disk Kullanımı

```bash
# Geçici dosyalar için disk kullanımını kontrol edin
du -sh /tmp/*
df -h
```

### Performans İzleme

```bash
# CPU ve RAM kullanımı
htop

# Process izleme
ps aux | grep uvicorn
```

## 🔧 Sorun Giderme

### Poppler Bulunamadı

```bash
sudo apt-get install poppler-utils
export PATH=$PATH:/usr/bin
```

### Tesseract OCR Çalışmıyor

```bash
# Türkçe dil paketini kontrol edin
tesseract --list-langs

# Eksikse kurun
sudo apt-get install tesseract-ocr-tur
```

### Playwright Tarayıcıları Bulunamadı

```bash
source venv/bin/activate
playwright install chromium
playwright install-deps chromium
```

### MongoDB Bağlantı Hatası

- MongoDB Atlas'ta IP whitelist'e VPS IP'nizi ekleyin
- Connection string'i kontrol edin
- Firewall kurallarını kontrol edin

### Port Zaten Kullanılıyor

```bash
# Port'u kullanan process'i bulun
sudo lsof -i :8000

# Process'i sonlandırın
sudo kill -9 <PID>
```

## 📝 Güncelleme

```bash
cd /path/to/PdfAnalyzerRAG
git pull  # Eğer git kullanıyorsanız
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart pdfanalyzerrag
```

## 🧹 Temizlik

Geçici dosyaları temizlemek için:

```bash
# /tmp klasöründeki eski PDF'leri temizle
find /tmp -name "*.pdf" -mtime +1 -delete

# Log dosyalarını temizle
find /path/to/logs -name "*.log" -mtime +7 -delete
```

## 📞 Destek

Sorun yaşarsanız:
1. Log dosyalarını kontrol edin
2. Sistem gereksinimlerini kontrol edin
3. Tüm bağımlılıkların kurulu olduğundan emin olun

## 📄 Lisans

[Lisans bilgilerinizi buraya ekleyin]

