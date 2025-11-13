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
            # Tesseract'ın kurulu olup olmadığını kontrol et
            try:
                pytesseract.get_tesseract_version()
                self._ocr_available = True
            except Exception:
                self._ocr_available = False
        except ImportError:
            self._ocr_available = False
        
        return self._ocr_available
    
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
            
            # PDF sayfasını görüntüye çevir
            images = convert_from_path(
                pdf_path,
                first_page=page_num + 1,
                last_page=page_num + 1,
                dpi=300  # Yüksek çözünürlük için
            )
            
            if not images:
                return ""
            
            # Kullanılabilir dilleri al
            ocr_lang = self._get_available_ocr_languages()
            
            # OCR ile metin çıkar
            text = pytesseract.image_to_string(
                images[0],
                lang=ocr_lang
            )
            
            return text.strip()
        except ImportError:
            raise Exception("OCR kütüphaneleri kurulu değil. 'pip install pytesseract pdf2image pillow' ve 'brew install tesseract tesseract-lang' komutlarını çalıştırın.")
        except Exception as e:
            raise Exception(f"OCR hatası: {str(e)}")
    
    def analyze_pdf_structure(self, pdf_path: str) -> Dict[str, Any]:
        """PDF dosyasının yapısını analiz eder"""
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                total_pages = len(reader.pages)
                
                # İlk birkaç sayfaydan metin örneği al
                sample_text = ""
                sample_pages = min(3, total_pages)
                has_text = False
                needs_ocr = False
                
                for i in range(sample_pages):
                    try:
                        page_text = reader.pages[i].extract_text()
                        if page_text and len(page_text.strip()) > 0:
                            sample_text += page_text + "\n"
                            has_text = True
                        else:
                            # Metin yoksa OCR gerekebilir
                            needs_ocr = True
                    except Exception as e:
                        needs_ocr = True
                        continue
                
                # Eğer metin yoksa ve OCR kullanılabilirse, OCR ile dene
                if not has_text and needs_ocr and self._check_ocr_available():
                    print("📸 PDF'de metin bulunamadı, OCR ile metin çıkarılıyor...")
                    try:
                        ocr_text = self._extract_text_with_ocr(pdf_path, 0)
                        if ocr_text:
                            sample_text = ocr_text[:1000]
                            has_text = True
                            print("✅ OCR ile metin başarıyla çıkarıldı")
                    except Exception as ocr_error:
                        print(f"⚠️ OCR hatası: {str(ocr_error)}")
                
                return {
                    'total_pages': total_pages,
                    'sample_text': sample_text[:1000],  # İlk 1000 karakter
                    'has_text': has_text,
                    'needs_ocr': needs_ocr and not has_text
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
                
                for page_num in range(start_page - 1, end_page):
                    if page_num < len(reader.pages):
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
                
                # Eğer metin yoksa ve OCR kullanılabilirse, OCR ile dene
                if (not any(page_texts) or all(len(t.strip()) < 10 for t in page_texts)) and (use_ocr or self._check_ocr_available()):
                    print("📸 PDF'de metin bulunamadı, OCR ile tüm sayfalar işleniyor...")
                    page_texts = []
                    for page_num in range(len(reader.pages)):
                        try:
                            ocr_text = self._extract_text_with_ocr(pdf_path, page_num)
                            page_texts.append(ocr_text if ocr_text else "")
                            if (page_num + 1) % 5 == 0:
                                print(f"📸 OCR: {page_num + 1}/{len(reader.pages)} sayfa işlendi...")
                        except Exception as e:
                            page_texts.append("")
                            continue
                    print("✅ OCR ile tüm sayfalar işlendi")
            
            return page_texts
        except Exception as e:
            raise Exception(f"Sayfa metinleri çıkarma hatası: {str(e)}")
    
    def create_intelligent_sections(self, pdf_path: str, total_pages: int, analyzer) -> List[Dict[str, int]]:
        """AI kullanarak içerik bazlı optimal bölümler oluşturur"""
        try:
            # Tüm sayfaların metinlerini çıkar
            page_texts = self.extract_all_page_texts(pdf_path)
            
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
