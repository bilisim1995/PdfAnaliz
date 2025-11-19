# 🚀 PdfAnalyzerRAG - VPS Deployment Kılavuzu

Bu kılavuz, PdfAnalyzerRAG projesini production ortamında çalıştırmak için gereken tüm adımları içerir.

## 📦 Hızlı Başlangıç

### 1. Sunucuya Bağlanın

```bash
ssh user@your-vps-ip
```

### 2. Projeyi İndirin

```bash
# Git ile
git clone <repository-url> /opt/pdfanalyzerrag
cd /opt/pdfanalyzerrag

# Veya dosyaları manuel olarak yükleyin
```

### 3. Kurulumu Başlatın

```bash
chmod +x install.sh
sudo ./install.sh
```

### 4. Ortam Değişkenlerini Ayarlayın

```bash
# .env.example dosyasını kopyalayın ve düzenleyin
cp .env.example .env
nano .env
```

Gerekli değişkenleri doldurun:
- MongoDB bağlantı string'i
- Bunny.net API anahtarı
- DeepSeek API anahtarı (opsiyonel)

### 5. Systemd Service'i Kurun

```bash
# Service dosyasını kopyalayın
sudo cp pdfanalyzerrag.service /etc/systemd/system/

# Dizin yolunu düzenleyin
sudo nano /etc/systemd/system/pdfanalyzerrag.service
# WorkingDirectory ve PATH değerlerini /opt/pdfanalyzerrag olarak güncelleyin

# Service'i aktif edin
sudo systemctl daemon-reload
sudo systemctl enable pdfanalyzerrag
sudo systemctl start pdfanalyzerrag
sudo systemctl status pdfanalyzerrag
```

### 6. Nginx Reverse Proxy Kurun

```bash
# Nginx kurulumu (eğer kurulu değilse)
sudo apt-get install nginx

# Konfigürasyon dosyasını kopyalayın
sudo cp nginx-pdfanalyzerrag.conf /etc/nginx/sites-available/pdfanalyzerrag

# Domain adınızı düzenleyin
sudo nano /etc/nginx/sites-available/pdfanalyzerrag
# server_name your-domain.com; satırını kendi domain'inizle değiştirin

# Site'ı aktif edin
sudo ln -s /etc/nginx/sites-available/pdfanalyzerrag /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 7. SSL Sertifikası Kurun (Let's Encrypt)

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 🔧 Yönetim Komutları

### Service Yönetimi

```bash
# Service'i başlat
sudo systemctl start pdfanalyzerrag

# Service'i durdur
sudo systemctl stop pdfanalyzerrag

# Service'i yeniden başlat
sudo systemctl restart pdfanalyzerrag

# Service durumunu kontrol et
sudo systemctl status pdfanalyzerrag

# Logları görüntüle
sudo journalctl -u pdfanalyzerrag -f
```

### Güncelleme

```bash
cd /opt/pdfanalyzerrag
chmod +x update.sh
./update.sh
```

## 📊 Sistem Gereksinimleri

### Minimum
- **CPU**: 2 core
- **RAM**: 2GB
- **Disk**: 10GB
- **OS**: Ubuntu 20.04+ / Debian 11+

### Önerilen
- **CPU**: 4+ core
- **RAM**: 4GB+
- **Disk**: 20GB+ SSD
- **OS**: Ubuntu 22.04 LTS

## 🔒 Güvenlik

### Firewall Yapılandırması

```bash
# UFW kurulumu
sudo apt-get install ufw

# Temel kurallar
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### Dosya İzinleri

```bash
# Proje dizini izinleri
sudo chown -R www-data:www-data /opt/pdfanalyzerrag
sudo chmod -R 755 /opt/pdfanalyzerrag

# .env dosyası güvenliği
sudo chmod 600 /opt/pdfanalyzerrag/.env
```

## 🐛 Sorun Giderme

### Service Başlamıyor

```bash
# Logları kontrol edin
sudo journalctl -u pdfanalyzerrag -n 50

# Python path'i kontrol edin
which python3
which uvicorn

# Virtual environment'ı kontrol edin
ls -la /opt/pdfanalyzerrag/venv/bin/
```

### OCR Çalışmıyor

```bash
# Poppler kontrolü
which pdftoppm
pdftoppm -v

# Tesseract kontrolü
which tesseract
tesseract --list-langs

# Eksikse kurun
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-tur
```

### MongoDB Bağlantı Hatası

1. MongoDB Atlas'ta IP whitelist kontrolü
2. Connection string doğruluğu
3. Firewall kuralları
4. Network bağlantısı

### Disk Doldu

```bash
# Geçici dosyaları temizle
find /tmp -name "*.pdf" -mtime +1 -delete

# Log dosyalarını temizle
sudo journalctl --vacuum-time=7d
```

## 📈 Performans Optimizasyonu

### Uvicorn Workers

`pdfanalyzerrag.service` dosyasında worker sayısını ayarlayın:

```ini
ExecStart=/opt/pdfanalyzerrag/venv/bin/uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 4
```

Worker sayısı = (CPU cores × 2) + 1

### Nginx Caching

Nginx konfigürasyonuna cache ekleyebilirsiniz:

```nginx
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=api_cache:10m max_size=1g inactive=60m;

location /api/ {
    proxy_cache api_cache;
    proxy_cache_valid 200 10m;
    # ...
}
```

## 📝 Yedekleme

### MongoDB Yedekleme

```bash
# MongoDB yedekleme scripti oluşturun
mongodump --uri="your-connection-string" --out=/backup/mongodb-$(date +%Y%m%d)
```

### Dosya Yedekleme

```bash
# Proje dosyalarını yedekle
tar -czf /backup/pdfanalyzerrag-$(date +%Y%m%d).tar.gz /opt/pdfanalyzerrag
```

## 🔄 Otomatik Güncelleme (Opsiyonel)

Cron job ekleyin:

```bash
# Haftalık güncelleme
0 2 * * 0 cd /opt/pdfanalyzerrag && ./update.sh >> /var/log/pdfanalyzerrag-update.log 2>&1
```

## 📞 Destek

Sorun yaşarsanız:
1. Log dosyalarını kontrol edin
2. System gereksinimlerini doğrulayın
3. Tüm bağımlılıkların kurulu olduğundan emin olun

## 📄 Dosya Yapısı

```
/opt/pdfanalyzerrag/
├── api_server.py          # Ana FastAPI uygulaması
├── requirements.txt       # Python bağımlılıkları
├── install.sh            # Kurulum scripti
├── update.sh             # Güncelleme scripti
├── .env                  # Ortam değişkenleri (oluşturulmalı)
├── pdfanalyzerrag.service # Systemd service dosyası
└── nginx-pdfanalyzerrag.conf # Nginx konfigürasyonu
```

