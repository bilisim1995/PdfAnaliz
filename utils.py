import requests
import tempfile
import os
import asyncio
from pathlib import Path
from urllib.parse import urlparse
import uuid
from typing import Optional, Dict
from pymongo import MongoClient

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


async def html_to_pdf(url: str) -> str:
    """HTML sayfasını PDF'ye çevirir (playwright async API kullanarak)"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise Exception("Playwright kurulu değil. Lütfen 'pip install playwright' ve 'playwright install chromium' komutlarını çalıştırın.")
    
    temp_dir = tempfile.gettempdir()
    filename = f"html_to_pdf_{uuid.uuid4().hex[:8]}.pdf"
    temp_path = os.path.join(temp_dir, filename)
    
    print(f"🌐 HTML sayfası açılıyor: {url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Viewport boyutunu ayarla (daha iyi render için)
        await page.set_viewport_size({"width": 1920, "height": 1080})
        
        try:
            # Sayfayı URL'den aç (daha uzun timeout ve daha fazla bekleme)
            print("⏳ Sayfa yükleniyor...")
            await page.goto(url, wait_until="networkidle", timeout=120000)  # 2 dakika timeout
            
            # Sayfanın tamamen yüklenmesini bekle (KAYSİS sayfaları için daha uzun bekleme)
            await page.wait_for_timeout(3000)  # 3 saniye bekle
            
            # JavaScript'in çalışmasını bekle (eğer dinamik içerik varsa)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")
            
            print("📄 PDF'ye dönüştürülüyor...")
            
            # PDF olarak kaydet (daha iyi formatlama için)
            await page.pdf(
                path=temp_path,
                format="A4",
                print_background=True,
                margin={"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"},
                prefer_css_page_size=False
            )
            
            print("✅ PDF oluşturuldu")
            
        except Exception as e:
            await browser.close()
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise Exception(f"HTML sayfası PDF'ye dönüştürülürken hata: {str(e)}")
        
        await browser.close()
    
    # Dosya boyutunu kontrol et
    if not os.path.exists(temp_path):
        raise ValueError("PDF dosyası oluşturulamadı")
    
    file_size = os.path.getsize(temp_path)
    if file_size < 1024:  # 1KB'dan küçükse
        os.remove(temp_path)
        raise ValueError("Oluşturulan PDF çok küçük (sayfa içeriği boş olabilir)")
    
    print(f"📊 PDF boyutu: {file_size / 1024:.2f} KB")
    
    return temp_path


async def download_pdf_from_url(url: str, max_retries: int = 3) -> str:
    """URL'den PDF indirir veya HTML sayfasını PDF'ye çevirir (async)"""
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # URL'yi doğrula
            parsed_url = urlparse(url)
            if not parsed_url.scheme or not parsed_url.netloc:
                raise ValueError("Geçersiz URL formatı")
            
            # HTTP headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/pdf,text/html,application/xhtml+xml,*/*'
            }
            
            # Proxy bilgilerini çek
            proxies = get_proxy_from_db()
            
            # İçeriği indir (async thread'de çalıştır - requests sync olduğu için)
            def _download_sync():
                return requests.get(url, headers=headers, timeout=120, allow_redirects=True, proxies=proxies)
            
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, _download_sync)
            response.raise_for_status()
        
            # Content-Type kontrolü
            content_type = response.headers.get('content-type', '').lower()
            
            # PDF kontrolü - daha kapsamlı kontrol
            is_pdf = False
            
            # 1. Content-Type kontrolü
            if 'application/pdf' in content_type:
                is_pdf = True
            # 2. URL uzantısı kontrolü
            elif url.lower().endswith('.pdf'):
                is_pdf = True
            # 3. PDF magic number kontrolü (en güvenilir)
            elif response.content.startswith(b'%PDF-'):
                is_pdf = True
            # 4. HTML içerik kontrolü (eğer HTML tag'leri varsa PDF değildir)
            elif b'<html' in response.content[:1024].lower() or b'<!doctype' in response.content[:1024].lower():
                is_pdf = False
            # 5. Content-Type'da HTML belirtilmişse
            elif 'text/html' in content_type or 'application/xhtml' in content_type:
                is_pdf = False
            
            # Eğer PDF ise direkt kaydet
            if is_pdf:
                # Geçici dosya oluştur
                temp_dir = tempfile.gettempdir()
                filename = f"downloaded_pdf_{uuid.uuid4().hex[:8]}.pdf"
                temp_path = os.path.join(temp_dir, filename)
                
                # Dosyayı kaydet
                with open(temp_path, 'wb') as f:
                    f.write(response.content)
                
                # Dosya boyutunu kontrol et
                file_size = os.path.getsize(temp_path)
                if file_size < 1024:  # 1KB'dan küçükse
                    os.remove(temp_path)
                    raise ValueError("İndirilen dosya çok küçük (PDF olmayabilir)")
                
                return temp_path
            else:
                # HTML sayfası ise PDF'ye çevir
                print(f"📄 HTML sayfası tespit edildi (Content-Type: {content_type}), PDF'ye çevriliyor...")
                print(f"🔗 URL: {url}")
                
                # HTML'i PDF'ye çevir (playwright async ile direkt URL'den)
                try:
                    pdf_path = await html_to_pdf(url)
                    print(f"✅ HTML sayfası başarıyla PDF'ye çevrildi: {pdf_path}")
                    return pdf_path
                except Exception as html_error:
                    print(f"❌ HTML'den PDF'ye dönüştürme hatası: {str(html_error)}")
                    raise Exception(f"HTML sayfası PDF'ye dönüştürülemedi: {str(html_error)}")
            
        except (requests.exceptions.RequestException, ValueError) as e:
            last_error = e
            if attempt < max_retries - 1:
                # Son deneme değilse, kısa bir süre bekle ve tekrar dene
                wait_time = (attempt + 1) * 2  # 2, 4, 6 saniye
                await asyncio.sleep(wait_time)
            continue
        except Exception as e:
            # Diğer hatalar için hemen çık
            raise Exception(f"PDF indirme/dönüştürme hatası: {str(e)}")
    
    # Tüm denemeler başarısız olduysa
    if last_error:
        if isinstance(last_error, requests.exceptions.RequestException):
            raise Exception(f"URL'den indirme hatası ({max_retries} deneme sonucu): {str(last_error)}")
        else:
            raise Exception(f"PDF indirme/dönüştürme hatası ({max_retries} deneme sonucu): {str(last_error)}")
    
    raise Exception("PDF indirilemedi (bilinmeyen hata)")

def create_output_directories() -> str:
    """Çıktı klasörlerini oluşturur"""
    try:
        # Ana çıktı klasörü
        base_dir = Path.cwd() / "pdf_output"
        
        # Benzersiz alt klasör (timestamp ile)
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = base_dir / f"sections_{timestamp}"
        
        # Klasörleri oluştur
        output_dir.mkdir(parents=True, exist_ok=True)
        
        return str(output_dir)
        
    except Exception as e:
        raise Exception(f"Çıktı klasörü oluşturma hatası: {str(e)}")

def validate_pdf_file(file_path: str) -> bool:
    """PDF dosyasının geçerliliğini kontrol eder"""
    try:
        if not os.path.exists(file_path):
            return False
        
        # Dosya uzantısı kontrolü
        if not file_path.lower().endswith('.pdf'):
            return False
        
        # Dosya boyutu kontrolü
        file_size = os.path.getsize(file_path)
        if file_size < 1024:  # 1KB'dan küçük
            return False
        
        # PDF magic number kontrolü
        with open(file_path, 'rb') as f:
            header = f.read(8)
            if not header.startswith(b'%PDF-'):
                return False
        
        return True
        
    except Exception:
        return False

def cleanup_temp_files(file_paths: list):
    """Geçici dosyaları temizler"""
    for file_path in file_paths:
        try:
            if os.path.exists(file_path) and 'temp' in file_path:
                os.remove(file_path)
        except Exception:
            pass  # Sessizce devam et

def format_file_size(size_bytes: int) -> str:
    """Dosya boyutunu okunabilir formatta döndürür"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

def extract_filename_from_url(url: str) -> str:
    """URL'den dosya adını çıkarır"""
    try:
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        if not filename or not filename.endswith('.pdf'):
            filename = f"document_{uuid.uuid4().hex[:8]}.pdf"
        return filename
    except Exception:
        return f"document_{uuid.uuid4().hex[:8]}.pdf"

def transliterate_turkish(text: str) -> str:
    """Türkçe karakterleri İngilizce karakterlere çevirir"""
    turkish_map = {
        'ç': 'c', 'Ç': 'C',
        'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'İ': 'I',
        'ö': 'o', 'Ö': 'O',
        'ş': 's', 'Ş': 'S',
        'ü': 'u', 'Ü': 'U'
    }
    
    result = text
    for turkish_char, english_char in turkish_map.items():
        result = result.replace(turkish_char, english_char)
    
    return result

def sanitize_filename(filename: str) -> str:
    """Dosya adını güvenli hale getirir"""
    import re
    # Türkçe karakterleri koru, sadece güvenli olmayan karakterleri temizle
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.strip('. ')
    if not filename:
        filename = f"file_{uuid.uuid4().hex[:8]}"
    return filename

def create_pdf_filename(base_name: str, section_num: int, start_page: int, end_page: int, title: str = "") -> str:
    """PDF bölüm dosya adı oluşturur (Türkçe karaktersiz)"""
    import re
    
    # Başlığı kullan, yoksa base_name kullan
    if title and title != "İçerik Tespit Edilemedi" and title != "API Analiz Hatası":
        # Başlıktan dosya adı oluştur
        filename = transliterate_turkish(title)
        # Özel karakterleri temizle
        filename = re.sub(r'[^\w\s-]', '', filename)
        # Boşlukları alt çizgiye çevir
        filename = re.sub(r'\s+', '_', filename)
        # Çok uzunsa kısalt
        if len(filename) > 80:
            filename = filename[:80]
    else:
        # Base name'den oluştur
        filename = transliterate_turkish(base_name)
        filename = re.sub(r'[^\w\s-]', '', filename)
        filename = re.sub(r'\s+', '_', filename)
        filename = f"{filename}_Bolum_{section_num}"
    
    # Sayfa numaralarını ekle
    filename = f"{section_num:02d}_{filename}_{start_page}-{end_page}.pdf"
    
    # Temizle ve güvenli hale getir
    filename = filename.strip('_')
    
    return filename
