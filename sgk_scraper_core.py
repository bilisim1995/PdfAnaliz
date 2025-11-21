"""
SGK Scraper Core Module - UI bağımsız scraping fonksiyonları
"""
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
import re
import json
import unicodedata
import os
from pymongo import MongoClient
from sgk_scraper import (
    normalize_text,
    is_title_similar,
    check_if_document_exists
)

# Global cache for uploaded documents
uploaded_documents_cache = []


def turkish_title(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize('NFC', text)
    s = s.replace("i\u0307", "i")
    tmp = s.replace('I', 'ı').replace('İ', 'i').lower()
    # Heuristic düzeltmeler
    tmp = re.sub(r"\bsayili\b", "sayılı", tmp)
    tmp = re.sub(r"\bsigortalilik\b", "sigortalılık", tmp)
    tmp = re.sub(r"\bsigortali\b", "sigortalı", tmp)
    tmp = re.sub(r"\bişlemleri\b", "işlemleri", tmp)
    words = re.split(r'(\s+)', tmp)
    titled_parts = []
    for w in words:
        if not w or w.isspace():
            titled_parts.append(w)
            continue
        first = w[0]
        rest = w[1:]
        if first == 'i':
            first_up = 'İ'
        elif first == 'ı':
            first_up = 'I'
        else:
            first_up = first.upper()
        titled_parts.append(first_up + rest)
    return ''.join(titled_parts)


# ============================================================================
# Proxy Yardımcı Fonksiyonları
# ============================================================================

def _get_mongodb_client():
    """MongoDB bağlantısı oluşturur"""
    try:
        connection_string = os.getenv("MONGODB_CONNECTION_STRING")
        if not connection_string:
            return None
        client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        return client
    except Exception:
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


def turkish_sentence_case(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize('NFC', text)
    s = s.replace("i\u0307", "i")
    s = s.replace('I', 'ı').replace('İ', 'i').lower()
    s = s.strip()
    if not s:
        return s
    first = s[0]
    rest = s[1:]
    if first == 'i':
        first_up = 'İ'
    elif first == 'ı':
        first_up = 'I'
    else:
        first_up = first.upper()
    return first_up + rest


def scrape_sgk_mevzuat(url: str = "https://kms.kaysis.gov.tr/Home/Kurum/22620739") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    KAYSİS sitesinden mevzuatları tarar ve API ile karşılaştırır
    Args:
        url: Taranacak kurum URL'i
    Returns: (all_sections, stats)
    """
    print(f"🔍 Mevzuat Tarama Başlatılıyor...")
    print(f"📡 Site: {url}")
    
    # Config'den bilgileri yükle
    api_base_url = ''
    access_token = ''
    
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            api_base_url = config.get('api_base_url', '')
            email = config.get('admin_email', '')
            password = config.get('admin_password', '')
            
            if api_base_url and email and password:
                print("🔐 API'ye bağlanılıyor...")
                # Login endpoint
                login_url = f"{api_base_url.rstrip('/')}/api/auth/login"
                login_data = {"email": email, "password": password}
                
                # Proxy bilgilerini çek
                proxies = get_proxy_from_db()
                
                login_response = requests.post(
                    login_url,
                    headers={"Content-Type": "application/json"},
                    json=login_data,
                    timeout=60,
                    proxies=proxies
                )
                
                if login_response.status_code == 200:
                    result = login_response.json()
                    access_token = result.get("access_token", "")
                    print("✅ API'ye bağlantı başarılı!")
                else:
                    print(f"⚠️ API'ye bağlanılamadı: HTTP {login_response.status_code}")
                    print("ℹ️ Mevzuat karşılaştırması yapılamayacak.")
            else:
                print("⚠️ Config eksik bilgiler içeriyor. Mevzuat karşılaştırması yapılamayacak.")
    except Exception as e:
        print(f"⚠️ Config yüklenemedi: {str(e)}")
        print("ℹ️ Mevzuat karşılaştırması yapılamayacak.")
    
    print("\n🌐 Siteye bağlanılıyor...")
    
    # MongoDB'den güncel proxy bilgilerini çek
    proxies = get_proxy_from_db()
    if proxies:
        print("🔐 Proxy kullanılıyor...")
    else:
        print("⚠️ Proxy bulunamadı, direkt bağlantı deneniyor...")
    
    try:
        # Siteye istek gönder
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30, proxies=proxies)
        
        if response.status_code != 200:
            print(f"❌ Siteye erişilemedi: HTTP {response.status_code}")
            return [], {}
        
        # HTML'i parse et
        soup = BeautifulSoup(response.content, 'html.parser')
        print("✅ Site başarıyla yüklendi!")
        
        print("📋 Accordion yapısı aranıyor...")
        
        # accordion2 div'ini bul
        accordion_div = soup.find('div', {'id': 'accordion2', 'class': 'panel-group'})
        
        if not accordion_div:
            print("⚠️ accordion2 div'i bulunamadı!")
            return [], {}
        
        print("✅ Accordion yapısı bulundu!")
        print("🔍 Başlıklar ve içerikler çekiliyor...")
        
        # Accordion içindeki tüm panel'leri bul
        panels = accordion_div.find_all('div', class_='panel')
        
        if not panels:
            panels = accordion_div.find_all(['div'], class_=lambda x: x and 'panel' in str(x).lower())
        
        all_sections = []
        
        if panels:
            for panel in panels:
                # Panel başlığını bul
                panel_heading = panel.find('div', class_=lambda x: x and 'heading' in str(x).lower())
                if not panel_heading:
                    panel_heading = panel.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'span'], class_=lambda x: x and ('heading' in str(x).lower() or 'title' in str(x).lower()))
                
                heading_text = ""
                if panel_heading:
                    # Başlık içindeki badge/span sayacılarını çıkar
                    try:
                        for badge in panel_heading.find_all('span', class_=lambda c: c and 'badge' in c):
                            badge.decompose()
                    except Exception:
                        pass
                    heading_text = panel_heading.get_text(strip=True)
                    # Sonda kalan sayıları da temizle (örn: "Kanunlar4" -> "Kanunlar")
                    heading_text = re.sub(r"\d+\s*$", "", heading_text).strip()
                
                # Panel içindeki linkleri ve içerikleri bul
                panel_body = panel.find('div', class_=lambda x: x and 'body' in str(x).lower())
                if not panel_body:
                    panel_body = panel
                
                # Panel içindeki tüm linkleri bul
                links_in_panel = panel_body.find_all('a', href=True)
                
                items_in_section = []
                for link in links_in_panel:
                    link_href = link.get('href', '')
                    
                    # Link içinde badge span'i varsa atla
                    if link.find('span', class_='badge'):
                        continue
                    
                    # Link metnini al
                    link_text = link.get_text(strip=True)
                    
                    # Boş veya çok kısa metinleri atla
                    if not link_text or len(link_text.strip()) < 10:
                        continue
                    
                    # Sadece sayılardan oluşan metinleri atla
                    if re.match(r'^[\d\s.,]+$', link_text.strip()):
                        continue
                    
                    # Link URL'ini tamamla
                    if link_href.startswith('http'):
                        full_url = link_href
                    elif link_href.startswith('/'):
                        full_url = f"https://kms.kaysis.gov.tr{link_href}"
                    else:
                        full_url = f"{url}{link_href}"
                    
                    # Sadece /Home/Goster/ ile başlayan linkleri al
                    if not full_url or '/Home/Goster/' not in full_url:
                        continue
                    
                    # Metni formatla: yalnızca başlığın ilk harfi büyük, diğerleri küçük (Türkçe)
                    formatted_text = turkish_sentence_case(link_text)
                    formatted_text = re.sub(r'\d+$', '', formatted_text).strip()
                    original_text = link_text.strip()
                    
                    items_in_section.append({
                        'baslik': formatted_text,
                        'baslik_original': original_text,
                        'link': full_url
                    })
                
                if heading_text or items_in_section:
                    all_sections.append({
                        'section_title': heading_text or 'Başlıksız Bölüm',
                        'items': items_in_section
                    })
        
        print(f"✅ {len(all_sections)} bölüm bulundu")
        total_items = sum(len(section['items']) for section in all_sections)
        print(f"📊 Toplam {total_items} mevzuat bulundu")
        
        # API'den yüklü mevzuatları çek
        global uploaded_documents_cache
        uploaded_documents = []
        if api_base_url and access_token:
            print("\n📡 API'den yüklü mevzuatlar kontrol ediliyor...")
            from sgk_scraper import get_uploaded_documents
            uploaded_documents = get_uploaded_documents(api_base_url, access_token, use_streamlit=False)
            uploaded_documents_cache = uploaded_documents  # Cache'e kaydet
            if uploaded_documents:
                print(f"✅ API'den {len(uploaded_documents)} yüklü mevzuat bulundu")
            else:
                print("ℹ️ API'de yüklü mevzuat bulunamadı veya bağlantı kurulamadı")
        
        # Karşılaştırma ve istatistikler
        stats = {
            'total_sections': len(all_sections),
            'total_items': total_items,
            'uploaded_documents_count': len(uploaded_documents),
            'sections_stats': []
        }
        
        for section in all_sections:
            section_title = section['section_title']
            items = section['items']
            
            uploaded_count = 0
            not_uploaded_count = 0
            
            for item in items:
                is_uploaded = False
                if uploaded_documents:
                    is_uploaded = check_if_document_exists(item['baslik'], uploaded_documents)
                    if not is_uploaded and item.get('baslik_original'):
                        is_uploaded = check_if_document_exists(item['baslik_original'], uploaded_documents)
                
                if is_uploaded:
                    uploaded_count += 1
                else:
                    not_uploaded_count += 1
            
            stats['sections_stats'].append({
                'section_title': section_title,
                'total': len(items),
                'uploaded': uploaded_count,
                'not_uploaded': not_uploaded_count
            })
        
        return all_sections, stats
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Bağlantı hatası: {str(e)}")
        return [], {}
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        return [], {}


def print_results_to_console(all_sections: List[Dict[str, Any]], stats: Dict[str, Any]):
    """Sonuçları konsola yazdırır"""
    print("\n" + "="*80)
    print("📋 BULUNAN MEVZUATLAR")
    print("="*80)
    
    if not all_sections:
        print("⚠️ Mevzuat bulunamadı!")
        return
    
    print(f"\n📊 Toplam {stats.get('total_sections', 0)} başlık altında {stats.get('total_items', 0)} mevzuat bulundu")
    print(f"📦 API'de {stats.get('uploaded_documents_count', 0)} yüklü mevzuat var\n")
    
    for section in all_sections:
        section_title = section['section_title']
        items = section['items']
        
        if not items:
            continue
        
        # İlgili istatistikleri bul
        section_stat = next(
            (s for s in stats.get('sections_stats', []) if s['section_title'] == section_title),
            {'total': len(items), 'uploaded': 0, 'not_uploaded': len(items)}
        )
        
        print(f"\n{'='*80}")
        print(f"📂 {section_title} Toplam:{section_stat['total']}")
        print(f"   ({section_stat['uploaded']} adet - yüklü ✅  - {section_stat['not_uploaded']} adet yüklü değil ⏳ )")
        print(f"{'='*80}")
        
        for i, item in enumerate(items, 1):
            # Mevzuatın yüklü olup olmadığını kontrol et
            is_uploaded = False
            if stats.get('uploaded_documents_count', 0) > 0:
                global uploaded_documents_cache
                if uploaded_documents_cache:
                    is_uploaded = check_if_document_exists(item['baslik'], uploaded_documents_cache)
                    if not is_uploaded and item.get('baslik_original'):
                        is_uploaded = check_if_document_exists(item['baslik_original'], uploaded_documents_cache)
            
            print(f"\n{i}. {item['baslik']}")
            print(f"   🔗 {item['link']}")
            
            if is_uploaded:
                print(f"   ✅ MevzuatGPT Yüklü.")
            else:
                print(f"   ⏳ Yüklü değil")
        
        print()
    
    print("="*80)
    print("✅ Tarama tamamlandı!")
    print("="*80)

