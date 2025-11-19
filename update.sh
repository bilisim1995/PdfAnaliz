#!/bin/bash
# PdfAnalyzerRAG Güncelleme Scripti

set -e

echo "=========================================="
echo "PdfAnalyzerRAG Güncelleme Başlatılıyor..."
echo "=========================================="

# Proje dizinine git
cd "$(dirname "$0")"

# Git kullanıyorsanız
if [ -d ".git" ]; then
    echo "📥 Git'ten güncellemeler çekiliyor..."
    git pull
fi

# Virtual environment'ı aktif et
if [ -d "venv" ]; then
    echo "🔌 Virtual environment aktif ediliyor..."
    source venv/bin/activate
else
    echo "❌ Virtual environment bulunamadı! Önce install.sh çalıştırın."
    exit 1
fi

# pip güncelle
echo "⬆️ pip güncelleniyor..."
pip install --upgrade pip setuptools wheel

# Python paketlerini güncelle
echo "📦 Python paketleri güncelleniyor..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --upgrade
    echo "✅ Python paketleri güncellendi"
else
    echo "⚠️ requirements.txt bulunamadı!"
    exit 1
fi

# Playwright tarayıcılarını güncelle
echo "🌐 Playwright tarayıcıları güncelleniyor..."
playwright install chromium
playwright install-deps chromium

# Systemd service'i yeniden başlat
if systemctl is-active --quiet pdfanalyzerrag; then
    echo "🔄 Service yeniden başlatılıyor..."
    sudo systemctl restart pdfanalyzerrag
    echo "✅ Service yeniden başlatıldı"
else
    echo "ℹ️ Service çalışmıyor, başlatılmadı"
fi

echo ""
echo "=========================================="
echo "✅ Güncelleme tamamlandı!"
echo "=========================================="
echo ""
echo "📊 Service durumu:"
sudo systemctl status pdfanalyzerrag --no-pager -l
echo ""

