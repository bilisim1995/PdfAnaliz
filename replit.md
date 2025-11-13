# PDF RAG Bölümlendirme Aracı

## Overview

This is a Streamlit-based web application that processes PDF documents and segments them into optimized chunks for RAG (Retrieval Augmented Generation) systems. The application offers two sectioning strategies: AI-powered intelligent sectioning that analyzes content to create semantically meaningful sections, and manual sectioning based on fixed page ranges. Using DeepSeek AI, it generates comprehensive metadata including titles, descriptions, and keywords for each section. Users can upload PDFs from their computer or download them from URLs, and the system automatically creates document sections with AI-generated metadata to improve retrieval performance in RAG applications.

## Recent Changes (October 23, 2025)

- Implemented **Two-Phase Workflow**: Separated PDF processing into analysis (JSON preview) and splitting (actual file creation) phases
  - Phase 1: Analyze PDF and generate metadata with JSON preview
  - Phase 2: User confirms and splits PDFs based on prepared metadata
- Added **Turkish Character Transliteration**: PDF filenames automatically convert Turkish characters (ç, ğ, ı, ö, ş, ü) to English equivalents (c, g, i, o, s, u)
- Enhanced **Filename Generation**: Intelligent filenames from AI-generated titles or original PDF names with section numbers and page ranges
- Improved **URL Download Reliability**: Added retry logic with exponential backoff for network resilience
- **Security Enhancement**: Removed hardcoded API key, now requires environment variable or user input
- **State Management**: Proper session state reset for multiple PDF processing without manual restart
- **Keywords Format Update**: Keywords now preserve Turkish characters and spaces instead of underscores (e.g., "otopark yönetmeliği" not "otopark_yönetmeliği")
- **API Upload Integration**: Added "Verileri Yükle" button to POST split PDFs and metadata to bulk upload endpoint
  - Supports multipart/form-data with files, category, institution, belge_adi, and metadata fields
  - Displays API response with batch_id and upload status
  - Configurable via sidebar settings (API URL, token, category, institution, belge_adi)
- **Login System**: Added authentication flow with email/password login
  - Users must login before accessing PDF processing features
  - API token automatically obtained from login callback response
  - Session state manages authentication (access_token, refresh_token, user_info)
  - Login page displays API base URL, email, and password inputs
  - Logout functionality clears session and returns to login page
- **Extended Timeouts**: Increased timeout limits for long-running operations
  - Streamlit script execution: 1800 seconds (30 minutes)
  - DeepSeek API calls: 120 seconds (2 minutes)
  - PDF download from URL: 120 seconds (2 minutes)
  - Login API call: 60 seconds (1 minute)
  - Bulk upload API call: 300 seconds (5 minutes)
- **Smart Document Naming**: AI-powered document name suggestions
  - Analyzes PDF content (first 3 pages) to suggest relevant document names
  - Displays suggestion below "Belge Adı" field in sidebar
  - "Öneriyi Uygula" button applies suggestion to input field
  - Automatically updates during PDF analysis phase
- **Low Page Count Warning**: Added warning for PDFs with 5 or fewer pages
  - Alerts users that splitting may not be necessary for short documents
  - Suggests using the PDF directly without splitting
  - Still allows proceeding with analysis if user prefers

## Previous Changes (October 22, 2025)

- Added **Intelligent Content-Based Sectioning**: AI analyzes PDF content to create semantically coherent sections based on topic changes and content flow
- Implemented **Dual Sectioning Strategies**: Users can choose between AI-powered intelligent sectioning or manual fixed-page sectioning
- Enhanced **Section Reasoning**: AI provides explanations for why each section was created
- Improved **Error Handling**: Better error messages when DeepSeek API encounters issues
- Increased **Maximum Page Limit**: Raised from 20 to 30 pages per section for manual mode

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture

**Decision**: Streamlit web framework  
**Rationale**: Provides rapid development of data-centric applications with minimal frontend code. Streamlit's session state management handles processing workflows and maintains user context across interactions.

**Key Components**:
- Session state management for tracking processing status, JSON output, and output directories
- File upload interface supporting both local files and URL-based downloads
- Sidebar configuration panel for API keys and processing parameters

### Backend Architecture

**Decision**: Modular Python architecture with separation of concerns  
**Rationale**: Each major functionality is isolated into dedicated modules for maintainability and testability.

**Core Modules**:

1. **PDFProcessor** (`pdf_processor.py`)
   - Handles PDF structure analysis and page counting
   - **Dual Sectioning Modes**:
     - `create_optimal_sections()`: Creates sections based on min/max page parameters (manual mode)
     - `create_intelligent_sections()`: Uses AI to analyze content and create semantically meaningful sections
   - `extract_all_page_texts()`: Extracts text from all pages for content analysis
   - Uses pypdf library for PDF manipulation
   - Extracts sample text from initial pages for structure analysis

2. **DeepSeekAnalyzer** (`deepseek_analyzer.py`)
   - Integrates with DeepSeek AI API via OpenAI client interface
   - **Content Analysis Functions**:
     - `analyze_section_content()`: Generates metadata (title, description, keywords) for individual sections
     - `suggest_content_based_sections()`: Analyzes entire PDF to suggest optimal section boundaries
   - Implements intelligent section validation and fallback strategies
   - Content length limiting (8000 characters for metadata, sampling for sectioning) for token management
   - Handles edge cases like insufficient text content
   - Uses Turkish language for metadata generation
   - Provides reasoning for each suggested section boundary

3. **Utils** (`utils.py`)
   - PDF download functionality from URLs with retry logic and validation
   - Content-type verification and PDF magic number checking
   - Temporary file management with unique UUID-based naming
   - HTTP request handling with proper headers, timeouts, and exponential backoff
   - **Turkish Character Transliteration**: `transliterate_turkish()` converts Turkish chars to English
   - **Intelligent Filename Generation**: `create_pdf_filename()` creates sanitized, English-compatible PDF filenames

### Data Processing Flow

**Decision**: Two-phase sequential processing pipeline  
**Rationale**: Ensures data integrity, allows for error handling at each stage, and gives users preview/confirmation before file creation.

**Phase 1 - Analysis and Preview**:
1. PDF acquisition (upload or URL download)
2. Structure analysis (page count, text extraction)
3. Section creation based on page ranges or AI content analysis
4. AI-powered metadata generation per section
5. JSON output generation with structured metadata
6. Preview JSON output to user

**Phase 2 - PDF Splitting**:
1. User confirms metadata from preview
2. Create output directories
3. Split PDF files according to prepared metadata
4. Generate filenames with Turkish character transliteration
5. Save JSON metadata file alongside PDF sections

### AI Integration

**Decision**: DeepSeek API for content analysis  
**Rationale**: Provides cost-effective, high-quality Turkish language support for metadata generation.

**Integration Details**:
- Uses OpenAI-compatible client interface
- Custom base URL pointing to DeepSeek API
- Structured prompt engineering for consistent metadata format
- Token optimization through content truncation
- Graceful degradation for sections with insufficient content

### Error Handling Strategy

**Decision**: Defensive programming with explicit error messages  
**Rationale**: Provides clear feedback for debugging and user guidance.

**Error Handling Patterns**:
- URL validation and content-type checking before processing
- PDF magic number verification for downloaded files
- File size validation (minimum 1KB)
- Try-catch blocks with descriptive error messages
- Fallback metadata for empty or invalid sections

### File Management

**Decision**: Temporary file storage with UUID-based naming  
**Rationale**: Prevents naming conflicts and automatic cleanup via OS temp directory management.

**Implementation**:
- Uses Python's tempfile module for secure temporary storage
- UUID hex strings for unique file identification
- No persistent storage requirement reduces infrastructure complexity

## External Dependencies

### AI Services
- **DeepSeek API**: Primary AI service for content analysis and metadata generation
  - OpenAI-compatible API interface
  - Base URL: https://api.deepseek.com
  - Requires API key authentication (default configured in environment)

### Python Libraries
- **Streamlit**: Web application framework for UI and interaction flow
- **openai**: Client library for DeepSeek API integration
- **pypdf**: PDF parsing and text extraction
- **requests**: HTTP client for URL-based PDF downloads

### External Integrations
- PDF downloads from arbitrary URLs with User-Agent spoofing
- HTTP request handling with 30-second timeout
- Content validation through headers and magic number verification

### Configuration
- Environment variable support for `DEEPSEEK_API_KEY`
- API key required via environment variable (no hardcoded fallback for security)
- Configurable section parameters (min/max pages per section)

## Modüler Scraper Yapısı

### Genel Bakış

Proje, farklı kurumlar için modüler bir scraper yapısına sahiptir. Her kurum için ayrı bir scraper modülü oluşturulabilir ve bu modüller `scrapers/` klasöründe tutulur.

### Mevcut Modüller

1. **SGK KAYSİS Scraper** (`scrapers/sgk_kaysis_scraper.py`)
   - Sosyal Güvenlik Kurumu'nun KAYSİS sitesinden mevzuat tarama
   - URL: `https://kms.kaysis.gov.tr/Home/Kurum/22620739`
   - Accordion yapısından mevzuat başlıkları ve linklerini çıkarır
   - API ile yüklü mevzuatları karşılaştırır

### Modül Yapısı

```
scrapers/
├── __init__.py                    # Modül export'ları
└── sgk_kaysis_scraper.py          # SGK KAYSİS scraper modülü
```

### Yeni Kurum Scraper'ı Ekleme

Farklı bir kurum için scraper eklemek için aşağıdaki adımları izleyin:

#### 1. Yeni Scraper Modülü Oluştur

`scrapers/` klasöründe yeni bir dosya oluşturun:
- Dosya adı: `{kurum_adi}_scraper.py` (örn: `adliye_scraper.py`, `saglik_scraper.py`)
- Dosya formatı: Python modülü

#### 2. Temel Fonksiyonları Implement Et

Yeni scraper modülünde aşağıdaki fonksiyonları implement edin:

```python
"""
{Kurum Adı} Scraper Module
{Kurum açıklaması}
"""
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
import re
import json

def scrape_{kurum}_mevzuat(url: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    {Kurum} sitesinden mevzuatları tarar
    
    Args:
        url: Taranacak kurum URL'i
    
    Returns:
        Tuple[List[Dict[str, Any]], Dict[str, Any]]: (all_sections, stats)
            - all_sections: Tüm bölümler ve mevzuatlar
            - stats: İstatistikler
    """
    # Scraping mantığınızı buraya yazın
    pass

def print_results_to_console(all_sections: List[Dict[str, Any]], stats: Dict[str, Any]):
    """Sonuçları konsola yazdırır"""
    # Konsol çıktısı mantığınızı buraya yazın
    pass
```

#### 3. Yardımcı Fonksiyonlar (Opsiyonel)

Eğer kurumun özel ihtiyaçları varsa, yardımcı fonksiyonlar ekleyebilirsiniz:
- `normalize_text()`: Metin normalizasyonu
- `is_title_similar()`: Başlık karşılaştırması
- `turkish_title()`: Türkçe karakter desteği
- `get_uploaded_documents()`: API'den döküman çekme (ortak kullanılabilir)

#### 4. `__init__.py` Dosyasını Güncelle

`scrapers/__init__.py` dosyasına yeni modülü ekleyin:

```python
from .{kurum_adi}_scraper import (
    scrape_{kurum}_mevzuat,
    print_results_to_console
)

__all__ = [
    # ... mevcut export'lar
    'scrape_{kurum}_mevzuat',
    'print_results_to_console'
]
```

#### 5. `api_server.py`'de Kullan

`api_server.py` dosyasında yeni scraper'ı import edin ve endpoint'lerde kullanın:

```python
from scrapers.{kurum_adi}_scraper import (
    scrape_{kurum}_mevzuat,
    print_results_to_console
)

# Endpoint'lerde kullanım
@app.post("/api/{kurum}/scrape")
async def scrape_{kurum}(req: PortalScanRequest):
    # ... kurum URL'ini kurumlar.json'dan çek
    all_sections, stats = scrape_{kurum}_mevzuat(url=kurum_url)
    print_results_to_console(all_sections, stats)
    # ... response hazırla
```

#### 6. `kurumlar.json` Dosyasını Güncelle

Yeni kurumu `kurumlar.json` dosyasına ekleyin:

```json
{
  "kurumlar": [
    {
      "id": "{kurum_id}",
      "kurum_adi": "{Kurum Adı}",
      "url": "{Kurum URL'i}"
    }
  ]
}
```

### Örnek: Yeni Kurum Ekleme

**Adım 1:** `scrapers/adliye_scraper.py` dosyası oluştur

```python
"""
Adliye Bakanlığı Scraper Module
Adliye Bakanlığı mevzuat tarama modülü
"""
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Tuple
import re

def scrape_adliye_mevzuat(url: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adliye Bakanlığı sitesinden mevzuatları tarar"""
    # Scraping mantığınız
    all_sections = []
    stats = {
        'total_sections': 0,
        'total_items': 0,
        'uploaded_documents_count': 0,
        'sections_stats': []
    }
    return all_sections, stats

def print_results_to_console(all_sections: List[Dict[str, Any]], stats: Dict[str, Any]):
    """Sonuçları konsola yazdırır"""
    print(f"Toplam {stats.get('total_items', 0)} mevzuat bulundu")
```

**Adım 2:** `scrapers/__init__.py` güncelle

```python
from .adliye_scraper import scrape_adliye_mevzuat, print_results_to_console
```

**Adım 3:** `api_server.py`'de kullan

```python
from scrapers.adliye_scraper import scrape_adliye_mevzuat

@app.post("/api/adliye/scrape")
async def scrape_adliye(req: PortalScanRequest):
    # ... implementation
```

**Adım 4:** `kurumlar.json` güncelle

```json
{
  "kurumlar": [
    {
      "id": "adliye_bakanligi_id",
      "kurum_adi": "Adliye Bakanlığı",
      "url": "https://example.com/adliye/mevzuat"
    }
  ]
}
```

### Ortak Fonksiyonlar

Eğer birden fazla scraper'da ortak kullanılacak fonksiyonlar varsa, bunları `scrapers/sgk_kaysis_scraper.py` içinden import edebilir veya `scrapers/utils.py` gibi bir ortak modül oluşturabilirsiniz:

```python
# Yeni scraper'da
from scrapers.sgk_kaysis_scraper import (
    get_uploaded_documents,
    check_if_document_exists,
    normalize_text
)
```

### Best Practices

1. **Modülerlik**: Her kurum için ayrı modül, kod tekrarından kaçının
2. **Hata Yönetimi**: Try-except blokları ile hataları yakalayın
3. **Logging**: Print veya logging ile işlem durumunu bildirin
4. **Dokümantasyon**: Fonksiyon docstring'leri ekleyin
5. **Tip Hints**: Python type hints kullanın
6. **Test**: Her scraper için test senaryoları oluşturun

### Notlar

- Mevcut `scrape_sgk_mevzuat` fonksiyonu SGK KAYSİS'e özeldir
- Farklı kurumlar farklı HTML yapılarına sahip olabilir
- Her kurum için scraping mantığı özelleştirilmelidir
- API entegrasyonu (yüklü mevzuat kontrolü) ortak kullanılabilir