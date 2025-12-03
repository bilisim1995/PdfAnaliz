#!/usr/bin/env python3
"""
Telegram Bot Webhook Setup Script
Bu script Telegram bot webhook'unu ayarlar.
"""
import os
import sys
import requests
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

def set_webhook():
    """Telegram bot webhook'unu ayarlar"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN environment variable bulunamadı!")
        print("   .env dosyasına TELEGRAM_BOT_TOKEN=... ekleyin")
        sys.exit(1)
    
    # Webhook URL'ini kullanıcıdan al
    print("=" * 60)
    print("Telegram Bot Webhook Kurulumu")
    print("=" * 60)
    print("\nAPI sunucunuzun public URL'ini girin.")
    print("Örnek: https://scrapers.mevzuatgpt.org")
    print("\nNot: Eğer HTTPS yoksa, Telegram webhook kabul etmez.")
    print("     Nginx reverse proxy veya Let's Encrypt kullanmanız gerekir.")
    print()
    
    webhook_url = input("Webhook URL (örn: https://scrapers.mevzuatgpt.org): ").strip()
    
    if not webhook_url:
        print("❌ Webhook URL boş olamaz!")
        sys.exit(1)
    
    if not webhook_url.startswith("https://"):
        print("⚠️  UYARI: Telegram sadece HTTPS webhook'ları kabul eder!")
        print("   Devam etmek istiyor musunuz? (y/n): ", end="")
        if input().lower() != 'y':
            sys.exit(0)
    
    # Telegram Bot API'ye webhook set et
    url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    data = {"url": webhook_url}
    
    print(f"\n📡 Webhook ayarlanıyor...")
    print(f"   URL: {webhook_url}")
    
    try:
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        
        if result.get("ok"):
            print("✅ Webhook başarıyla ayarlandı!")
            print(f"   Webhook bilgisi: {result.get('description', 'N/A')}")
            
            # Webhook bilgisini kontrol et
            info_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
            info_response = requests.get(info_url, timeout=10)
            info_result = info_response.json()
            
            if info_result.get("ok"):
                webhook_info = info_result.get("result", {})
                print(f"\n📊 Webhook Bilgileri:")
                print(f"   URL: {webhook_info.get('url', 'N/A')}")
                print(f"   Bekleyen güncelleme: {webhook_info.get('pending_update_count', 0)}")
                if webhook_info.get('last_error_date'):
                    print(f"   ⚠️  Son hata: {webhook_info.get('last_error_message', 'N/A')}")
        else:
            print(f"❌ Webhook ayarlanamadı!")
            print(f"   Hata: {result.get('description', 'Bilinmeyen hata')}")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Webhook ayarlama hatası: {str(e)}")
        sys.exit(1)


def delete_webhook():
    """Telegram bot webhook'unu siler (polling için)"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN environment variable bulunamadı!")
        sys.exit(1)
    
    url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"
    
    print("📡 Webhook siliniyor...")
    try:
        response = requests.post(url, timeout=10)
        result = response.json()
        
        if result.get("ok"):
            print("✅ Webhook başarıyla silindi!")
        else:
            print(f"❌ Webhook silinemedi: {result.get('description', 'Bilinmeyen hata')}")
    except Exception as e:
        print(f"❌ Webhook silme hatası: {str(e)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        delete_webhook()
    else:
        set_webhook()

