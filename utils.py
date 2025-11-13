import requests
import tempfile
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

def html_to_pdf(url: str) -> str:
    """HTML sayfasını PDF'ye çevirir (playwright kullanarak)"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Exception("Playwright kurulu değil. Lütfen 'pip install playwright' ve 'playwright install chromium' komutlarını çalıştırın.")
    
    temp_dir = tempfile.gettempdir()
    filename = f"html_to_pdf_{uuid.uuid4().hex[:8]}.pdf"
    temp_path = os.path.join(temp_dir, filename)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Sayfayı URL'den aç (daha iyi render için)
        page.goto(url, wait_until="networkidle", timeout=60000)
        
        # Sayfanın yüklenmesini bekle
        page.wait_for_timeout(2000)  # 2 saniye bekle
        
        # PDF olarak kaydet
        page.pdf(
            path=temp_path,
            format="A4",
            print_background=True,
            margin={"top": "20mm", "right": "20mm", "bottom": "20mm", "left": "20mm"}
        )
        
        browser.close()
    
    # Dosya boyutunu kontrol et
    file_size = os.path.getsize(temp_path)
    if file_size < 1024:  # 1KB'dan küçükse
        os.remove(temp_path)
        raise ValueError("Oluşturulan PDF çok küçük")
    
    return temp_path


def download_pdf_from_url(url: str, max_retries: int = 3) -> str:
    """URL'den PDF indirir veya HTML sayfasını PDF'ye çevirir"""
    import time
    
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
            
            # İçeriği indir (daha uzun timeout ve allow_redirects)
            response = requests.get(url, headers=headers, timeout=120, allow_redirects=True)  # 2 dakika timeout
            response.raise_for_status()
        
            # Content-Type kontrolü
            content_type = response.headers.get('content-type', '').lower()
            
            # PDF kontrolü
            is_pdf = False
            if 'pdf' in content_type or url.lower().endswith('.pdf'):
                # PDF magic number kontrolü
                if response.content.startswith(b'%PDF-'):
                    is_pdf = True
            
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
                print("📄 HTML sayfası tespit edildi, PDF'ye çevriliyor...")
                
                # HTML'i PDF'ye çevir (playwright ile direkt URL'den)
                pdf_path = html_to_pdf(url)
                print("✅ HTML sayfası PDF'ye çevrildi")
                return pdf_path
            
        except (requests.exceptions.RequestException, ValueError) as e:
            last_error = e
            if attempt < max_retries - 1:
                # Son deneme değilse, kısa bir süre bekle ve tekrar dene
                wait_time = (attempt + 1) * 2  # 2, 4, 6 saniye
                time.sleep(wait_time)
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
