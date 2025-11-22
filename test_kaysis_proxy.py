#!/usr/bin/env python3
"""
KAYSİS Proxy Bağlantı Test Scripti
curl_cffi kullanarak Chrome tarayıcısını taklit eder ve WAF engellemelerini aşar.
Sabit proxy bilgilerini kullanarak KAYSİS sitesine bağlantıyı test eder.
"""

import sys
import json
from typing import Dict, Optional

# curl_cffi import kontrolü
try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("❌ curl_cffi modülü bulunamadı!")
    print("   Lütfen şu komutu çalıştırın: pip install curl-cffi")
    sys.exit(1)

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


def check_proxy_ip(proxies: Dict[str, str]) -> Optional[Dict[str, str]]:
    """
    Proxy üzerinden IP adresini kontrol eder ve lokasyon bilgisini döner.
    
    Args:
        proxies: Proxy bilgileri
    
    Returns:
        IP ve lokasyon bilgileri veya None
    """
    print("🌍 Proxy IP adresi kontrol ediliyor...")
    try:
        # IP adresini al
        ip_response = requests.get(
            'https://ipv4.icanhazip.com',
            proxies=proxies,
            timeout=10,
            impersonate="chrome110"  # Chrome 110 parmak izi
        )
        ip_address = ip_response.text.strip()
        
        # IP lokasyon bilgisini al
        try:
            geo_response = requests.get(
                f'http://ip-api.com/json/{ip_address}?fields=status,country,countryCode,city,query',
                proxies=proxies,
                timeout=10,
                impersonate="chrome110"
            )
            geo_data = geo_response.json()
            
            if geo_data.get('status') == 'success':
                country = geo_data.get('country', 'Bilinmiyor')
                country_code = geo_data.get('countryCode', 'Bilinmiyor')
                city = geo_data.get('city', 'Bilinmiyor')
                
                print(f"   IP Adresi: {ip_address}")
                print(f"   Ülke: {country} ({country_code})")
                print(f"   Şehir: {city}")
                
                # Türkiye kontrolü
                if country_code == 'TR':
                    print("   ✅ Proxy Türkiye IP'si kullanıyor!")
                    return {
                        'ip': ip_address,
                        'country': country,
                        'country_code': country_code,
                        'city': city,
                        'is_turkey': True
                    }
                else:
                    print(f"   ⚠️ Proxy Türkiye IP'si kullanmıyor! ({country_code})")
                    return {
                        'ip': ip_address,
                        'country': country,
                        'country_code': country_code,
                        'city': city,
                        'is_turkey': False
                    }
            else:
                print(f"   IP Adresi: {ip_address}")
                print("   ⚠️ Lokasyon bilgisi alınamadı")
                return {'ip': ip_address}
        except Exception as e:
            print(f"   IP Adresi: {ip_address}")
            print(f"   ⚠️ Lokasyon bilgisi alınamadı: {str(e)}")
            return {'ip': ip_address}
            
    except Exception as e:
        print(f"   ❌ IP kontrolü başarısız: {str(e)}")
        return None


def test_kaysis_connection(detsis: str = "22620739") -> bool:
    """
    KAYSİS sitesine proxy ile bağlantıyı test eder.
    curl_cffi kullanarak Chrome tarayıcısını taklit eder ve WAF engellemelerini aşar.
    
    Args:
        detsis: DETSIS numarası (varsayılan: 22620739 - SGK)
    
    Returns:
        bool: Bağlantı başarılı ise True, değilse False
    """
    url = f"https://kms.kaysis.gov.tr/Home/Kurum/{detsis}"
    
    print("=" * 80)
    print("🔍 KAYSİS Proxy Bağlantı Testi (curl_cffi ile Chrome Taklidi)")
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
    
    print(f"✅ Proxy: {proxy_display}")
    print()
    
    # IP kontrolü
    ip_info = check_proxy_ip(proxies)
    print()
    
    # Bağlantı testi - Chrome tarayıcısını taklit et
    print("🌐 KAYSİS sitesine bağlanılıyor (Chrome taklidi ile)...")
    try:
        # Gerçek bir Chrome tarayıcısının gönderdiği tüm header'lar
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.google.com/',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }
        
        # curl_cffi ile Chrome 110 parmak izini kullan
        response = requests.get(
            url,
            headers=headers,
            proxies=proxies,
            timeout=30,
            impersonate="chrome110",  # Chrome 110 TLS fingerprint
            verify=True
        )
        
        if response.status_code == 200:
            print("✅ Bağlantı başarılı!")
            print(f"   HTTP Status: {response.status_code}")
            print(f"   Response Size: {len(response.content)} bytes")
            
            # HTML içeriğinde başarılı yükleme işaretleri kontrol et
            content = response.text.lower()
            if 'accordion' in content or 'panel' in content or 'kurum' in content:
                print("   ✅ Sayfa içeriği başarıyla yüklendi (KAYSİS yapısı tespit edildi)")
            else:
                print("   ⚠️ Sayfa yüklendi ancak beklenen içerik bulunamadı")
            
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
        import traceback
        traceback.print_exc()
        print()
        print("=" * 80)
        return False


def main():
    """Ana fonksiyon"""
    # curl_cffi kontrolü
    if not CURL_CFFI_AVAILABLE:
        print("❌ curl_cffi modülü bulunamadı!")
        print("   Lütfen şu komutu çalıştırın: pip install curl-cffi")
        sys.exit(1)
    
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
