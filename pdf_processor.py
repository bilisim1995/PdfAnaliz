import pypdf
from pathlib import Path
import tempfile
import math
from typing import List, Dict, Any

class PDFProcessor:
    """PDF işleme ve bölümlendirme sınıfı"""
    
    def __init__(self):
        pass
    
    def analyze_pdf_structure(self, pdf_path: str) -> Dict[str, Any]:
        """PDF dosyasının yapısını analiz eder"""
        try:
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                total_pages = len(reader.pages)
                
                # İlk birkaç sayfaydan metin örneği al
                sample_text = ""
                sample_pages = min(3, total_pages)
                
                for i in range(sample_pages):
                    try:
                        page_text = reader.pages[i].extract_text()
                        sample_text += page_text + "\n"
                    except Exception as e:
                        continue
                
                return {
                    'total_pages': total_pages,
                    'sample_text': sample_text[:1000],  # İlk 1000 karakter
                    'has_text': len(sample_text.strip()) > 0
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
    
    def extract_text_from_pages(self, pdf_path: str, start_page: int, end_page: int) -> str:
        """Belirtilen sayfa aralığından metin çıkarır"""
        try:
            text = ""
            with open(pdf_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                
                for page_num in range(start_page - 1, end_page):
                    if page_num < len(reader.pages):
                        try:
                            page_text = reader.pages[page_num].extract_text()
                            text += page_text + "\n"
                        except Exception as e:
                            # Sayfa metin çıkarma hatası - devam et
                            text += f"[Sayfa {page_num + 1}: Metin çıkarılamadı]\n"
                            continue
            
            return text
        except Exception as e:
            raise Exception(f"Metin çıkarma hatası: {str(e)}")
    
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
