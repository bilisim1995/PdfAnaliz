"""
Ahiler Kalkınma Ajansı KAYSİS Scraper Module
Ahiler Kalkınma Ajansı'nın KAYSİS sitesinden mevzuat tarama modülü
"""
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
import re
import json
import unicodedata

# Streamlit import (opsiyonel - use_streamlit parametresi ile kontrol edilir)
try:
    import streamlit as st
    STREAMLIT_AVAILABLE = True
except ImportError:
    STREAMLIT_AVAILABLE = False


# ============================================================================
# Yardımcı Fonksiyonlar
# ============================================================================

def normalize_text(text: str) -> str:
    """Metni karşılaştırma için normalize eder (büyük/küçük harf, boşluklar)"""
    if not text:
        return ""
    # Küçük harfe çevir, fazla boşlukları temizle
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    return normalized


def is_title_similar(title1: str, title2: str) -> bool:
    """İki başlığın benzer olup olmadığını kontrol eder"""
    norm1 = normalize_text(title1)
    norm2 = normalize_text(title2)
    
    # Tam eşleşme
    if norm1 == norm2:
        return True
    
    # Bir başlık diğerini içeriyor mu? (en az 20 karakter)
    if len(norm1) >= 20 and len(norm2) >= 20:
        if norm1 in norm2 or norm2 in norm1:
            return True
    
    # Başlıkların ilk 30 karakteri aynı mı? (hızlı kontrol)
    if len(norm1) >= 30 and len(norm2) >= 30:
        if norm1[:30] == norm2[:30]:
            return True
    
    return False


def turkish_title(text: str) -> str:
    """Türkçe karakterleri dikkate alarak Title Case'e çevirir"""
    if not text:
        return ""
    # Unicode normalizasyonu (i̇ → i)
    s = unicodedata.normalize('NFC', text)
    s = s.replace("i\u0307", "i")
    # Türkçe küçük harfe çevirme
    tmp = s.replace('I', 'ı').replace('İ', 'i').lower()
    # Yaygın kelime/ek düzeltmeleri (heuristic)
    tmp = re.sub(r"\bsayili\b", "sayılı", tmp)
    tmp = re.sub(r"\bsigortalilik\b", "sigortalılık", tmp)
    tmp = re.sub(r"\bsigortali\b", "sigortalı", tmp)
    tmp = re.sub(r"\bişlemleri\b", "işlemleri", tmp)
    # Kelime kelime baş harf büyüt
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


def turkish_sentence_case(text: str) -> str:
    """Türkçe karakterleri dikkate alarak Sentence Case'e çevirir (sadece ilk harf büyük)"""
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


# ============================================================================
# API İşlemleri
# ============================================================================

def get_uploaded_documents(api_base_url: str, access_token: str, use_streamlit: bool = True) -> List[Dict[str, Any]]:
    """API'den yüklü mevzuatları çeker (sayfalama ile)"""
    try:
        url = f"{api_base_url.rstrip('/')}/api/admin/documents"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        all_documents = []
        page = 1
        limit = 100  # API maksimum 100 kabul ediyor
        has_more = True
        
        # Sayfalama ile tüm belgeleri çek
        while has_more:
            params = {
                'page': page,
                'limit': limit
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success') and result.get('data'):
                    documents = result['data'].get('documents', [])
                    all_documents.extend(documents)
                    
                    # Pagination bilgisini kontrol et
                    pagination = result['data'].get('pagination', {})
                    has_more = pagination.get('has_next', False)
                    page += 1
                    
                    # Güvenlik için maksimum 50 sayfa (5000 belge) çek
                    if page > 50:
                        if use_streamlit and STREAMLIT_AVAILABLE:
                            st.warning("⚠️ Çok fazla belge var. İlk 5000 belge çekildi.")
                        else:
                            print("⚠️ Çok fazla belge var. İlk 5000 belge çekildi.")
                        break
                else:
                    has_more = False
            elif response.status_code == 401:
                if use_streamlit and STREAMLIT_AVAILABLE:
                    st.warning("⚠️ Oturum süresi dolmuş. Lütfen tekrar giriş yapın.")
                else:
                    print("⚠️ Oturum süresi dolmuş. Lütfen tekrar giriş yapın.")
                return []
            elif response.status_code == 422:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', 'Bilinmeyen hata')
                    if use_streamlit and STREAMLIT_AVAILABLE:
                        st.warning(f"⚠️ API parametre hatası: {error_msg}")
                        st.code(error_data, language="json")
                    else:
                        print(f"⚠️ API parametre hatası: {error_msg}")
                        print(f"Error details: {error_data}")
                except:
                    if use_streamlit and STREAMLIT_AVAILABLE:
                        st.warning(f"⚠️ API'den belgeler çekilemedi: HTTP 422 (Unprocessable Entity)")
                        st.code(response.text[:500] if response.text else "Hata mesajı alınamadı", language="text")
                    else:
                        print(f"⚠️ API'den belgeler çekilemedi: HTTP 422 (Unprocessable Entity)")
                        print(response.text[:500] if response.text else "Hata mesajı alınamadı")
                return []
            else:
                if use_streamlit and STREAMLIT_AVAILABLE:
                    st.warning(f"⚠️ API'den belgeler çekilemedi: HTTP {response.status_code}")
                    if response.text:
                        try:
                            error_data = response.json()
                            st.code(error_data, language="json")
                        except:
                            st.code(response.text[:500], language="text")
                else:
                    print(f"⚠️ API'den belgeler çekilemedi: HTTP {response.status_code}")
                    if response.text:
                        print(response.text[:500])
                return []
        
        # Frontend'de status=completed filtresi uygula
        completed_documents = [
            doc for doc in all_documents 
            if doc.get('processing_status') == 'completed'
        ]
        
        return completed_documents
            
    except Exception as e:
        if use_streamlit and STREAMLIT_AVAILABLE:
            st.warning(f"⚠️ API bağlantı hatası: {str(e)}")
        else:
            print(f"⚠️ API bağlantı hatası: {str(e)}")
        return []


def check_if_document_exists(document_title: str, uploaded_documents: List[Dict[str, Any]]) -> bool:
    """Belge başlığının API'de yüklü olup olmadığını kontrol eder"""
    for doc in uploaded_documents:
        # title, document_title, belge_adi alanlarını kontrol et
        doc_titles = [
            doc.get('title', ''),
            doc.get('document_title', ''),
            doc.get('belge_adi', ''),
            doc.get('filename', '')
        ]
        
        for doc_title in doc_titles:
            if doc_title and is_title_similar(document_title, doc_title):
                return True
    
    return False


# ============================================================================
# KAYSİS Scraping Fonksiyonları
# ============================================================================

def scrape_ahiler_kalkinma_ajansi_mevzuat(url: str = "https://kms.kaysis.gov.tr/Home/Kurum/17211906") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    KAYSİS sitesinden Ahiler Kalkınma Ajansı mevzuatlarını tarar ve API ile karşılaştırır
    
    Args:
        url: Taranacak kurum URL'i (varsayılan: Ahiler Kalkınma Ajansı KAYSİS URL'i)
    
    Returns:
        Tuple[List[Dict[str, Any]], Dict[str, Any]]: (all_sections, stats)
            - all_sections: Tüm bölümler ve mevzuatlar
            - stats: İstatistikler (toplam bölüm, toplam mevzuat, yüklü sayısı vb.)
    """
    print(f"🔍 Ahiler Kalkınma Ajansı Mevzuat Tarama Başlatılıyor...")
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
                
                login_response = requests.post(
                    login_url,
                    headers={"Content-Type": "application/json"},
                    json=login_data,
                    timeout=60
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
    
    try:
        # Siteye istek gönder
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        
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
        uploaded_documents = []
        if api_base_url and access_token:
            print("\n📡 API'den yüklü mevzuatlar kontrol ediliyor...")
            uploaded_documents = get_uploaded_documents(api_base_url, access_token, use_streamlit=False)
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


def print_results_to_console(all_sections: List[Dict[str, Any]], stats: Dict[str, Any], uploaded_documents: Optional[List[Dict[str, Any]]] = None):
    """
    Sonuçları konsola yazdırır
    
    Args:
        all_sections: Tüm bölümler ve mevzuatlar
        stats: İstatistikler
        uploaded_documents: Yüklü dökümanlar listesi (opsiyonel, stats'ten de alınabilir)
    """
    print("\n" + "="*80)
    print("📋 BULUNAN MEVZUATLAR (Ahiler Kalkınma Ajansı)")
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
            if uploaded_documents:
                is_uploaded = check_if_document_exists(item['baslik'], uploaded_documents)
                if not is_uploaded and item.get('baslik_original'):
                    is_uploaded = check_if_document_exists(item['baslik_original'], uploaded_documents)
            
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

