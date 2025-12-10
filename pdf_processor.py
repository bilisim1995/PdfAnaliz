import pypdf
from pathlib import Path
import tempfile
import math
from typing import List, Dict, Any, Optional
import os
import shutil
import subprocess # subprocess modülünü tepeye ekledim

# --- AYARLAR VE PATH BULMA ---

# Poppler yolunu ayarla
POPPLER_PATH = None
poppler_paths = [
    '/usr/bin',  # Linux standart yolu
    '/usr/local/bin',  # Alternatif Linux yolu
    '/opt/homebrew/bin',  # macOS Homebrew yolu
]

# Poppler yolunu bul
for path in poppler_paths:
    if os.path.exists(os.path.join(path, 'pdftoppm')):
        POPPLER_PATH = path
        break

# Eğer hiçbir standart yolda bulunamazsa, which komutu ile bulmayı dene
if POPPLER_PATH is None:
    pdftoppm_cmd = shutil.which('pdftoppm')
    if pdftoppm_cmd:
        POPPLER_PATH = os.path.dirname(pdftoppm_cmd)

# pdftoppm komutunun tam yolunu belirle (Bu değişkeni aşağıda kullanacağız)
PDFTOPPM_BIN = os.path.join(POPPLER_PATH, 'pdftoppm') if POPPLER_PATH else 'pdftoppm'

class PDFProcessor:
    """PDF işleme ve bölümlendirme sınıfı"""
    
    def __init__(self):
        self._ocr_available = None  # Lazy check for OCR availability
        self._ocr_cache: Dict[tuple, str] = {}  # OCR cache: (pdf_path, page_num) -> text
        self._rapidocr_instance = None  # RapidOCR instance (lazy initialization)
    
    def _check_ocr_available(self) -> bool:
        """OCR kütüphanesinin kullanılabilir olup olmadığını kontrol eder"""
        if self._ocr_available is not None:
            return self._ocr_available
        
        try:
            from rapidocr_onnxruntime import RapidOCR
            from pdf2image import convert_from_path
            
            # RapidOCR kontrolü
            try:
                # RapidOCR instance oluşturmayı dene
                ocr = RapidOCR()
                self._rapidocr_instance = ocr
            except Exception as e:
                print(f"⚠️ RapidOCR hatası: {str(e)}")
                self._ocr_available = False
                return False
            
            # Poppler kontrolü (DÜZELTİLDİ: Tam yol kullanılıyor)
            try:
                # Sadece 'pdftoppm' yerine PDFTOPPM_BIN kullanıyoruz
                result = subprocess.run([PDFTOPPM_BIN, '-v'], capture_output=True, text=True, timeout=5)
                if result.returncode != 0 and "version" not in result.stderr:
                    raise Exception("Poppler komutu başarısız oldu")
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                print(f"⚠️ Poppler hatası ({PDFTOPPM_BIN}): {str(e)}")
                print("⚠️ 'apt-get install poppler-utils' kurulu mu?")
                self._ocr_available = False
                return False
            
            self._ocr_available = True
            return True
        except ImportError as e:
            print(f"⚠️ OCR Paketleri eksik: {str(e)}")
            print("⚠️ 'pip install rapidocr-onnxruntime' komutunu çalıştırın")
            self._ocr_available = False
            return False
    
    def _get_rapidocr_instance(self):
        """RapidOCR instance'ını döndürür (lazy initialization)"""
        if self._rapidocr_instance is None:
            from rapidocr_onnxruntime import RapidOCR
            self._rapidocr_instance = RapidOCR()
        return self._rapidocr_instance
    
    def _extract_text_with_ocr(self, pdf_path: str, page_num: int) -> str:
        """OCR kullanarak sayfadan metin çıkarır (cache kullanır) - RapidOCR ile"""
        # Cache kontrolü
        cache_key = (pdf_path, page_num)
        if cache_key in self._ocr_cache:
            return self._ocr_cache[cache_key]
        
        try:
            from pdf2image import convert_from_path
            
            # Poppler ön kontrolü (DÜZELTİLDİ: Tam yol kullanılıyor)
            try:
                subprocess.run([PDFTOPPM_BIN, '-v'], capture_output=True, timeout=5)
            except Exception as e:
                 # Önemsiz hataları yutabiliriz, asıl işi convert_from_path yapacak
                 pass
            
            # PDF sayfasını görüntüye çevir
            try:
                convert_kwargs = {
                    'first_page': page_num + 1,
                    'last_page': page_num + 1,
                    'dpi': 300,
                    'thread_count': 1
                }
                # Poppler yolu bulunduysa ekle
                if POPPLER_PATH:
                    convert_kwargs['poppler_path'] = POPPLER_PATH
                
                images = convert_from_path(pdf_path, **convert_kwargs)
            except Exception as pdf_error:
                error_msg = str(pdf_error).lower()
                if "poppler" in error_msg or "pdftoppm" in error_msg:
                    raise Exception(f"Poppler hatası (Yol: {POPPLER_PATH}): {str(pdf_error)}")
                raise Exception(f"PDF görüntüye dönüştürme hatası: {str(pdf_error)}")
            
            if not images:
                return ""
            
            # RapidOCR ile metin çıkar
            ocr = self._get_rapidocr_instance()
            # PIL Image'i numpy array'e çevir
            import numpy as np
            img_array = np.array(images[0])
            
            # OCR işlemi
            result, elapse = ocr(img_array)
            
            # Result formatı: [[box, text, score], ...]
            # Text'leri birleştir
            if result:
                text = '\n'.join([item[1] for item in result if item[1]])
            else:
                text = ""
            
            text = text.strip()
            
            # Cache'e kaydet
            self._ocr_cache[cache_key] = text
            return text

        except Exception as e:
            # Hataları yukarı fırlat
            raise e
    
    def clear_ocr_cache(self):
        """OCR cache'ini temizler"""
        self._ocr_cache.clear()
    
    def get_ocr_cache_size(self) -> int:
        """OCR cache'inde kaç sayfa olduğunu döndürür"""
        return len(self._ocr_cache)

    def analyze_pdf_structure(self, pdf_path: str, skip_text_analysis: bool = False) -> Dict[str, Any]:
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                total_pages = len(reader.pages)
                
                if skip_text_analysis:
                    return {
                        'total_pages': total_pages,
                        'sample_text': '',
                        'has_text': False,
                        'needs_ocr': True,
                        'text_coverage': 0.0
                    }
                
                sample_text = ""
                pages_with_text = 0
                needs_ocr = False
                
                # İlk 10 sayfayı kontrol et
                check_limit = min(10, total_pages)
                for i in range(check_limit):
                    try:
                        page_text = reader.pages[i].extract_text()
                        if page_text and len(page_text.strip()) > 10:
                            sample_text += page_text + "\n"
                            pages_with_text += 1
                    except:
                        pass
                
                text_coverage = pages_with_text / check_limit if check_limit > 0 else 0
                
                if text_coverage < 0.3: # %30'dan az sayfada metin varsa OCR gerekir
                    needs_ocr = True
                
                has_text = len(sample_text.strip()) > 0

                # Metin yoksa ve OCR aktifse test et
                if needs_ocr and self._check_ocr_available():
                    try:
                        # İlk sayfa için OCR dene
                        ocr_text = self._extract_text_with_ocr(pdf_path, 0)
                        if ocr_text:
                            sample_text = ocr_text[:1000]
                            has_text = True
                    except Exception as e:
                        print(f"OCR Test Hatası: {e}")

                return {
                    'total_pages': total_pages,
                    'sample_text': sample_text[:1000],
                    'has_text': has_text,
                    'needs_ocr': needs_ocr,
                    'text_coverage': text_coverage
                }
        except Exception as e:
            raise Exception(f"PDF analiz hatası: {str(e)}")
    
    def create_optimal_sections(self, pdf_path: str, total_pages: int, 
                             min_pages: int, max_pages: int) -> List[Dict[str, int]]:
        sections = []
        if total_pages <= max_pages:
            sections.append({'start_page': 1, 'end_page': total_pages})
        else:
            ideal_section_size = (min_pages + max_pages) // 2
            estimated_sections = math.ceil(total_pages / ideal_section_size)
            pages_per_section = total_pages // estimated_sections
            remainder = total_pages % estimated_sections
            current_page = 1
            for i in range(estimated_sections):
                section_size = pages_per_section
                if i < remainder: section_size += 1
                section_size = max(min_pages, min(max_pages, section_size))
                end_page = min(current_page + section_size - 1, total_pages)
                sections.append({'start_page': current_page, 'end_page': end_page})
                current_page = end_page + 1
                if end_page >= total_pages: break
        return sections
    
    def create_section_pdf(self, source_pdf_path: str, start_page: int, 
                          end_page: int, output_dir: str, section_num: int) -> str:
        try:
            with open(source_pdf_path, 'rb') as source_file:
                reader = pypdf.PdfReader(source_file)
                writer = pypdf.PdfWriter()
                for page_num in range(start_page - 1, end_page):
                    if page_num < len(reader.pages):
                        writer.add_page(reader.pages[page_num])
                output_filename = f"{section_num:02d}_Bolum_{start_page}-{end_page}.pdf"
                output_path = Path(output_dir) / output_filename
                with open(output_path, 'wb') as output_file:
                    writer.write(output_file)
                return str(output_path)
        except Exception as e:
            raise Exception(f"Bölüm PDF oluşturma hatası: {str(e)}")
    
    def extract_text_from_pages(self, pdf_path: str, start_page: int, end_page: int, use_ocr: bool = False) -> str:
        text = ""
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                total_pages = len(reader.pages)
                actual_end_page = min(end_page, total_pages)
                
                if use_ocr and self._check_ocr_available():
                    # OCR MODU
                    for page_num in range(start_page - 1, actual_end_page):
                        try:
                            ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                            text += (ocr_text if ocr_text else "") + "\n"
                        except Exception:
                            text += f"[Sayfa {page_num+1}: OCR Hatası]\n"
                else:
                    # NORMAL MOD + FALLBACK
                    for page_num in range(start_page - 1, actual_end_page):
                        try:
                            page_text = reader.pages[page_num].extract_text()
                            if (not page_text or len(page_text.strip()) < 10) and self._check_ocr_available():
                                page_text = self._extract_text_with_ocr(pdf_path, page_num)
                            text += (page_text if page_text else "") + "\n"
                        except Exception:
                            text += f"[Sayfa {page_num+1}: Okuma Hatası]\n"
            return text
        except Exception as e:
            raise Exception(f"Metin çıkarma hatası: {str(e)}")
            
    def extract_all_page_texts(self, pdf_path: str, use_ocr: bool = False) -> List[str]:
        page_texts = []
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                num_pages = len(reader.pages)
            
            # Dosyayı kapattıktan sonra döngüye girelim (OCR işlemleri uzun sürer)
            for i in range(num_pages):
                # Tek tek sayfa metni al (OCR veya Normal)
                # create_section_pdf veya extract_text_from_pages kullanabiliriz
                # Basitlik için extract_text_from_pages'i tek sayfa için çağırıyoruz
                txt = self.extract_text_from_pages(pdf_path, i+1, i+1, use_ocr=use_ocr)
                page_texts.append(txt.strip())
                
            return page_texts
        except Exception as e:
            raise Exception(f"Sayfa metinleri hatası: {str(e)}")

    def create_intelligent_sections(self, pdf_path: str, total_pages: int, analyzer, use_ocr: bool = False) -> List[Dict[str, int]]:
        try:
            page_texts = self.extract_all_page_texts(pdf_path, use_ocr=use_ocr)
            suggested_sections = analyzer.suggest_content_based_sections(page_texts, total_pages)
            sections = []
            for section in suggested_sections:
                sections.append({
                    'start_page': section['start_page'],
                    'end_page': section['end_page'],
                    'reason': section.get('reason', '')
                })
            return sections
        except Exception as e:
            return self.create_optimal_sections(pdf_path, total_pages, 3, 10)
    
    def get_pdf_metadata(self, pdf_path: str) -> Dict[str, Any]:
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                metadata = reader.metadata if reader.metadata else {}
                return {
                    'title': metadata.get('/Title', ''),
                    'author': metadata.get('/Author', ''),
                    'subject': metadata.get('/Subject', ''),
                    'creator': metadata.get('/Creator', ''),
                    'producer': metadata.get('/Producer', ''),
                    'creation_date': str(metadata.get('/CreationDate', '')),
                    'modification_date': str(metadata.get('/ModDate', ''))
                }
        except Exception as e:
            return {'error': f"Metadata okuma hatası: {str(e)}"}