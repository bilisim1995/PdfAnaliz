#!/usr/bin/env python3
"""
KAYSİS Proxy Bağlantı Test Scripti
Sabit proxy bilgilerini kullanarak KAYSİS sitesine bağlantıyı test eder.
"""

import sys
import requests
from typing import Dict

# Sabit proxy bilgileri
PROXY_HOST = "geo.iproyal.com"
PROXY_PORT = "12321"
PROXY_USERNAME = "tU23j0va4T4HjIqh"
PROXY_PASSWORD = "fA0UiMSvxNJiF9B6_country-tr"


def get_proxy() -> Dict[str, str]:
    """
    Sabit proxy bilgilerini döner.
    Returns: {'http': 'http://user:pass@host:port', 'https': 'http://user:pass@host:port'}
    """
    proxy_auth = f"{PROXY_USERNAME}:{PROXY_PASSWORD}"
    proxy_url = f"{proxy_auth}@{PROXY_HOST}:{PROXY_PORT}"
    
    return {
        'http': f'http://{proxy_url}',
        'https': f'http://{proxy_url}'
    }


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
    
    # Sabit proxy bilgilerini kullan
    proxies = get_proxy()
    print("🔐 Sabit proxy bilgileri kullanılıyor.")
    
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

