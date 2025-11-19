#!/bin/bash
# VPS Kurulum Scripti - PdfAnalyzerRAG
# Ubuntu/Debian için sistem paketlerini kurar

set -e  # Hata durumunda dur

echo "=========================================="
echo "PdfAnalyzerRAG VPS Kurulum Başlatılıyor..."
echo "=========================================="

# Sistem güncellemesi
echo "📦 Sistem paketleri güncelleniyor..."
sudo apt-get update
sudo apt-get upgrade -y

# Python ve pip kurulumu
echo "🐍 Python ve pip kurulumu kontrol ediliyor..."
if ! command -v python3 &> /dev/null; then
    echo "Python3 kuruluyor..."
    sudo apt-get install -y python3 python3-pip python3-venv
fi

# Poppler (PDF2Image için gerekli)
echo "📄 Poppler kuruluyor..."
sudo apt-get install -y poppler-utils

# Tesseract OCR ve Türkçe dil paketi
echo "👁️ Tesseract OCR kuruluyor..."
sudo apt-get install -y tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng

# Playwright için gerekli sistem paketleri
echo "🎭 Playwright sistem bağımlılıkları kuruluyor..."
sudo apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1

# Diğer gerekli paketler
echo "🔧 Diğer gerekli paketler kuruluyor..."
sudo apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    git \
    curl \
    wget

# Python virtual environment oluştur
echo "📁 Python virtual environment oluşturuluyor..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment oluşturuldu"
else
    echo "ℹ️ Virtual environment zaten mevcut"
fi

# Virtual environment'ı aktif et
echo "🔌 Virtual environment aktif ediliyor..."
source venv/bin/activate

# pip güncelle
echo "⬆️ pip güncelleniyor..."
pip install --upgrade pip setuptools wheel

# Python paketlerini kur
echo "📦 Python paketleri kuruluyor..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo "✅ Python paketleri kuruldu"
else
    echo "⚠️ requirements.txt bulunamadı!"
    exit 1
fi

# Playwright tarayıcılarını kur
echo "🌐 Playwright tarayıcıları kuruluyor..."
playwright install chromium
playwright install-deps chromium

echo ""
echo "=========================================="
echo "✅ Kurulum tamamlandı!"
echo "=========================================="
echo ""
echo "📝 Sonraki adımlar:"
echo "1. .env dosyasını oluşturun ve gerekli değişkenleri ekleyin"
echo "2. Sunucuyu başlatmak için:"
echo "   source venv/bin/activate"
echo "   uvicorn api_server:app --host 0.0.0.0 --port 8000"
echo ""
echo "🔒 Güvenlik için:"
echo "- Firewall kurallarını yapılandırın (ufw veya iptables)"
echo "- SSL/TLS sertifikası ekleyin (Let's Encrypt önerilir)"
echo "- Reverse proxy kullanın (Nginx önerilir)"
echo ""

