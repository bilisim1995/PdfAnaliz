import pypdf
from pathlib import Path
import tempfile
import math
from typing import List, Dict, Any, Optional
import os

class PDFProcessor:
    """PDF işleme ve bölümlendirme sınıfı"""
    
    def __init__(self):
        self._ocr_available = None  # Lazy check for OCR availability
    
    def _check_ocr_available(self) -> bool:
        """OCR kütüphanesinin kullanılabilir olup olmadığını kontrol eder"""
        if self._ocr_available is not None:
            return self._ocr_available
        
        try:
            import pytesseract
            from pdf2image import convert_from_path
            import subprocess
            
            # Tesseract'ın kurulu olup olmadığını kontrol et
            try:
                pytesseract.get_tesseract_version()
            except Exception as e:
                print("⚠️ Tesseract OCR kurulu değil veya erişilemiyor.")
                print("📋 Kurulum için:")
                print("   Linux/Debian/Ubuntu: sudo apt-get install tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng")
                print("   macOS: brew install tesseract tesseract-lang")
                print("   Veya proje kök dizininde: sudo ./install.sh")
                print(f"   Hata detayı: {str(e)}")
                self._ocr_available = False
                return False
            
            # Poppler'ın kurulu olup olmadığını kontrol et
            try:
                result = subprocess.run(['pdftoppm', '-v'], capture_output=True, text=True, timeout=5)
                if result.returncode != 0:
                    raise Exception("Poppler komutu çalışmadı")
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                print("⚠️ Poppler kurulu değil. 'apt-get install poppler-utils' komutunu çalıştırın.")
                self._ocr_available = False
                return False
            
            self._ocr_available = True
            return True
        except ImportError as e:
            print(f"⚠️ OCR Python paketleri kurulu değil: {str(e)}")
            print("⚠️ 'pip install pytesseract pdf2image pillow' komutunu çalıştırın.")
            self._ocr_available = False
            return False
    
    def _get_available_ocr_languages(self) -> str:
        """Kullanılabilir OCR dillerini kontrol eder ve uygun dil string'i döner"""
        try:
            import pytesseract
            available_langs = pytesseract.get_languages()
            
            # Türkçe ve İngilizce varsa ikisini de kullan
            if 'tur' in available_langs and 'eng' in available_langs:
                return 'tur+eng'
            elif 'tur' in available_langs:
                return 'tur'
            elif 'eng' in available_langs:
                return 'eng'
            else:
                return 'eng'  # Varsayılan olarak İngilizce
        except Exception:
            return 'eng'  # Hata durumunda İngilizce
    
    def _extract_text_with_ocr(self, pdf_path: str, page_num: int) -> str:
        """OCR kullanarak sayfadan metin çıkarır"""
        try:
            import pytesseract
            from pdf2image import convert_from_path
            from PIL import Image
            import subprocess
            
            # Poppler kontrolü (pdf2image için gerekli)
            try:
                subprocess.run(['pdftoppm', '-v'], capture_output=True, timeout=5, check=True)
            except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
                raise Exception("Poppler kurulu değil. Sistem paketlerini kurun: 'apt-get install poppler-utils' (Linux) veya 'brew install poppler' (macOS)")
            
            # PDF sayfasını görüntüye çevir
            try:
                images = convert_from_path(
                    pdf_path,
                    first_page=page_num + 1,
                    last_page=page_num + 1,
                    dpi=300,  # Yüksek çözünürlük için
                    thread_count=1  # Tek sayfa için thread gerekmez
                )
            except Exception as pdf_error:
                error_msg = str(pdf_error).lower()
                if "poppler" in error_msg or "pdftoppm" in error_msg:
                    raise Exception("Poppler kurulu değil veya PATH'te bulunamıyor. 'apt-get install poppler-utils' komutunu çalıştırın.")
                raise Exception(f"PDF görüntüye dönüştürme hatası: {str(pdf_error)}")
            
            if not images or len(images) == 0:
                return ""
            
            # Kullanılabilir dilleri al
            ocr_lang = self._get_available_ocr_languages()
            
            # OCR ile metin çıkar
            try:
                text = pytesseract.image_to_string(
                    images[0],
                    lang=ocr_lang
                )
            except Exception as tesseract_error:
                error_msg = str(tesseract_error).lower()
                if "tesseract" in error_msg or "not found" in error_msg:
                    raise Exception("Tesseract OCR kurulu değil. 'apt-get install tesseract-ocr tesseract-ocr-tur' komutunu çalıştırın.")
                raise Exception(f"Tesseract OCR hatası: {str(tesseract_error)}")
            
            return text.strip()
        except ImportError as import_error:
            missing_pkg = str(import_error)
            if "pytesseract" in missing_pkg:
                raise Exception("pytesseract kurulu değil. 'pip install pytesseract' komutunu çalıştırın.")
            elif "pdf2image" in missing_pkg:
                raise Exception("pdf2image kurulu değil. 'pip install pdf2image' komutunu çalıştırın.")
            elif "PIL" in missing_pkg or "Image" in missing_pkg:
                raise Exception("Pillow kurulu değil. 'pip install pillow' komutunu çalıştırın.")
            raise Exception(f"OCR Python paketleri eksik: {str(import_error)}")
        except Exception as e:
            # Hata mesajını daha açıklayıcı hale getir
            error_msg = str(e)
            if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                raise Exception(f"Poppler hatası: {error_msg}. Sistem paketlerini kurun: 'apt-get install poppler-utils' (Linux) veya 'brew install poppler' (macOS)")
            elif "tesseract" in error_msg.lower():
                raise Exception(f"Tesseract hatası: {error_msg}. Sistem paketlerini kurun: 'apt-get install tesseract-ocr tesseract-ocr-tur' (Linux) veya 'brew install tesseract tesseract-lang' (macOS)")
            raise Exception(f"OCR hatası: {error_msg}")
    
    def analyze_pdf_structure(self, pdf_path: str) -> Dict[str, Any]:
        """PDF dosyasının yapısını analiz eder"""
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                total_pages = len(reader.pages)
                
                # Daha kapsamlı kontrol: İlk, ortadaki ve son sayfaları kontrol et
                sample_text = ""
                sample_pages = min(10, total_pages)  # İlk 10 sayfayı kontrol et
                has_text = False
                needs_ocr = False
                total_text_length = 0
                pages_with_text = 0
                
                # İlk sayfaları kontrol et
                for i in range(sample_pages):
                    try:
                        page_text = reader.pages[i].extract_text()
                        if page_text and len(page_text.strip()) > 0:
                            sample_text += page_text + "\n"
                            total_text_length += len(page_text.strip())
                            pages_with_text += 1
                            has_text = True
                        else:
                            # Metin yoksa OCR gerekebilir
                            needs_ocr = True
                    except Exception as e:
                        needs_ocr = True
                        continue
                
                # Ortadaki ve son sayfaları da kontrol et (eğer PDF uzunsa)
                if total_pages > 10:
                    # Ortadaki sayfalar
                    mid_start = total_pages // 2
                    mid_end = min(mid_start + 3, total_pages)
                    for i in range(mid_start, mid_end):
                        try:
                            page_text = reader.pages[i].extract_text()
                            if page_text and len(page_text.strip()) > 0:
                                total_text_length += len(page_text.strip())
                                pages_with_text += 1
                            else:
                                needs_ocr = True
                        except Exception:
                            needs_ocr = True
                    
                    # Son sayfalar
                    last_start = max(0, total_pages - 3)
                    for i in range(last_start, total_pages):
                        try:
                            page_text = reader.pages[i].extract_text()
                            if page_text and len(page_text.strip()) > 0:
                                total_text_length += len(page_text.strip())
                                pages_with_text += 1
                            else:
                                needs_ocr = True
                        except Exception:
                            needs_ocr = True
                
                # Eğer metin çok azsa (toplam sayfa sayısının %20'sinden az sayfa metin içeriyorsa) OCR gerekli
                if total_pages > 0:
                    text_coverage = pages_with_text / total_pages
                    if text_coverage < 0.2:  # %20'den az sayfa metin içeriyorsa
                        needs_ocr = True
                        print(f"📸 PDF'de metin kapsamı düşük (%{text_coverage*100:.1f}), OCR gerekli olabilir")
                    
                    # Ortalama sayfa başına metin miktarını hesapla
                    avg_text_per_page = total_text_length / pages_with_text if pages_with_text > 0 else 0
                    
                    # Sayfa başına metin miktarlarını kontrol et (sadece başlıklar mı yoksa gerçek içerik mi?)
                    page_text_lengths = []
                    for i in range(min(5, total_pages)):  # İlk 5 sayfayı kontrol et
                        try:
                            page_text = reader.pages[i].extract_text()
                            if page_text:
                                page_text_lengths.append(len(page_text.strip()))
                        except Exception:
                            pass
                    
                    # Eğer sayfa başına metin miktarı çok değişkense veya çoğu sayfada çok azsa, OCR gerekli
                    if page_text_lengths:
                        min_text = min(page_text_lengths)
                        max_text = max(page_text_lengths)
                        # Eğer çoğu sayfada metin 200 karakterden azsa, muhtemelen sadece başlıklar var
                        pages_with_low_text = sum(1 for length in page_text_lengths if length < 200)
                        if pages_with_low_text >= len(page_text_lengths) * 0.6:  # %60'tan fazla sayfa az metin içeriyorsa
                            needs_ocr = True
                            print(f"⚠️ PDF'de çoğu sayfada metin çok az (min: {min_text}, max: {max_text}, ortalama: {avg_text_per_page:.0f} karakter/sayfa). Muhtemelen sadece başlıklar. OCR gerekli.")
                        # Veya ortalama 300 karakterden azsa
                        elif avg_text_per_page > 0 and avg_text_per_page < 300:
                            needs_ocr = True
                            print(f"⚠️ PDF'de metin var ama çok az (ortalama {avg_text_per_page:.0f} karakter/sayfa). Muhtemelen sadece başlıklar. OCR gerekli.")
                
                # Eğer metin yoksa ve OCR kullanılabilirse, OCR ile test et
                if not has_text and needs_ocr and self._check_ocr_available():
                    print("📸 PDF'de metin bulunamadı, OCR test ediliyor...")
                    try:
                        ocr_text = self._extract_text_with_ocr(pdf_path, 0)
                        if ocr_text:
                            sample_text = ocr_text[:1000]
                            has_text = True
                            print("✅ OCR ile metin başarıyla çıkarıldı (test)")
                    except Exception as ocr_error:
                        print(f"⚠️ OCR test hatası: {str(ocr_error)}")
                
                return {
                    'total_pages': total_pages,
                    'sample_text': sample_text[:1000],  # İlk 1000 karakter
                    'has_text': has_text,
                    'needs_ocr': needs_ocr or (pages_with_text / total_pages < 0.2 if total_pages > 0 else True),
                    'text_coverage': pages_with_text / total_pages if total_pages > 0 else 0.0
                }
        except Exception as e:
            raise Exception(f"PDF analiz hatası: {str(e)}")
    
    def create_optimal_sections(self, pdf_path: str, total_pages: int, 
                             min_pages: int, max_pages: int) -> List[Dict[str, int]]:
        """RAG için optimal bölümler oluşturur"""
        sections = []
        
        if total_pages <= max_pages:
            # Tek bölüm yeterli
            sections.append({
                'start_page': 1,
                'end_page': total_pages
            })
        else:
            # Çoklu bölüm gerekli
            # Optimal bölüm sayısını hesapla
            ideal_section_size = (min_pages + max_pages) // 2
            estimated_sections = math.ceil(total_pages / ideal_section_size)
            
            # Bölümleri oluştur
            pages_per_section = total_pages // estimated_sections
            remainder = total_pages % estimated_sections
            
            current_page = 1
            
            for i in range(estimated_sections):
                # Bazı bölümlere fazladan sayfa ekle
                section_size = pages_per_section
                if i < remainder:
                    section_size += 1
                
                # Minimum ve maksimum sınırları kontrol et
                section_size = max(min_pages, min(max_pages, section_size))
                
                end_page = min(current_page + section_size - 1, total_pages)
                
                sections.append({
                    'start_page': current_page,
                    'end_page': end_page
                })
                
                current_page = end_page + 1
                
                # Son sayfaya ulaşıldıysa dur
                if end_page >= total_pages:
                    break
        
        return sections
    
    def create_section_pdf(self, source_pdf_path: str, start_page: int, 
                          end_page: int, output_dir: str, section_num: int) -> str:
        """Belirtilen sayfa aralığında yeni PDF oluşturur"""
        try:
            with open(source_pdf_path, 'rb') as source_file:
                reader = pypdf.PdfReader(source_file)
                writer = pypdf.PdfWriter()
                
                # Sayfaları ekle (1-indexed'dan 0-indexed'a çevir)
                for page_num in range(start_page - 1, end_page):
                    if page_num < len(reader.pages):
                        writer.add_page(reader.pages[page_num])
                
                # Çıktı dosya adını oluştur
                output_filename = f"{section_num:02d}_Bolum_{start_page}-{end_page}.pdf"
                output_path = Path(output_dir) / output_filename
                
                # PDF'i kaydet
                with open(output_path, 'wb') as output_file:
                    writer.write(output_file)
                
                return str(output_path)
                
        except Exception as e:
            raise Exception(f"Bölüm PDF oluşturma hatası: {str(e)}")
    
    def extract_text_from_pages(self, pdf_path: str, start_page: int, end_page: int, use_ocr: bool = False) -> str:
        """Belirtilen sayfa aralığından metin çıkarır (OCR desteği ile)"""
        try:
            text = ""
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                total_pages = len(reader.pages)
                
                # use_ocr=True ise, tüm sayfalar için direkt OCR yap (metin kontrolü yapma)
                if use_ocr and self._check_ocr_available():
                    # end_page dahil olacak şekilde düzelt (1-indexed'den 0-indexed'a çevir)
                    actual_end_page = min(end_page, total_pages)
                    pages_to_process = actual_end_page - start_page + 1
                    print(f"📸 OCR modu: {pages_to_process} sayfa OCR ile işlenecek (sayfa {start_page}-{actual_end_page})...")
                    # Tüm sayfaları işle (end_page dahil) - range'e +1 ekleyerek end_page'i dahil et
                    for page_num in range(start_page - 1, actual_end_page + 1):  # +1 ekleyerek end_page'i dahil et
                        if page_num < 0 or page_num >= total_pages:
                            continue  # Geçersiz sayfa numarası, atla
                        try:
                            ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                            if ocr_text and len(ocr_text.strip()) > 0:
                                text += ocr_text + "\n"
                                if (page_num + 1) % 10 == 0 or (page_num + 1) == actual_end_page:
                                    print(f"📸 OCR: {page_num + 1}/{actual_end_page} sayfa işlendi...")
                            else:
                                # Boş metin ama hata değil (sayfa boş olabilir)
                                if (page_num + 1) % 20 == 0:
                                    print(f"⚠️ Sayfa {page_num + 1}: OCR ile metin çıkarılamadı (sayfa boş olabilir)")
                                text += f"[Sayfa {page_num + 1}: OCR ile metin çıkarılamadı]\n"
                        except Exception as ocr_error:
                            error_msg = str(ocr_error)
                            # İlk sayfada hata varsa detaylı mesaj göster
                            if page_num == 0:
                                print(f"❌ Sayfa {page_num + 1} için OCR hatası: {error_msg}")
                                if "poppler" in error_msg.lower():
                                    print("❌ Poppler kurulu değil! Sistem paketlerini kurun.")
                                elif "tesseract" in error_msg.lower():
                                    print("❌ Tesseract kurulu değil! Sistem paketlerini kurun.")
                            elif (page_num + 1) % 10 == 0:
                                # Her 10 sayfada bir özet mesaj
                                print(f"⚠️ Sayfa {page_num + 1} için OCR hatası (devam ediliyor...)")
                            text += f"[Sayfa {page_num + 1}: OCR hatası - {error_msg[:50]}]\n"
                            continue
                    print(f"✅ OCR tamamlandı: {pages_to_process} sayfa işlendi (sayfa {start_page}-{actual_end_page})")
                    return text
                
                # Normal mod: Önce metin çıkarmayı dene, yoksa OCR yap
                actual_end_page = min(end_page, total_pages)
                for page_num in range(start_page - 1, actual_end_page):  # range end_page'e kadar ama dahil değil
                    if page_num >= total_pages:
                        break
                    try:
                        page_text = reader.pages[page_num].extract_text()
                        
                        # Eğer metin yoksa ve OCR kullanılabilirse, OCR ile dene
                        if (not page_text or len(page_text.strip()) < 10) and (use_ocr or self._check_ocr_available()):
                            try:
                                ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                                if ocr_text:
                                    page_text = ocr_text
                                    print(f"✅ Sayfa {page_num + 1} için OCR ile metin çıkarıldı")
                            except Exception as ocr_error:
                                print(f"⚠️ Sayfa {page_num + 1} için OCR hatası: {str(ocr_error)}")
                        
                        if page_text:
                            text += page_text + "\n"
                    except Exception as e:
                        # Sayfa metin çıkarma hatası - OCR ile dene
                        if use_ocr or self._check_ocr_available():
                            try:
                                ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                                if ocr_text:
                                    text += ocr_text + "\n"
                                    continue
                            except Exception:
                                pass
                        text += f"[Sayfa {page_num + 1}: Metin çıkarılamadı]\n"
                        continue
            
            return text
        except Exception as e:
            raise Exception(f"Metin çıkarma hatası: {str(e)}")
    
    def extract_all_page_texts(self, pdf_path: str, use_ocr: bool = False) -> List[str]:
        """Tüm sayfaların metinlerini çıkarır (OCR desteği ile)"""
        try:
            page_texts = []
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                
                # Önce normal metin çıkarmayı dene
                for page_num in range(len(reader.pages)):
                    try:
                        page_text = reader.pages[page_num].extract_text()
                        page_texts.append(page_text if page_text else "")
                    except Exception as e:
                        page_texts.append("")
                        continue
                
                # Eğer use_ocr=True ise veya metin yoksa, OCR ile dene
                if use_ocr and self._check_ocr_available():
                    # Direkt OCR modu: Tüm sayfaları OCR ile işle (sınırlama olmadan)
                    print(f"📸 OCR modu: Tüm {len(reader.pages)} sayfa OCR ile işlenecek (sınırlama olmadan)...")
                    page_texts = []
                    for page_num in range(len(reader.pages)):
                        try:
                            ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                            page_texts.append(ocr_text if ocr_text else "")
                            if (page_num + 1) % 10 == 0 or (page_num + 1) == len(reader.pages):
                                print(f"📸 OCR: {page_num + 1}/{len(reader.pages)} sayfa işlendi...")
                        except Exception as e:
                            page_texts.append("")
                            if (page_num + 1) % 20 == 0:
                                print(f"⚠️ Sayfa {page_num + 1} için OCR hatası (devam ediliyor...)")
                            continue
                    print(f"✅ OCR tamamlandı: {len(reader.pages)} sayfa işlendi")
                elif (not any(page_texts) or all(len(t.strip()) < 10 for t in page_texts)) and self._check_ocr_available():
                    # Metin yoksa otomatik OCR
                    print("📸 PDF'de metin bulunamadı, OCR ile tüm sayfalar işleniyor...")
                    page_texts = []
                    for page_num in range(len(reader.pages)):
                        try:
                            ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                            page_texts.append(ocr_text if ocr_text else "")
                            if (page_num + 1) % 10 == 0:
                                print(f"📸 OCR: {page_num + 1}/{len(reader.pages)} sayfa işlendi...")
                        except Exception as e:
                            page_texts.append("")
                            continue
                    print("✅ OCR ile tüm sayfalar işlendi")
            
            return page_texts
        except Exception as e:
            raise Exception(f"Sayfa metinleri çıkarma hatası: {str(e)}")
    
    def create_intelligent_sections(self, pdf_path: str, total_pages: int, analyzer, use_ocr: bool = False) -> List[Dict[str, int]]:
        """AI kullanarak içerik bazlı optimal bölümler oluşturur"""
        try:
            # Tüm sayfaların metinlerini çıkar (OCR desteği ile)
            page_texts = self.extract_all_page_texts(pdf_path, use_ocr=use_ocr)
            
            # AI'dan bölüm önerileri al
            suggested_sections = analyzer.suggest_content_based_sections(page_texts, total_pages)
            
            # Bölümleri formatla
            sections = []
            for section in suggested_sections:
                sections.append({
                    'start_page': section['start_page'],
                    'end_page': section['end_page'],
                    'reason': section.get('reason', '')
                })
            
            return sections
            
        except Exception as e:
            print(f"Akıllı bölümleme hatası: {str(e)}")
            # Fallback: Basit eşit bölümleme
            return self.create_optimal_sections(pdf_path, total_pages, 3, 10)
    
    def get_pdf_metadata(self, pdf_path: str) -> Dict[str, Any]:
        """PDF metadata bilgilerini alır"""
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
