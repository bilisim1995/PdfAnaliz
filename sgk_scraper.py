import streamlit as st
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
import re
import json
import unicodedata


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
            
            response = requests.get(url, headers=headers, params=params, timeout=1200)  # 20 dakika timeout
            
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
                        if use_streamlit:
                            st.warning("⚠️ Çok fazla belge var. İlk 5000 belge çekildi.")
                        else:
                            print("⚠️ Çok fazla belge var. İlk 5000 belge çekildi.")
                        break
                else:
                    has_more = False
            elif response.status_code == 401:
                if use_streamlit:
                    st.warning("⚠️ Oturum süresi dolmuş. Lütfen tekrar giriş yapın.")
                else:
                    print("⚠️ Oturum süresi dolmuş. Lütfen tekrar giriş yapın.")
                return []
            elif response.status_code == 422:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', 'Bilinmeyen hata')
                    if use_streamlit:
                        st.warning(f"⚠️ API parametre hatası: {error_msg}")
                        st.code(error_data, language="json")
                    else:
                        print(f"⚠️ API parametre hatası: {error_msg}")
                        print(f"Error details: {error_data}")
                except:
                    if use_streamlit:
                        st.warning(f"⚠️ API'den belgeler çekilemedi: HTTP 422 (Unprocessable Entity)")
                        st.code(response.text[:500] if response.text else "Hata mesajı alınamadı", language="text")
                    else:
                        print(f"⚠️ API'den belgeler çekilemedi: HTTP 422 (Unprocessable Entity)")
                        print(response.text[:500] if response.text else "Hata mesajı alınamadı")
                return []
            else:
                if use_streamlit:
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
        if use_streamlit:
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

def sgk_tara():
    """SGK mevzuatlarını KAYSİS sitesinden tarar ve API ile karşılaştırır"""
    url = "https://kms.kaysis.gov.tr/Home/Kurum/22620739"
    
    # API bilgilerini kontrol et
    api_base_url = st.session_state.get('api_base_url', '')
    access_token = st.session_state.get('access_token', '')
    logged_in = st.session_state.get('logged_in', False)
    
    if not logged_in or not api_base_url or not access_token:
        # Config'den bilgileri yükle ve login yap
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                api_base_url = config.get('api_base_url', '')
                email = config.get('admin_email', '')
                password = config.get('admin_password', '')
                
                if api_base_url and email and password:
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
                        st.session_state.logged_in = True
                        st.session_state.access_token = access_token
                        st.session_state.api_base_url = api_base_url
                    else:
                        st.warning("⚠️ API'ye bağlanılamadı. Mevzuat karşılaştırması yapılamayacak.")
                        api_base_url = ''
                        access_token = ''
                else:
                    st.warning("⚠️ Config eksik bilgiler içeriyor. Mevzuat karşılaştırması yapılamayacak.")
                    api_base_url = ''
                    access_token = ''
        except Exception as e:
            st.warning(f"⚠️ Config yüklenemedi: {str(e)}")
            api_base_url = ''
            access_token = ''
    
    st.header("🔍 SGK Mevzuat Tarama")
    st.info(f"📡 Site: {url}")
    
    with st.spinner("🌐 Siteye bağlanılıyor..."):
        try:
            # Siteye istek gönder
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=1200)  # 20 dakika timeout
            
            if response.status_code != 200:
                st.error(f"❌ Siteye erişilemedi: HTTP {response.status_code}")
                return
            
            # HTML'i parse et
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Başlıkları ve içerikleri bul
            st.success("✅ Site başarıyla yüklendi!")
            
            # Progress bar
            progress = st.progress(0)
            status_text = st.empty()
            
            status_text.text("📋 Accordion yapısı aranıyor...")
            progress.progress(20)
            
            # accordion2 div'ini bul
            accordion_div = soup.find('div', {'id': 'accordion2', 'class': 'panel-group'})
            
            if not accordion_div:
                st.warning("⚠️ accordion2 div'i bulunamadı. Sayfa yapısını analiz ediyorum...")
                with st.expander("🔍 Sayfa Yapısı Analizi"):
                    st.code(str(soup)[:5000], language="html")
                return
            
            st.success("✅ Accordion yapısı bulundu!")
            progress.progress(40)
            
            status_text.text("🔍 Başlıklar ve içerikler çekiliyor...")
            
            # Accordion içindeki tüm panel'leri bul
            # Genellikle panel yapısı: <div class="panel"> içinde <div class="panel-heading"> ve <div class="panel-body">
            panels = accordion_div.find_all('div', class_='panel')
            
            if not panels:
                # Alternatif: panel-heading veya panel-body direkt bul
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
                        
                        # Link içinde badge span'i varsa atla (örn: <span class="badge">6981</span>)
                        if link.find('span', class_='badge'):
                            continue
                        
                        # Link metnini al (badge olmadan)
                        link_text = link.get_text(strip=True)
                        
                        # Boş veya çok kısa metinleri atla
                        if not link_text or len(link_text.strip()) < 10:
                            continue
                        
                        # Sadece sayılardan oluşan metinleri atla (örn: "6981", "6961", "1.", "4.")
                        # Sayı ve noktalama işaretlerinden oluşan metinleri filtrele
                        if re.match(r'^[\d\s.,]+$', link_text.strip()):
                            continue
                        
                        # Link URL'ini tamamla
                        if link_href.startswith('http'):
                            full_url = link_href
                        elif link_href.startswith('/'):
                            full_url = f"https://kms.kaysis.gov.tr{link_href}"
                        else:
                            full_url = f"{url}{link_href}"
                        
                        # Sadece /Home/Goster/ ile başlayan linkleri al (gerçek mevzuat sayfaları)
                        if not full_url or '/Home/Goster/' not in full_url:
                            continue
                        
                        # Metni formatla: Başlığın sadece ilk harfi büyük, diğerleri küçük (Türkçe uyumlu)
                        formatted_text = turkish_sentence_case(link_text)
                        # Başlıktaki sonundaki sayıları kaldır (örn: "Esas ve Usuller35" → "Esas ve Usuller")
                        formatted_text = re.sub(r'\d+$', '', formatted_text).strip()
                        # Orijinal metni de sakla (karşılaştırma için)
                        original_text = link_text.strip()
                        
                        items_in_section.append({
                            'baslik': formatted_text,
                            'baslik_original': original_text,  # Karşılaştırma için orijinal
                            'link': full_url
                        })
                    
                    if heading_text or items_in_section:
                        all_sections.append({
                            'section_title': heading_text or 'Başlıksız Bölüm',
                            'items': items_in_section
                        })
            
            progress.progress(60)
            
            # API'den yüklü mevzuatları çek
            uploaded_documents = []
            if api_base_url and access_token:
                status_text.text("📡 API'den yüklü mevzuatlar kontrol ediliyor...")
                progress.progress(70)
                uploaded_documents = get_uploaded_documents(api_base_url, access_token)
                if uploaded_documents:
                    st.success(f"✅ API'den {len(uploaded_documents)} yüklü mevzuat bulundu")
                else:
                    st.info("ℹ️ API'de yüklü mevzuat bulunamadı veya bağlantı kurulamadı")
            
            progress.progress(80)
            
            # Sonuçları göster ve karşılaştır
            if all_sections:
                st.subheader(f"📋 Bulunan Mevzuatlar")
                
                total_items = sum(len(section['items']) for section in all_sections)
                st.info(f"📊 Toplam {len(all_sections)} başlık altında {total_items} mevzuat bulundu")
                
                # Her bölümü göster
                for section in all_sections:
                    section_title = section['section_title']
                    items = section['items']
                    
                    if items:
                        # Yüklü ve yüklü olmayan sayılarını hesapla
                        uploaded_count = 0
                        not_uploaded_count = 0
                        item_statuses = []
                        
                        for item in items:
                            # Mevzuatın yüklü olup olmadığını kontrol et
                            # Hem formatlanmış hem orijinal başlığı kontrol et
                            is_uploaded = False
                            if uploaded_documents:
                                # Önce formatlanmış başlığı kontrol et
                                is_uploaded = check_if_document_exists(item['baslik'], uploaded_documents)
                                # Eğer bulunamadıysa orijinal başlığı da kontrol et
                                if not is_uploaded and item.get('baslik_original'):
                                    is_uploaded = check_if_document_exists(item['baslik_original'], uploaded_documents)
                            
                            item_statuses.append(is_uploaded)
                            if is_uploaded:
                                uploaded_count += 1
                            else:
                                not_uploaded_count += 1
                        
                        # Başlık formatı: "Kanunlar Toplam:4"
                        expander_title = f"{section_title} Toplam:{len(items)}"
                        
                        with st.expander(expander_title, expanded=True):
                            # İstatistik bilgisini bir kere göster (yüklü olmayan mevzuat varsa)
                            if not_uploaded_count > 0:
                                st.caption(f"({uploaded_count} adet - yüklü ✅  - {not_uploaded_count} adet yüklü değil ⏳ )")
                                st.markdown("---")
                            
                            for i, item in enumerate(items, 1):
                                is_uploaded = item_statuses[i - 1]
                                
                                # Başlık
                                st.markdown(f"**{item['baslik']}**")
                                
                                if item['link']:
                                    st.markdown(f"   🔗 [Link]({item['link']})")
                                
                                # Yüklü durumu alta taşındı
                                if is_uploaded:
                                    st.markdown("✅ **MevzuatGPT Yüklü.**")
                                
                                # Yükle butonu
                                if not is_uploaded:
                                    button_key = f"yukle_{section_title}_{i}_{hash(item['baslik'])}"
                                    if st.button("📤 MevzuatGPT YÜKLE", key=button_key, type="primary", use_container_width=True):
                                        st.info(f"🚀 Yükleme işlemi başlatılıyor: {item['baslik']}")
                                        st.warning("⚠️ PDF yükleme özelliği henüz entegre edilmedi. Bu özellik yakında eklenecek.")
                                
                                st.markdown("---")
                    else:
                        st.caption(f"📂 {section_title} (içerik bulunamadı)")
            else:
                st.warning("⚠️ Accordion içinde içerik bulunamadı.")
                with st.expander("🔍 Accordion Yapısı Analizi"):
                    st.code(str(accordion_div)[:5000], language="html")
            
            progress.progress(100)
            status_text.text("✅ Tarama tamamlandı!")
            
        except requests.exceptions.RequestException as e:
            st.error(f"❌ Bağlantı hatası: {str(e)}")
        except Exception as e:
            st.error(f"❌ Hata oluştu: {str(e)}")
            st.exception(e)


