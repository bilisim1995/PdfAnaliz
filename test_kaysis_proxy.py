#!/usr/bin/env python3
"""
KAYSİS Proxy Bağlantı Test Scripti
MongoDB'den proxy bilgilerini çeker ve KAYSİS sitesine bağlantıyı test eder.
"""

import os
import sys
import requests
from typing import Optional, Dict
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError


def _get_mongodb_client():
    """MongoDB bağlantısı oluşturur"""
    try:
        connection_string = os.getenv("MONGODB_CONNECTION_STRING")
        if not connection_string:
            print("❌ MONGODB_CONNECTION_STRING environment variable bulunamadı!")
            return None
        client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"❌ MongoDB bağlantı hatası: {str(e)}")
        return None


def get_proxy_from_db() -> Optional[Dict[str, str]]:
    """
    MongoDB'den aktif proxy bilgilerini çeker.
    Returns: {'http': 'http://user:pass@host:port', 'https': 'http://user:pass@host:port'} veya None
    """
    try:
        client = _get_mongodb_client()
        if not client:
            return None
        
        database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
        db = client[database_name]
        col = db["proxies"]
        
        # Aktif proxy'yi bul (is_active=True olan ilk kayıt)
        proxy_doc = col.find_one({"is_active": True}, sort=[("created_at", -1)])
        client.close()
        
        if not proxy_doc:
            return None
        
        host = proxy_doc.get("host", "").strip()
        port = proxy_doc.get("port", "").strip()
        username = proxy_doc.get("username", "").strip()
        password = proxy_doc.get("password", "").strip()
        
        if not host or not port:
            return None
        
        # Proxy URL'ini oluştur
        if username and password:
            proxy_auth = f"{username}:{password}"
            proxy_url = f"{proxy_auth}@{host}:{port}"
        else:
            proxy_url = f"{host}:{port}"
        
        return {
            'http': f'http://{proxy_url}',
            'https': f'http://{proxy_url}'
        }
    except Exception as e:
        print(f"⚠️ Proxy bilgisi çekilemedi: {str(e)}")
        return None


def test_kaysis_connection(detsis: str = "22620739") -> bool:
    """
    KAYSİS sitesine proxy ile bağlantıyı test eder.
    
    Args:
        detsis: DETSIS numarası (varsayılan: 22620739 - SGK)
    
    Returns:
        bool: Bağlantı başarılı ise True, değilse False
    """
    url = f"https://kms.kaysis.gov.tr/Home/Kurum/{detsis}"
    
    print("=" * 80)
    print("🔍 KAYSİS Proxy Bağlantı Testi")
    print("=" * 80)
    print(f"📡 Test URL: {url}")
    print()
    
    # Proxy bilgilerini çek
    print("🔐 Proxy bilgileri MongoDB'den çekiliyor...")
    proxies = get_proxy_from_db()
    
    if not proxies:
        print("❌ Proxy bulunamadı!")
        print("   MongoDB'de aktif (is_active=True) bir proxy kaydı olmalı.")
        return False
    
    # Proxy bilgilerini göster (şifre hariç)
    http_proxy = proxies.get('http', '')
    if '@' in http_proxy:
        proxy_display = http_proxy.split('@')[1] if '@' in http_proxy else http_proxy
    else:
        proxy_display = http_proxy.replace('http://', '')
    
    print(f"✅ Proxy bulundu: {proxy_display}")
    print()
    
    # Bağlantı testi
    print("🌐 KAYSİS sitesine bağlanılıyor...")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=30, proxies=proxies)
        
        if response.status_code == 200:
            print("✅ Bağlantı başarılı!")
            print(f"   HTTP Status: {response.status_code}")
            print(f"   Response Size: {len(response.content)} bytes")
            print()
            print("=" * 80)
            return True
        else:
            print(f"⚠️ Bağlantı reddedildi!")
            print(f"   HTTP Status: {response.status_code}")
            print(f"   Response: {response.text[:200] if response.text else 'Boş yanıt'}")
            print()
            print("=" * 80)
            return False
            
    except requests.exceptions.ProxyError as e:
        print(f"❌ Proxy hatası: {str(e)}")
        print("   Proxy sunucusuna bağlanılamadı veya proxy erişimi reddedildi.")
        print()
        print("=" * 80)
        return False
    except requests.exceptions.Timeout:
        print("❌ Zaman aşımı hatası!")
        print("   Bağlantı 30 saniye içinde tamamlanamadı.")
        print()
        print("=" * 80)
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Bağlantı hatası: {str(e)}")
        print("   KAYSİS sitesine erişilemedi.")
        print()
        print("=" * 80)
        return False
    except Exception as e:
        print(f"❌ Beklenmeyen hata: {str(e)}")
        print()
        print("=" * 80)
        return False


def main():
    """Ana fonksiyon"""
    # DETSIS numarasını argüman olarak al (opsiyonel)
    detsis = sys.argv[1] if len(sys.argv) > 1 else "22620739"
    
    print()
    success = test_kaysis_connection(detsis)
    
    if success:
        print("✅ TEST SONUCU: Bağlantı başarılı")
        sys.exit(0)
    else:
        print("❌ TEST SONUCU: Bağlantı reddedildi veya hata oluştu")
        sys.exit(1)


if __name__ == "__main__":
    main()

