"""
FastAPI Server for SGK Scraper
"""
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Tuple
import uvicorn
from scrapers.kaysis_scraper import (
    scrape_kaysis_mevzuat,
    print_results_to_console,
    get_uploaded_documents,
    get_proxy_from_db,
    turkish_sentence_case,
    is_title_similar
)
import threading
import re
import os
from pathlib import Path
import json
import subprocess
import platform
from datetime import datetime

# curl_cffi import kontrolü
try:
    from curl_cffi import requests
    from curl_cffi.requests import CurlMime
    CURL_CFFI_AVAILABLE = True
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False
    CurlMime = None

from pdf_processor import PDFProcessor
from deepseek_analyzer import DeepSeekAnalyzer
from utils import download_pdf_from_url, create_output_directories, create_pdf_filename, validate_pdf_file
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError
from bson import ObjectId
import urllib.parse
import unicodedata
import shutil
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

# .env dosyasını yükle
load_dotenv()

# Stdout'u line-buffered yap (anlık log görünümü için)
import sys
if sys.stdout.isatty():
    # Terminal'de çalışıyorsa line buffering
    sys.stdout.reconfigure(line_buffering=True)
else:
    # Systemd/journalctl için unbuffered
    import os
    os.environ['PYTHONUNBUFFERED'] = '1'
    # sys.stdout'u flush etmek için wrapper
    class Unbuffered:
        def __init__(self, stream):
            self.stream = stream
        def write(self, data):
            self.stream.write(data)
            self.stream.flush()
        def __getattr__(self, attr):
            return getattr(self.stream, attr)
    sys.stdout = Unbuffered(sys.stdout)
    sys.stderr = Unbuffered(sys.stderr)

# Swagger/OpenAPI kategorileri
openapi_tags = [
    {
        "name": "SGK Scraper",
        "description": "SGK mevzuatlarını tarama, analiz ve yükleme işlemleri."
    },
    {
        "name": "e-Devlet Scraper",
        "description": "Türkiye.gov.tr hizmet linklerini toplama ve kaydetme."
    },
    {
        "name": "Links",
        "description": "e-Devlet linkleri için listeleme, oluşturma, güncelleme ve silme işlemleri."
    },
    {
        "name": "Kurumlar",
        "description": "Kurum kayıtları için CRUD ve logo yükleme işlemleri."
    },
    {
        "name": "Kurum Duyuru",
        "description": "Kurum duyuruları için CRUD işlemleri."
    },
    {
        "name": "MongoDB",
        "description": "Metadata ve Content koleksiyonları için yönetim endpointleri."
    },
    {
        "name": "Proxy",
        "description": "Proxy ayarları için CRUD işlemleri."
    },
    {
        "name": "Health",
        "description": "Servis sağlık kontrolü."
    }
]

app = FastAPI(
    title="SGK Scraper API",
    version="1.0.0",
    description="SGK ve e-Devlet entegrasyonları için REST API",
    redoc_url=None,
    openapi_tags=openapi_tags
)

# CORS middleware ekle - Tüm origin'lere izin ver
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tüm origin'lere izin ver
    allow_credentials=False,  # allow_origins=["*"] ile birlikte True olamaz
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],  # Tüm HTTP metodlarına izin ver
    allow_headers=["*"],  # Tüm header'lara izin ver
    expose_headers=["*"],  # Tüm header'ları expose et
    max_age=3600,  # Preflight cache süresi (1 saat)
)

# Son tarama sonuçlarından id -> item eşlemesini tutmak için önbellek
# { id: { "section_title": str, "baslik": str, "link": str } }
last_item_map: Dict[int, Dict[str, Any]] = {}


def _load_config() -> Optional[Dict[str, Any]]:
    """Config dosyasını yükler"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _get_mongo_collections():
    """MongoDB client ve ilgili koleksiyonları döner (metadata, content)."""
    client = _get_mongodb_client()
    if not client:
        return None, None, None
    database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
    metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
    content_collection_name = os.getenv("MONGODB_CONTENT_COLLECTION", "content")
    db = client[database_name]
    return client, db[metadata_collection_name], db[content_collection_name]


def normalize_for_exact_match(s: str) -> str:
    """Tam eşleşme için metni normalize eder (Türkçe karakter ve boşluk desteği)"""
    if not s:
        return ""
    import unicodedata
    # Unicode normalizasyonu
    s = unicodedata.normalize('NFC', s)
    s = s.replace("i\u0307", "i")
    # Türkçe küçük harfe çevirme
    s = s.replace('I', 'ı').replace('İ', 'i').lower()
    # Fazla boşlukları temizle ve trim et
    s = re.sub(r'\s+', ' ', s.strip())
    return s


def to_title(s: str) -> str:
    """Türkçe karakterleri dikkate alarak Title Case'e çevirir"""
    if not s:
        return ""
    import unicodedata
    # Unicode normalizasyonu
    s = unicodedata.normalize('NFC', s)
    s = s.replace("i\u0307", "i")
    # Türkçe küçük harfe çevirme
    tmp = s.replace('I', 'ı').replace('İ', 'i').lower()
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


class ScrapeResponse(BaseModel):
    success: bool
    message: str
    data: Dict[str, Any] = {}


class PortalScanRequest(BaseModel):
    id: str = Field(..., description="Kurum MongoDB ObjectId")
    detsis: str = Field(..., description="DETSIS numarası (KAYSİS kurum ID'si)")
    type: str = Field(default="kaysis", description="Scraper tipi (varsayılan: kaysis)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "68bbf6df8ef4e8023c19641d",
                "detsis": "60521689",
                "type": "kaysis"
            }
        }
    }


class PortalScanWithDataRequest(BaseModel):
    id: Optional[str] = Field(default=None, description="Kurum MongoDB ObjectId (opsiyonel, kurum_id ile birlikte kullanılabilir)")
    kurum_id: Optional[str] = Field(default=None, description="Kurum MongoDB ObjectId (opsiyonel, id ile birlikte kullanılabilir)")
    detsis: Optional[str] = Field(default=None, description="DETSIS numarası (opsiyonel, MongoDB'den alınır)")
    type: str = Field(default="kaysis", description="Scraper tipi (varsayılan: kaysis)")
    sections: List[Dict[str, Any]] = Field(..., description="Önceden taranmış mevzuat verileri (zorunlu, scraper çalıştırılmaz)")
    stats: Optional[Dict[str, Any]] = Field(default=None, description="Önceden taranmış istatistikler (opsiyonel)")

    def __init__(self, **data):
        # Eğer 'data' wrapper'ı varsa (generate-json response formatı), içindeki değerleri çıkar
        if 'data' in data and isinstance(data['data'], dict):
            data_wrapper = data.pop('data')
            # data içindeki değerleri ana seviyeye taşı
            for key, value in data_wrapper.items():
                if key not in data:  # Sadece yoksa ekle, varsa üzerine yazma
                    data[key] = value
        
        # id veya kurum_id'den birini normalize et
        if 'kurum_id' in data and 'id' not in data:
            data['id'] = data.pop('kurum_id')
        elif 'kurum_id' in data and 'id' in data:
            # İkisi de varsa id'yi kullan, kurum_id'yi kaldır
            data.pop('kurum_id', None)
        super().__init__(**data)

    model_config = {
        "json_schema_extra": {
            "example": {
                "kurum_id": "68bbf6df8ef4e8023c19641d",
                "detsis": "60521689",
                "type": "kaysis",
                "sections": [
                    {
                        "section_title": "Kanunlar",
                        "items": [
                            {
                                "baslik": "Örnek Kanun",
                                "link": "https://kms.kaysis.gov.tr/Home/Goster/123"
                            }
                        ]
                    }
                ],
                "stats": {
                    "total_sections": 1,
                    "total_items": 1
                }
            }
        }
    }


class GenerateJsonRequest(BaseModel):
    id: Optional[str] = Field(default=None, description="Kurum MongoDB ObjectId (opsiyonel, kurum_id ile birlikte kullanılabilir)")
    kurum_id: Optional[str] = Field(default=None, description="Kurum MongoDB ObjectId (opsiyonel, id ile birlikte kullanılabilir)")
    type: str = Field(default="kaysis", description="Scraper tipi (varsayılan: kaysis)")

    def __init__(self, **data):
        # id veya kurum_id'den birini normalize et
        if 'kurum_id' in data and 'id' not in data:
            data['id'] = data.pop('kurum_id')
        elif 'kurum_id' in data and 'id' in data:
            # İkisi de varsa id'yi kullan, kurum_id'yi kaldır
            data.pop('kurum_id', None)
        super().__init__(**data)

    model_config = {
        "json_schema_extra": {
            "example": {
                "kurum_id": "68bbf6df8ef4e8023c19641d",
                "type": "kaysis"
            }
        }
    }


class ProcessRequest(BaseModel):
    kurum_id: str = Field(..., description="Kurum MongoDB ObjectId")
    detsis: str = Field(..., description="DETSIS numarası (KAYSİS kurum ID'si)")
    type: str = Field(default="kaysis", description="Scraper tipi (varsayılan: kaysis)")
    link: str = Field(..., description="PDF indirme linki")
    mode: str = Field(default="t", description="İşlem modu: 'm' (MevzuatGPT), 'p' (Portal), 't' (Tamamı)")
    category: Optional[str] = Field(default=None, description="Belge kategorisi (opsiyonel)")
    document_name: Optional[str] = Field(default=None, description="Belge adı (opsiyonel)")
    use_ocr: bool = Field(default=False, description="OCR kullanımı: True ise tüm sayfalar OCR ile işlenir, False ise OCR kullanılmaz (varsayılan: False)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "kurum_id": "68bbf6df8ef4e8023c19641d",
                "detsis": "60521689",
                "type": "kaysis",
                "link": "https://kms.kaysis.gov.tr/Home/Goster/104890",
                "mode": "t",
                "category": "Kanunlar",
                "document_name": "Türkiye cumhuriyeti hükümeti ile tunus cumhuriyeti hükümeti arasında sosyal güvenlik anlaşmasının onaylanmasının uygun bulunduğuna dair kanun",
                "use_ocr": True
            }
        }
    }


class ProcessData(BaseModel):
    category: str
    institution: str
    document_name: str
    output_dir: Optional[str] = None
    sections_count: int
    upload_response: Optional[Dict[str, Any]] = None


class ProcessResponse(BaseModel):
    success: bool
    message: str
    data: Optional[ProcessData] = None


@app.get("/", tags=["Health"], summary="API kök")
async def root():
    """API root endpoint"""
    return {
        "message": "SGK Scraper API",
        "version": "1.0.0",
        "endpoints": {
            "POST /api/mevzuatgpt/scrape": "Kurum mevzuatlarını tarar ve konsola yazdırır",
            "POST /api/mevzuatgpt/scrape-with-data": "Kurum mevzuatlarını tarar veya gönderilen JSON verilerini kullanır",
            "POST /api/mevzuatgpt/generate-json": "Sadece tarama yapar ve JSON oluşturur (karşılaştırma yapmaz)"
        }
    }


@app.post("/api/mevzuatgpt/scrape", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="Kurum mevzuat tarama")
async def scrape_mevzuatgpt(req: PortalScanRequest):
    """
    Belirtilen kurumun mevzuatlarını tarar ve sonuçları konsola yazdırır.
    type parametresi ile scraper tipi belirlenir (şu an için sadece 'kaysis' desteklenir).
    """
    try:
        print("\n" + "="*80)
        print(f"🚀 API Endpoint'ten Kurum Mevzuat Tarama İsteği Alındı (Kurum ID: {req.id}, Type: {req.type})")
        print("="*80)
        
        # Type kontrolü
        if req.type.lower() != "kaysis":
            return ScrapeResponse(
                success=False,
                message=f"Desteklenmeyen scraper tipi: {req.type}. Şu an için sadece 'kaysis' desteklenmektedir.",
                data={"error": "UNSUPPORTED_TYPE", "type": req.type}
            )
        
        # MongoDB'den kurum bilgisini çek
        kurum_adi = None
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                db = client[database_name]
                kurumlar_collection = db["kurumlar"]
                from bson import ObjectId
                kurum_doc = kurumlar_collection.find_one({"_id": ObjectId(req.id)})
                if kurum_doc:
                    kurum_adi = kurum_doc.get("kurum_adi", "Bilinmeyen Kurum")
                client.close()
        except Exception as e:
            print(f"⚠️ MongoDB'den kurum bilgisi alınamadı: {str(e)}")
            kurum_adi = "Bilinmeyen Kurum"
        
        print(f"📋 Kurum: {kurum_adi}")
        print(f"🔢 DETSIS: {req.detsis}")
        
        # Önce API'den yüklü documents'ları çek (çerez kullanmadan, direkt API)
        uploaded_docs = []
        # MongoDB'den portal'da bulunan pdf_adi'ları çek
        portal_docs = []
        cfg = _load_config()
        if cfg:
            token = _login_with_config(cfg)
            if token:
                api_base_url = cfg.get("api_base_url")
                print(f"📡 API'den yüklü documents çekiliyor...")
                try:
                    uploaded_docs = get_uploaded_documents(api_base_url, token, use_streamlit=False)
                    print(f"✅ {len(uploaded_docs)} document bulundu")
                    # Debug: İlk birkaç belgenin tüm alanlarını yazdır
                    if uploaded_docs:
                        print(f"🔍 DEBUG - İlk 3 belgenin tüm alanları:")
                        for i, doc in enumerate(uploaded_docs[:3]):
                            print(f"   Belge {i+1}: {doc}")
                        # Tüm olası alan isimlerini kontrol et
                        all_fields = set()
                        for doc in uploaded_docs[:10]:
                            all_fields.update(doc.keys())
                        print(f"🔍 DEBUG - Belgelerde bulunan alan isimleri: {sorted(all_fields)}")
                except Exception as e:
                    print(f"⚠️ Documents çekme hatası: {str(e)}")
                    import traceback
                    traceback.print_exc()

        # MongoDB metadata.pdf_adi -> portal_docs
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
                db = client[database_name]
                metadata_collection = db[metadata_collection_name]
                # Sadece pdf_adi alanını al
                cursor = metadata_collection.find({}, {"pdf_adi": 1})
                count = 0
                for doc in cursor:
                    val = (doc.get("pdf_adi") or "").strip()
                    if val:
                        portal_docs.append({"pdf_adi": val})
                        count += 1
                client.close()
                print(f"✅ MongoDB'den {count} pdf_adi okundu (portal karşılaştırması için)")
        except Exception as e:
            print(f"⚠️ MongoDB portal listesi okunamadı: {str(e)}")
        
        # KAYSİS scraper'ı kullan
        if req.type.lower() == "kaysis":
            all_sections, stats = scrape_kaysis_mevzuat(detsis=req.detsis)
            print_results_to_console(all_sections, stats)
        
        # Response hazırla (benzersiz item id'leri, uploaded durumu ve bölüm başlık temizleme)
        item_id_counter = 1
        response_sections = []
        # Önbelleği sıfırla
        global last_item_map
        last_item_map = {}
        for section in all_sections:
            raw_title = section['section_title']
            # Sonunda kalan sayıları temizle (örn: "Kanunlar4" -> "Kanunlar")
            clean_title = re.sub(r"\d+\s*$", "", raw_title).strip()
            items = section.get('items', [])
            items_with_ids = []
            for item in items:
                # Yükleme durumunu belirle - tam eşleşme (normalize edilmiş)
                item_baslik = item.get('baslik', '')
                item_normalized = normalize_for_exact_match(item_baslik)
                is_uploaded = False
                
                # API'den gelen belgelerle karşılaştır (tam eşleşme)
                for doc in uploaded_docs:
                    belge_adi = doc.get("belge_adi", "")
                    if belge_adi:
                        belge_normalized = normalize_for_exact_match(belge_adi)
                        if item_normalized == belge_normalized:
                            is_uploaded = True
                            break
                
                # Portal (MongoDB metadata.pdf_adi karşılaştırması) - tam eşleşme
                is_in_portal = False
                for doc in portal_docs:
                    pdf_adi = doc.get("pdf_adi", "")
                    if pdf_adi:
                        pdf_normalized = normalize_for_exact_match(pdf_adi)
                        if item_normalized == pdf_normalized:
                            is_in_portal = True
                            break
                
                # Benzersiz id ver ve önbelleğe yaz
                item_payload = {
                    "id": item_id_counter,
                    "mevzuatgpt": is_uploaded,
                    "portal": is_in_portal,
                    "baslik": item.get('baslik', ''),
                    "link": item.get('link', '')
                }
                items_with_ids.append(item_payload)

                # Önbelleğe kategori bilgisini de ekleyerek koy
                last_item_map[item_id_counter] = {
                    "section_title": clean_title,
                    "baslik": item_payload["baslik"],
                    "link": item_payload["link"]
                }
                item_id_counter += 1
            response_sections.append({
                "section_title": clean_title,
                "items_count": len(items_with_ids),
                "items": items_with_ids
            })
        
        # sections_stats'ı is_title_similar ile yeniden hesapla
        sections_stats_clean = []
        for section in all_sections:
            raw_title = section['section_title']
            clean_title = re.sub(r"\d+\s*$", "", raw_title).strip()
            items = section.get('items', [])
            
            uploaded_count = 0
            not_uploaded_count = 0
            
            for item in items:
                item_baslik = item.get('baslik', '')
                item_normalized = normalize_for_exact_match(item_baslik)
                is_uploaded = False
                
                # API'den gelen belgelerle karşılaştır (tam eşleşme)
                for doc in uploaded_docs:
                    belge_adi = doc.get("belge_adi", "")
                    if belge_adi:
                        belge_normalized = normalize_for_exact_match(belge_adi)
                        if item_normalized == belge_normalized:
                            is_uploaded = True
                            break
                
                if is_uploaded:
                    uploaded_count += 1
                else:
                    not_uploaded_count += 1
            
            sections_stats_clean.append({
                "section_title": clean_title,
                "total": len(items),
                "uploaded": uploaded_count,
                "not_uploaded": not_uploaded_count
            })
        
        response_data = {
            "total_sections": stats.get('total_sections', 0),
            "total_items": stats.get('total_items', 0),
            "uploaded_documents_count": stats.get('uploaded_documents_count', 0),
            "sections": response_sections,
            "sections_stats": sections_stats_clean
        }
        
        return ScrapeResponse(
            success=True,
            message=f"{kurum_adi} tarama işlemi başarıyla tamamlandı. Sonuçlar konsola yazdırıldı.",
            data=response_data
        )
        
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Scraping işlemi sırasında hata oluştu: {str(e)}"
        )


@app.post("/api/mevzuatgpt/scrape-with-data", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="JSON veri ile karşılaştırma ve finalize")
async def scrape_mevzuatgpt_with_data(req: PortalScanWithDataRequest):
    """
    Gönderilen JSON verilerini kullanarak API/Elasticsearch karşılaştırması yapar ve finalize eder.
    Scraper çalıştırılmaz, sadece gönderilen JSON verisi ile işlem yapılır.
    Adımlar: 1) Kurum bilgisi, 2) API'den belgeler, 3) MongoDB'den belgeler, 4) Karşılaştırma, 5) Finalize
    """
    try:
        print("\n" + "="*80)
        print(f"🚀 JSON Veri ile Karşılaştırma İsteği Alındı")
        
        # id veya kurum_id kontrolü
        kurum_id = req.id or getattr(req, 'kurum_id', None)
        if not kurum_id:
            return ScrapeResponse(
                success=False,
                message="Kurum ID (id veya kurum_id) gönderilmedi.",
                data={"error": "KURUM_ID_REQUIRED"}
            )
        
        print(f"📋 Kurum ID: {kurum_id}, Type: {req.type}")
        
        # Sections kontrolü - zorunlu
        if not req.sections or len(req.sections) == 0:
            return ScrapeResponse(
                success=False,
                message="JSON verisi (sections) gönderilmedi. Bu endpoint sadece JSON verisi ile çalışır.",
                data={"error": "NO_SECTIONS_PROVIDED"}
            )
        
        print(f"📦 Gönderilen JSON verisi kullanılacak ({len(req.sections)} bölüm)")
        print("="*80)
        
        # Type kontrolü
        if req.type.lower() != "kaysis":
            return ScrapeResponse(
                success=False,
                message=f"Desteklenmeyen scraper tipi: {req.type}. Şu an için sadece 'kaysis' desteklenmektedir.",
                data={"error": "UNSUPPORTED_TYPE", "type": req.type}
            )
        
        # ADIM 1,2,3: MongoDB'den kurum bilgisini çek ve mevcut belgeleri topla
        kurum_adi = None
        detsis = req.detsis  # Önce request'ten al
        
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                db = client[database_name]
                kurumlar_collection = db["kurumlar"]
                from bson import ObjectId
                kurum_doc = kurumlar_collection.find_one({"_id": ObjectId(kurum_id)})
                if kurum_doc:
                    kurum_adi = kurum_doc.get("kurum_adi", "Bilinmeyen Kurum")
                    # Eğer detsis request'te yoksa MongoDB'den al
                    if not detsis:
                        detsis = kurum_doc.get("detsis", "")
                client.close()
        except Exception as e:
            print(f"⚠️ MongoDB'den kurum bilgisi alınamadı: {str(e)}")
            kurum_adi = "Bilinmeyen Kurum"
        
        print(f"📋 Kurum: {kurum_adi}")
        print(f"🔢 DETSIS: {detsis or 'Belirtilmedi'}")
        
        # ADIM 2: API'den yüklü documents'ları çek (MevzuatGPT/Supabase)
        uploaded_docs = []
        cfg = _load_config()
        if cfg:
            token = _login_with_config(cfg)
            if token:
                api_base_url = cfg.get("api_base_url")
                print(f"📡 API'den yüklü documents çekiliyor (MevzuatGPT/Supabase)...")
                try:
                    uploaded_docs = get_uploaded_documents(api_base_url, token, use_streamlit=False)
                    print(f"✅ {len(uploaded_docs)} document bulundu (MevzuatGPT/Supabase)")
                except Exception as e:
                    print(f"⚠️ Documents çekme hatası: {str(e)}")
                    uploaded_docs = []  # Hata durumunda boş liste
            else:
                print("⚠️ API'ye giriş yapılamadı, belge kontrolü yapılamayacak")
                uploaded_docs = []
        else:
            print("⚠️ Config bulunamadı, API belge kontrolü yapılamayacak")
            uploaded_docs = []

        # ADIM 3: MongoDB metadata.pdf_adi -> portal_docs
        portal_docs = []
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
                db = client[database_name]
                metadata_collection = db[metadata_collection_name]
                # Sadece pdf_adi alanını al
                cursor = metadata_collection.find({}, {"pdf_adi": 1})
                count = 0
                for doc in cursor:
                    val = (doc.get("pdf_adi") or "").strip()
                    if val:
                        portal_docs.append({"pdf_adi": val})
                        count += 1
                client.close()
                print(f"✅ MongoDB'den {count} pdf_adi okundu (portal karşılaştırması için)")
        except Exception as e:
            print(f"⚠️ MongoDB portal listesi okunamadı: {str(e)}")
        
        # ADIM 4: Gönderilen JSON verisini kullan (scraper yok)
        print("📦 Gönderilen JSON verisi kullanılıyor (scraper çalıştırılmıyor)...")
        all_sections = req.sections
        
        # Stats'ı hesapla veya gönderilen stats'ı kullan
        if req.stats:
            stats = req.stats
        else:
            # Stats'ı hesapla
            total_items = sum(len(section.get('items', [])) for section in all_sections)
            stats = {
                'total_sections': len(all_sections),
                'total_items': total_items,
                'uploaded_documents_count': len(uploaded_docs)
            }
        print(f"✅ {len(all_sections)} bölüm, {stats.get('total_items', 0)} mevzuat JSON'dan alındı")
        
        # ADIM 5,6: Response hazırla (benzersiz item id'leri, uploaded durumu ve bölüm başlık temizleme)
        item_id_counter = 1
        response_sections = []
        # Önbelleği sıfırla
        global last_item_map
        last_item_map = {}
        for section in all_sections:
            raw_title = section.get('section_title', '')
            # Sonunda kalan sayıları temizle (örn: "Kanunlar4" -> "Kanunlar")
            clean_title = re.sub(r"\d+\s*$", "", raw_title).strip()
            items = section.get('items', [])
            items_with_ids = []
            for item in items:
                # Yükleme durumunu belirle - tam eşleşme (normalize edilmiş)
                item_baslik = item.get('baslik', '')
                if not item_baslik:
                    # Baslik yoksa atla
                    continue
                    
                item_normalized = normalize_for_exact_match(item_baslik)
                is_uploaded = False
                matched_doc_title = None
                matched_doc_field = None
                
                # MevzuatGPT/Supabase'den gelen belgelerle karşılaştır
                if uploaded_docs:
                    # Birden fazla alan kontrol et (API'den dönen belgelerde farklı alan isimleri olabilir)
                    # SADECE TAM EŞLEŞME kullan (is_title_similar çok gevşek, yanlış eşleşmelere neden oluyor)
                    for doc in uploaded_docs:
                        doc_titles = [
                            ("belge_adi", doc.get("belge_adi", "")),
                            ("title", doc.get("title", "")),
                            ("document_name", doc.get("document_name", "")),
                            ("filename", doc.get("filename", "")),
                            ("name", doc.get("name", ""))
                        ]
                        
                        for field_name, doc_title in doc_titles:
                            if doc_title:
                                # Sadece tam eşleşme kontrolü (normalize_for_exact_match ile)
                                doc_normalized = normalize_for_exact_match(doc_title)
                                if item_normalized == doc_normalized:
                                    is_uploaded = True
                                    matched_doc_title = doc_title
                                    matched_doc_field = field_name
                                    break
                        
                        if is_uploaded:
                            break
                
                # Portal (MongoDB metadata.pdf_adi karşılaştırması) - tam eşleşme
                is_in_portal = False
                for doc in portal_docs:
                    pdf_adi = doc.get("pdf_adi", "")
                    if pdf_adi:
                        pdf_normalized = normalize_for_exact_match(pdf_adi)
                        if item_normalized == pdf_normalized:
                            is_in_portal = True
                            break
                
                # Benzersiz id ver ve önbelleğe yaz
                item_payload = {
                    "id": item_id_counter,
                    "mevzuatgpt": is_uploaded,
                    "portal": is_in_portal,
                    "baslik": item.get('baslik', ''),
                    "link": item.get('link', '')
                }
                items_with_ids.append(item_payload)

                # Önbelleğe kategori bilgisini de ekleyerek koy
                last_item_map[item_id_counter] = {
                    "section_title": clean_title,
                    "baslik": item_payload["baslik"],
                    "link": item_payload["link"]
                }
                item_id_counter += 1
            response_sections.append({
                "section_title": clean_title,
                "items_count": len(items_with_ids),
                "items": items_with_ids
            })
        
        # sections_stats'ı is_title_similar ile yeniden hesapla
        sections_stats_clean = []
        for section in all_sections:
            raw_title = section.get('section_title', '')
            clean_title = re.sub(r"\d+\s*$", "", raw_title).strip()
            items = section.get('items', [])
            
            uploaded_count = 0
            not_uploaded_count = 0
            
            for item in items:
                item_baslik = item.get('baslik', '')
                item_normalized = normalize_for_exact_match(item_baslik)
                is_uploaded = False
                
                # MevzuatGPT/Supabase'den gelen belgelerle karşılaştır
                # SADECE TAM EŞLEŞME kullan (is_title_similar çok gevşek, yanlış eşleşmelere neden oluyor)
                if uploaded_docs:
                    for doc in uploaded_docs:
                        doc_titles = [
                            ("belge_adi", doc.get("belge_adi", "")),
                            ("title", doc.get("title", "")),
                            ("document_name", doc.get("document_name", "")),
                            ("filename", doc.get("filename", "")),
                            ("name", doc.get("name", ""))
                        ]
                        
                        for field_name, doc_title in doc_titles:
                            if doc_title:
                                # Sadece tam eşleşme kontrolü (normalize_for_exact_match ile)
                                doc_normalized = normalize_for_exact_match(doc_title)
                                if item_normalized == doc_normalized:
                                    is_uploaded = True
                                    break
                        
                        if is_uploaded:
                            break
                
                if is_uploaded:
                    uploaded_count += 1
                else:
                    not_uploaded_count += 1
            
            sections_stats_clean.append({
                "section_title": clean_title,
                "total": len(items),
                "uploaded": uploaded_count,
                "not_uploaded": not_uploaded_count
            })
        
        response_data = {
            "total_sections": stats.get('total_sections', 0),
            "total_items": stats.get('total_items', 0),
            "uploaded_documents_count": stats.get('uploaded_documents_count', len(uploaded_docs)),
            "sections": response_sections,
            "sections_stats": sections_stats_clean
        }
        
        # Nihai response'u JSON dosyasına kaydet
        try:
            import json
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"karşılaştırma_sonuçları_{kurum_id}_{timestamp}.json"
            filepath = os.path.join(os.getcwd(), filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(response_data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ Karşılaştırma sonuçları kaydedildi: {filename}")
        except Exception as e:
            print(f"⚠️ JSON dosyasına kaydetme hatası: {str(e)}")
        
        return ScrapeResponse(
            success=True,
            message=f"{kurum_adi} tarama işlemi başarıyla tamamlandı." + (" (JSON verisi kullanıldı)" if req.sections else " (Siteden tarama yapıldı)"),
            data=response_data
        )
        
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Scraping işlemi sırasında hata oluştu: {str(e)}"
        )


@app.post("/api/mevzuatgpt/generate-json", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="Sadece tarama yap ve JSON oluştur")
async def generate_scrape_json(req: GenerateJsonRequest):
    """
    Sadece scraper ile siteye bağlanır, tarama yapar ve toplanan verileri JSON formatında döndürür.
    API bağlantısı, Elasticsearch kontrolü, karşılaştırma gibi işlemler yapılmaz.
    Sadece saf tarama yapılır ve ham veriler döner.
    Kurum ID'si ile MongoDB'den detsis numarası bulunur ve kullanılır.
    """
    try:
        print("\n" + "="*80)
        
        # id veya kurum_id kontrolü
        kurum_id = req.id or getattr(req, 'kurum_id', None)
        if not kurum_id:
            return ScrapeResponse(
                success=False,
                message="Kurum ID (id veya kurum_id) gönderilmedi.",
                data={"error": "KURUM_ID_REQUIRED"}
            )
        
        print(f"🚀 JSON Oluşturma İsteği Alındı (Kurum ID: {kurum_id}, Type: {req.type})")
        print("="*80)
        
        # Type kontrolü
        if req.type.lower() != "kaysis":
            return ScrapeResponse(
                success=False,
                message=f"Desteklenmeyen scraper tipi: {req.type}. Şu an için sadece 'kaysis' desteklenmektedir.",
                data={"error": "UNSUPPORTED_TYPE", "type": req.type}
            )
        
        # MongoDB'den kurum bilgisini çek (sadece detsis için)
        detsis = None
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                db = client[database_name]
                kurumlar_collection = db["kurumlar"]
                from bson import ObjectId
                kurum_doc = kurumlar_collection.find_one({"_id": ObjectId(kurum_id)})
                if kurum_doc:
                    detsis = kurum_doc.get("detsis", "")
                client.close()
        except Exception as e:
            print(f"⚠️ MongoDB'den kurum bilgisi alınamadı: {str(e)}")
        
        if not detsis:
            return ScrapeResponse(
                success=False,
                message=f"Kurum bulunamadı veya DETSIS numarası bulunamadı. Kurum ID: {kurum_id}",
                data={"error": "KURUM_NOT_FOUND", "kurum_id": kurum_id}
            )
        
        print(f"📋 Kurum ID: {kurum_id}")
        print(f"🔢 DETSIS: {detsis}")
        
        # Sadece tarama yap (API bağlantısı yok, sadece siteye bağlan)
        print("🌐 KAYSİS sitesinden tarama başlatılıyor (sadece scraper, API/Elasticsearch yok)...")
        
        # KAYSİS URL'ini oluştur
        url = f"https://kms.kaysis.gov.tr/Home/Kurum/{detsis}"
        print(f"📡 Site: {url}")
        
        # MongoDB'den güncel proxy bilgilerini çek
        proxies = get_proxy_from_db()
        if proxies:
            print("🔐 Proxy kullanılıyor...")
        else:
            print("⚠️ Proxy bulunamadı, direkt bağlantı deneniyor...")
        
        # Siteye bağlan ve HTML'i parse et
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.google.com/',
                'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
                'Connection': 'keep-alive',
                'Cache-Control': 'max-age=0'
            }
            
            # curl_cffi ile Chrome taklidi yap (eğer mevcut ise)
            if CURL_CFFI_AVAILABLE:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=1200,  # 20 dakika timeout
                    proxies=proxies,
                    impersonate="chrome110"  # Chrome 110 TLS fingerprint
                )
            else:
                response = requests.get(url, headers=headers, timeout=1200, proxies=proxies)
            
            if response.status_code != 200:
                print(f"❌ Siteye erişilemedi: HTTP {response.status_code}")
                return ScrapeResponse(
                    success=False,
                    message=f"Siteye erişilemedi: HTTP {response.status_code}",
                    data={"error": "SITE_ACCESS_FAILED", "status_code": response.status_code}
                )
            
            # HTML'i parse et
            soup = BeautifulSoup(response.content, 'html.parser')
            print("✅ Site başarıyla yüklendi!")
            
            print("📋 Accordion yapısı aranıyor...")
            
            # accordion2 div'ini bul
            accordion_div = soup.find('div', {'id': 'accordion2', 'class': 'panel-group'})
            
            if not accordion_div:
                print("⚠️ accordion2 div'i bulunamadı!")
                return ScrapeResponse(
                    success=False,
                    message="Site yapısı bulunamadı (accordion2 div'i yok).",
                    data={"error": "STRUCTURE_NOT_FOUND"}
                )
            
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
            
            if not all_sections:
                return ScrapeResponse(
                    success=False,
                    message="Tarama başarısız veya sonuç bulunamadı.",
                    data={"error": "SCRAPE_FAILED", "message": "Hiç bölüm bulunamadı"}
                )
            
            # Stats oluştur (sadece temel bilgiler, API karşılaştırması yok)
            stats = {
                'total_sections': len(all_sections),
                'total_items': total_items
            }
            
            # JSON formatını hazırla (ham veriler, karşılaştırma yok)
            json_data = {
                "kurum_id": kurum_id,
                "detsis": detsis,
                "type": req.type,
                "sections": all_sections,
                "stats": stats
            }
            
            print(f"✅ JSON oluşturuldu: {len(all_sections)} bölüm, {total_items} mevzuat")
            
            return ScrapeResponse(
                success=True,
                message=f"Tarama tamamlandı ve JSON oluşturuldu.",
                data=json_data
            )
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Bağlantı hatası: {str(e)}")
            return ScrapeResponse(
                success=False,
                message=f"Bağlantı hatası: {str(e)}",
                data={"error": "CONNECTION_ERROR", "message": str(e)}
            )
        except Exception as e:
            print(f"❌ Tarama hatası: {str(e)}")
            import traceback
            traceback.print_exc()
            return ScrapeResponse(
                success=False,
                message=f"Tarama hatası: {str(e)}",
                data={"error": "SCRAPE_ERROR", "message": str(e)}
            )
        
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"JSON oluşturma işlemi sırasında hata oluştu: {str(e)}"
        )


@app.post("/api/kurum/portal-scan", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="Kurum portal tarama (MongoDB kontrolü)")
async def scrape_kurum_portal(req: PortalScanRequest):
    """
    Belirtilen kurumun mevzuatlarını tarar ve MongoDB metadata koleksiyonundaki kayıtlarla karşılaştırır.
    Portal durumunu (true/false) döner.
    type parametresi ile scraper tipi belirlenir (şu an için sadece 'kaysis' desteklenir).
    """
    try:
        print("\n" + "="*80)
        print(f"🚀 API Endpoint'ten Kurum Portal Tarama İsteği Alındı (Kurum ID: {req.id}, Type: {req.type})")
        print("="*80)
        
        # Type kontrolü
        if req.type.lower() != "kaysis":
            return ScrapeResponse(
                success=False,
                message=f"Desteklenmeyen scraper tipi: {req.type}. Şu an için sadece 'kaysis' desteklenmektedir.",
                data={"error": "UNSUPPORTED_TYPE", "type": req.type}
            )
        
        # MongoDB'den kurum bilgisini çek
        kurum_adi = None
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                db = client[database_name]
                kurumlar_collection = db["kurumlar"]
                from bson import ObjectId
                kurum_doc = kurumlar_collection.find_one({"_id": ObjectId(req.id)})
                if kurum_doc:
                    kurum_adi = kurum_doc.get("kurum_adi", "Bilinmeyen Kurum")
                client.close()
        except Exception as e:
            print(f"⚠️ MongoDB'den kurum bilgisi alınamadı: {str(e)}")
            kurum_adi = "Bilinmeyen Kurum"
        
        print(f"📋 Kurum: {kurum_adi}")
        print(f"🔢 DETSIS: {req.detsis}")
        
        # MongoDB'den portal'da bulunan pdf_adi'ları çek
        portal_title_set = set()
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
                db = client[database_name]
                metadata_collection = db[metadata_collection_name]
                # Sadece pdf_adi alanını al
                cursor = metadata_collection.find({}, {"pdf_adi": 1})
                count = 0
                for doc in cursor:
                    val = (doc.get("pdf_adi") or "").strip()
                    if val:
                        portal_title_set.add(to_title(val))
                        count += 1
                client.close()
                print(f"✅ MongoDB'den {count} pdf_adi okundu (portal karşılaştırması için)")
        except Exception as e:
            print(f"⚠️ MongoDB portal listesi okunamadı: {str(e)}")
        
        # KAYSİS scraper'ı kullan
        if req.type.lower() == "kaysis":
            all_sections, stats = scrape_kaysis_mevzuat(detsis=req.detsis)
            print_results_to_console(all_sections, stats)
        
        # Response hazırla (benzersiz item id'leri, portal durumu ve bölüm başlık temizleme)
        item_id_counter = 1
        response_sections = []
        # Önbelleği sıfırla
        global last_item_map
        last_item_map = {}
        for section in all_sections:
            raw_title = section['section_title']
            # Sonunda kalan sayıları temizle (örn: "Kanunlar4" -> "Kanunlar")
            clean_title = re.sub(r"\d+\s*$", "", raw_title).strip()
            items = section.get('items', [])
            items_with_ids = []
            for item in items:
                # Portal (MongoDB metadata.pdf_adi karşılaştırması) - %100 eşitlik
                item_title_tc = to_title(item.get('baslik', ''))
                is_in_portal = (item_title_tc in portal_title_set)
                
                # Benzersiz id ver ve önbelleğe yaz
                item_payload = {
                    "id": item_id_counter,
                    "portal": is_in_portal,
                    "baslik": item.get('baslik', ''),
                    "link": item.get('link', '')
                }
                items_with_ids.append(item_payload)

                # Önbelleğe kategori bilgisini de ekleyerek koy
                last_item_map[item_id_counter] = {
                    "section_title": clean_title,
                    "baslik": item_payload["baslik"],
                    "link": item_payload["link"]
                }
                item_id_counter += 1
            response_sections.append({
                "section_title": clean_title,
                "items_count": len(items_with_ids),
                "items": items_with_ids
            })
        
        # sections_stats'ı portal_title_set ile hesapla
        sections_stats_clean = []
        for section in all_sections:
            raw_title = section['section_title']
            clean_title = re.sub(r"\d+\s*$", "", raw_title).strip()
            items = section.get('items', [])
            
            portal_count = 0
            not_portal_count = 0
            
            for item in items:
                item_title_tc = to_title(item.get('baslik', ''))
                is_in_portal = (item_title_tc in portal_title_set)
                if is_in_portal:
                    portal_count += 1
                else:
                    not_portal_count += 1
            
            sections_stats_clean.append({
                "section_title": clean_title,
                "total": len(items),
                "portal": portal_count,
                "not_portal": not_portal_count
            })
        
        response_data = {
            "total_sections": stats.get('total_sections', 0),
            "total_items": stats.get('total_items', 0),
            "portal_documents_count": len(portal_title_set),
            "sections": response_sections,
            "sections_stats": sections_stats_clean
        }
        
        return ScrapeResponse(
            success=True,
            message=f"{kurum_adi} portal tarama işlemi başarıyla tamamlandı. Sonuçlar konsola yazdırıldı.",
            data=response_data
        )
        
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Portal tarama işlemi sırasında hata oluştu: {str(e)}"
        )


@app.get("/health", tags=["Health"], summary="Sağlık kontrolü")
async def health_check():
    """
    Detaylı sağlık kontrolü endpoint'i.
    Servis durumu, MongoDB bağlantısı ve sistem bilgilerini kontrol eder.
    """
    health_status = {
        "status": "healthy",
        "service": "SGK Scraper API",
        "timestamp": datetime.now().isoformat(),
        "checks": {}
    }
    
    # 1. MongoDB bağlantı kontrolü
    try:
        client = _get_mongodb_client()
        if client:
            client.admin.command('ping')
            client.close()
            health_status["checks"]["mongodb"] = {
                "status": "healthy",
                "message": "MongoDB bağlantısı başarılı"
            }
        else:
            health_status["checks"]["mongodb"] = {
                "status": "unhealthy",
                "message": "MongoDB bağlantısı kurulamadı"
            }
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["checks"]["mongodb"] = {
            "status": "unhealthy",
            "message": f"MongoDB bağlantı hatası: {str(e)}"
        }
        health_status["status"] = "degraded"
    
    # 2. Systemd servis durumu kontrolü
    try:
        service_name = "pdfanalyzerrag"
        
        # systemctl komutunu farklı path'lerde ara (önce en yaygın path'ler)
        systemctl_paths = ["/usr/bin/systemctl", "/bin/systemctl", "systemctl"]
        systemctl_cmd = None
        
        for path in systemctl_paths:
            try:
                if path == "systemctl":
                    # PATH'te ara
                    result = subprocess.run(
                        ["which", "systemctl"],
                        capture_output=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        systemctl_cmd = result.stdout.strip().decode('utf-8') if result.stdout else "systemctl"
                        break
                else:
                    # Direkt path'i kontrol et
                    result = subprocess.run(
                        ["test", "-f", path],
                        capture_output=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        systemctl_cmd = path
                        break
            except Exception:
                continue
        
        if systemctl_cmd:
            result = subprocess.run(
                [systemctl_cmd, "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                service_status = result.stdout.strip()
                health_status["checks"]["systemd_service"] = {
                    "status": "healthy" if service_status == "active" else "unhealthy",
                    "message": f"Servis durumu: {service_status}",
                    "service_name": service_name
                }
                if service_status != "active":
                    health_status["status"] = "unhealthy"
            else:
                health_status["checks"]["systemd_service"] = {
                    "status": "unknown",
                    "message": f"Servis durumu kontrol edilemedi: {result.stderr.strip() if result.stderr else 'Servis bulunamadı veya erişilemedi'}"
                }
        else:
            health_status["checks"]["systemd_service"] = {
                "status": "not_available",
                "message": "systemctl komutu bulunamadı (systemd mevcut değil veya PATH'te yok)",
                "note": "Bu sistemde systemd servis yönetimi kullanılamıyor olabilir"
            }
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        health_status["checks"]["systemd_service"] = {
            "status": "not_available",
            "message": f"Systemd servis kontrolü yapılamadı: {str(e)}",
            "note": "Sistem systemd kullanmıyor olabilir veya yetki sorunu olabilir"
        }
    
    # 3. curl_cffi kontrolü
    health_status["checks"]["curl_cffi"] = {
        "status": "available" if CURL_CFFI_AVAILABLE else "unavailable",
        "message": "curl_cffi mevcut" if CURL_CFFI_AVAILABLE else "curl_cffi kurulu değil (standart requests kullanılıyor)"
    }
    
    # 4. Sistem bilgileri
    health_status["system"] = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version()
    }
    
    return health_status


@app.get("/api/health/logs", tags=["Health"], summary="Servis loglarını getir")
async def get_service_logs(lines: int = 100):
    """
    Systemd servis loglarını getirir.
    
    Args:
        lines: Gösterilecek log satırı sayısı (varsayılan: 100, maksimum: 1000)
    
    Returns:
        Servis logları ve metadata
    """
    try:
        # Satır sayısını sınırla
        lines = max(1, min(lines, 1000))
        
        service_name = "pdfanalyzerrag"
        
        # journalctl komutunu farklı path'lerde ara (önce en yaygın path'ler)
        journalctl_paths = ["/usr/bin/journalctl", "/bin/journalctl", "journalctl"]
        journalctl_cmd = None
        
        for path in journalctl_paths:
            try:
                if path == "journalctl":
                    # PATH'te ara
                    result = subprocess.run(
                        ["which", "journalctl"],
                        capture_output=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        journalctl_cmd = result.stdout.strip().decode('utf-8') if result.stdout else "journalctl"
                        break
                else:
                    # Direkt path'i kontrol et
                    result = subprocess.run(
                        ["test", "-f", path],
                        capture_output=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        journalctl_cmd = path
                        break
            except Exception:
                continue
        
        if not journalctl_cmd:
            return {
                "success": False,
                "service_name": service_name,
                "error": "journalctl komutu bulunamadı (systemd mevcut değil)",
                "timestamp": datetime.now().isoformat(),
                "logs": [],
                "raw_logs": "",
                "note": "Bu sistemde systemd log yönetimi kullanılamıyor"
            }
        
        # journalctl komutunu çalıştır
        result = subprocess.run(
            [journalctl_cmd, "-u", service_name, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            logs = result.stdout.strip()
            log_lines = logs.split('\n') if logs else []
            
            return {
                "success": True,
                "service_name": service_name,
                "lines_requested": lines,
                "lines_returned": len(log_lines),
                "timestamp": datetime.now().isoformat(),
                "logs": log_lines,
                "raw_logs": logs
            }
        else:
            # journalctl komutu başarısız oldu, alternatif yöntem dene
            error_msg = result.stderr.strip() if result.stderr else "Bilinmeyen hata"
            
            # systemctl status komutunu dene
            systemctl_paths = ["/usr/bin/systemctl", "/bin/systemctl", "systemctl"]
            systemctl_cmd = None
            
            for path in systemctl_paths:
                try:
                    if path == "systemctl":
                        # PATH'te ara
                        test_result = subprocess.run(
                            ["which", "systemctl"],
                            capture_output=True,
                            timeout=2
                        )
                        if test_result.returncode == 0:
                            systemctl_cmd = test_result.stdout.strip().decode('utf-8') if test_result.stdout else "systemctl"
                            break
                    else:
                        # Direkt path'i kontrol et
                        test_result = subprocess.run(
                            ["test", "-f", path],
                            capture_output=True,
                            timeout=2
                        )
                        if test_result.returncode == 0:
                            systemctl_cmd = path
                            break
                except Exception:
                    continue
            
            if systemctl_cmd:
                try:
                    status_result = subprocess.run(
                        [systemctl_cmd, "status", service_name, "--no-pager", "-n", str(lines)],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if status_result.returncode == 0:
                        logs = status_result.stdout.strip()
                        log_lines = logs.split('\n') if logs else []
                        return {
                            "success": True,
                            "service_name": service_name,
                            "lines_requested": lines,
                            "lines_returned": len(log_lines),
                            "timestamp": datetime.now().isoformat(),
                            "logs": log_lines,
                            "raw_logs": logs,
                            "note": "journalctl kullanılamadı, systemctl status kullanıldı"
                        }
                except Exception:
                    pass
            
            return {
                "success": False,
                "service_name": service_name,
                "error": f"Loglar alınamadı: {error_msg}",
                "timestamp": datetime.now().isoformat(),
                "logs": [],
                "raw_logs": ""
            }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Log alma işlemi zaman aşımına uğradı",
            "timestamp": datetime.now().isoformat(),
            "logs": [],
            "raw_logs": ""
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "journalctl komutu bulunamadı (systemd mevcut değil)",
            "timestamp": datetime.now().isoformat(),
            "logs": [],
            "raw_logs": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Beklenmeyen hata: {str(e)}",
            "timestamp": datetime.now().isoformat(),
            "logs": [],
            "raw_logs": ""
        }


@app.get("/api/health/status", tags=["Health"], summary="Servis durumu detaylı bilgi")
async def get_service_status():
    """
    Systemd servis durumunu detaylı olarak getirir.
    
    Returns:
        Servis durumu, aktif süre, son restart zamanı vb.
    """
    try:
        service_name = "pdfanalyzerrag"
        
        # systemctl komutunu farklı path'lerde ara (önce en yaygın path'ler)
        systemctl_paths = ["/usr/bin/systemctl", "/bin/systemctl", "systemctl"]
        systemctl_cmd = None
        
        for path in systemctl_paths:
            try:
                if path == "systemctl":
                    # PATH'te ara
                    test_result = subprocess.run(
                        ["which", "systemctl"],
                        capture_output=True,
                        timeout=2
                    )
                    if test_result.returncode == 0:
                        systemctl_cmd = test_result.stdout.strip().decode('utf-8') if test_result.stdout else "systemctl"
                        break
                else:
                    # Direkt path'i kontrol et
                    test_result = subprocess.run(
                        ["test", "-f", path],
                        capture_output=True,
                        timeout=2
                    )
                    if test_result.returncode == 0:
                        systemctl_cmd = path
                        break
            except Exception:
                continue
        
        if not systemctl_cmd:
            return {
                "success": False,
                "error": "systemctl komutu bulunamadı (systemd mevcut değil)",
                "timestamp": datetime.now().isoformat(),
                "note": "Bu sistemde systemd servis yönetimi kullanılamıyor"
            }
        
        # systemctl status komutunu çalıştır
        result = subprocess.run(
            [systemctl_cmd, "status", service_name, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        status_info = {
            "success": True,
            "service_name": service_name,
            "timestamp": datetime.now().isoformat(),
            "status_output": result.stdout.strip() if result.returncode == 0 else None,
            "error": result.stderr.strip() if result.stderr and result.returncode != 0 else None
        }
        
        # systemctl show komutu ile daha detaylı bilgi al
        try:
            show_result = subprocess.run(
                [systemctl_cmd, "show", service_name, "--no-pager"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if show_result.returncode == 0:
                # Key-value çiftlerini parse et
                details = {}
                for line in show_result.stdout.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        details[key] = value
                status_info["details"] = details
        except Exception:
            pass
        
        return status_info
        
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Servis durumu kontrolü zaman aşımına uğradı",
            "timestamp": datetime.now().isoformat()
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "systemctl komutu bulunamadı (systemd mevcut değil)",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Beklenmeyen hata: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }


# ========================
# MongoDB Admin Endpoints
# ========================

@app.get("/api/mongo/metadata/{id}", tags=["MongoDB"], summary="Metadata getir")
async def get_metadata(id: str):
    try:
        client, metadata_col, content_col = _get_mongo_collections()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            doc = metadata_col.find_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz metadata _id")
        if not doc:
            client.close()
            raise HTTPException(status_code=404, detail="Metadata bulunamadı")
        doc["_id"] = str(doc["_id"])
        client.close()
        return {"success": True, "data": doc}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.put("/api/mongo/metadata/{id}", tags=["MongoDB"], summary="Metadata güncelle")
async def update_metadata(id: str, body: Dict[str, Any]):
    try:
        client, metadata_col, content_col = _get_mongo_collections()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        # Güvenli güncelleme (boş/null değerleri set etmeyelim)
        update_data: Dict[str, Any] = {}
        for k, v in (body or {}).items():
            if v is not None:
                update_data[k] = v
        if not update_data:
            client.close()
            return {"success": True, "message": "Güncellenecek alan yok"}
        try:
            res = metadata_col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz metadata _id")
        client.close()
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Metadata bulunamadı")
        return {"success": True, "modified": res.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.get("/api/mongo/content/by-metadata/{metadata_id}", tags=["MongoDB"], summary="Content getir (metadata)")
async def get_content_by_metadata(metadata_id: str):
    try:
        client, metadata_col, content_col = _get_mongo_collections()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            doc = content_col.find_one({"metadata_id": ObjectId(metadata_id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz metadata_id")
        if not doc:
            client.close()
            raise HTTPException(status_code=404, detail="Content bulunamadı")
        doc["_id"] = str(doc["_id"])
        doc["metadata_id"] = str(doc["metadata_id"])
        client.close()
        return {"success": True, "data": doc}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.put("/api/mongo/content/by-metadata/{metadata_id}", tags=["MongoDB"], summary="Content güncelle (metadata)")
async def update_content_by_metadata(metadata_id: str, body: Dict[str, Any]):
    try:
        client, metadata_col, content_col = _get_mongo_collections()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        new_content = (body or {}).get("icerik")
        if new_content is None:
            client.close()
            raise HTTPException(status_code=400, detail="Body içinde 'icerik' alanı gerekli")
        try:
            res = content_col.update_one(
                {"metadata_id": ObjectId(metadata_id)},
                {"$set": {"icerik": new_content}}
            )
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz metadata_id")
        client.close()
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Content bulunamadı")
        return {"success": True, "modified": res.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.delete("/api/mongo/metadata/{id}", tags=["MongoDB"], summary="Portal içeriğini sil (Metadata, Content ve Bunny.net PDF)")
async def delete_portal_content(id: str):
    """
    Portal içeriğini tamamen siler:
    1. MongoDB metadata kaydını siler
    2. MongoDB content kaydını siler (metadata_id ile ilişkili)
    3. Bunny.net'teki PDF dosyasını siler (pdf_url'den)
    
    NOT: Bu işlem sadece portal için geçerlidir, MevzuatGPT'yi etkilemez.
    """
    try:
        client, metadata_col, content_col = _get_mongo_collections()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        try:
            # Önce metadata kaydını bul
            metadata_doc = metadata_col.find_one({"_id": ObjectId(id)})
            if not metadata_doc:
                client.close()
                raise HTTPException(status_code=404, detail="Metadata bulunamadı")
            
            # pdf_url'i al (Bunny.net'ten silmek için)
            pdf_url = metadata_doc.get("pdf_url", "")
            
            print(f"🗑️ Portal içeriği siliniyor: metadata_id={id}")
            print(f"📄 PDF URL: {pdf_url}")
            
            # 1. Content kaydını sil (metadata_id ile ilişkili)
            content_result = content_col.delete_one({"metadata_id": ObjectId(id)})
            if content_result.deleted_count > 0:
                print(f"✅ Content kaydı silindi: {content_result.deleted_count} kayıt")
            else:
                print("⚠️ Content kaydı bulunamadı (zaten silinmiş olabilir)")
            
            # 2. Metadata kaydını sil
            metadata_result = metadata_col.delete_one({"_id": ObjectId(id)})
            if metadata_result.deleted_count == 0:
                client.close()
                raise HTTPException(status_code=404, detail="Metadata silinemedi (kayıt bulunamadı)")
            
            print(f"✅ Metadata kaydı silindi: {metadata_result.deleted_count} kayıt")
            
            # 3. Bunny.net'ten PDF'i sil
            bunny_deleted = False
            if pdf_url:
                bunny_deleted = _delete_from_bunny(pdf_url)
            else:
                print("⚠️ PDF URL bulunamadı, Bunny.net silme işlemi atlandı")
            
            client.close()
            
            # Sonuç mesajı
            result_message = f"Portal içeriği başarıyla silindi. Metadata: ✅, Content: ✅"
            if pdf_url:
                if bunny_deleted:
                    result_message += ", Bunny.net PDF: ✅"
                else:
                    result_message += ", Bunny.net PDF: ⚠️ (silme başarısız veya dosya bulunamadı)"
            
            return {
                "success": True,
                "message": result_message,
                "deleted": {
                    "metadata": metadata_result.deleted_count,
                    "content": content_result.deleted_count,
                    "bunny_pdf": bunny_deleted
                }
            }
            
        except HTTPException:
            client.close()
            raise
        except Exception as e:
            client.close()
            if "invalid" in str(e).lower() or "objectid" in str(e).lower():
                raise HTTPException(status_code=400, detail="Geçersiz metadata _id")
            raise HTTPException(status_code=500, detail=f"Silme işlemi sırasında hata: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.get("/api/mongo/metadata", tags=["MongoDB"], summary="Metadata listele")
async def list_metadata(limit: int = 100, offset: int = 0):
    """Tüm metadata kayıtlarını listeler (varsayılan limit 100)."""
    try:
        client, metadata_col, content_col = _get_mongo_collections()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        # Güvenli limit aralığı
        if limit <= 0:
            limit = 100
        if limit > 1000:
            limit = 1000
        if offset < 0:
            offset = 0
        total = metadata_col.count_documents({})
        cursor = metadata_col.find({}).skip(offset).limit(limit).sort("olusturulma_tarihi", -1)
        items = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            items.append(doc)
        client.close()
        return {"success": True, "total": total, "limit": limit, "offset": offset, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


# ========================
# Kurumlar CRUD Endpoints
# ========================

def _get_kurumlar_collection():
    client = _get_mongodb_client()
    if not client:
        return None, None
    database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
    db = client[database_name]
    return client, db["kurumlar"]


def _get_kurum_duyuru_collection():
    client = _get_mongodb_client()
    if not client:
        return None, None
    database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
    db = client[database_name]
    return client, db["kurum_duyuru"]

def _get_links_collection():
    client = _get_mongodb_client()
    if not client:
        return None, None
    database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
    db = client[database_name]
    return client, db["links"]


@app.get("/api/mongo/kurumlar", tags=["Kurumlar"], summary="Kurumları listele")
async def list_kurumlar(limit: int = 100, offset: int = 0):
    try:
        client, col = _get_kurumlar_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        if limit <= 0:
            limit = 100
        if limit > 1000:
            limit = 1000
        if offset < 0:
            offset = 0
        total = col.count_documents({})
        cursor = col.find({}).skip(offset).limit(limit).sort("olusturulma_tarihi", -1)
        items = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            items.append(d)
        client.close()
        return {"success": True, "total": total, "limit": limit, "offset": offset, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


# ==============================
# e-Devlet Link Scraper Endpoint
# ==============================

def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def _is_safe_edevlet_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ["http", "https"]:
            return False
        hostname = parsed.hostname or ""
        allowed_domains = [
            'turkiye.gov.tr',
            'www.turkiye.gov.tr',
            'gov.tr',
            'e-devlet.gov.tr'
        ]
        for d in allowed_domains:
            if hostname == d or hostname.endswith('.' + d):
                return True
        return False
    except Exception:
        return False


def _extract_links_from_page(base_url: str, html: bytes) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, 'html.parser')

    # Öncelik verilen selektörler
    priority_selectors = [
        'a.integratedService[href]:not([href=""])',
        'a[data-description][href]:not([href=""])'
    ]
    general_selectors = [
        '.service-item a',
        '.link-item a',
        '.menu-item a',
        'li a[href]:not([href="#"]):not([href=""])',
        '.card a',
        '.services-list a',
        '.category-list a',
        '.service-card a',
        'a[href*="/hizmet"]'
    ]

    containers = []
    for selector in priority_selectors:
        containers.extend(soup.select(selector))
    if len(containers) < 5:
        for selector in general_selectors:
            containers.extend(soup.select(selector))

    # Tekilleştir
    seen = set()
    unique = []
    for el in containers:
        href = el.get('href', '')
        if href and href not in seen:
            seen.add(href)
            unique.append(el)

    results: List[Dict[str, str]] = []
    seen_urls_result = set()
    for el in unique:
        href = el.get('href', '').strip()
        if not href:
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen_urls_result:
            continue

        # Başlık
        title = el.get_text(strip=True) or el.get('title', '').strip() or el.get('alt', '') or el.get('aria-label', '')
        if not title:
            # Üst başlıkları dene
            parent = el.parent
            while parent and not title:
                if parent.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                    title = parent.get_text(strip=True)
                    break
                parent = parent.parent
        title = (title or "Başlık bulunamadı")[:200]

        # Açıklama
        description = el.get('data-description', '').strip()
        if not description:
            parent = el.parent
            if parent:
                siblings = parent.find_all(['p', 'span', 'div'], class_=lambda x: x and ('desc' in x.lower() or 'summary' in x.lower()))
                for s in siblings:
                    txt = s.get_text(strip=True)
                    if txt and len(txt) > 10:
                        description = txt
                        break
        if not description:
            next_elements = el.find_next_siblings(['p', 'div', 'span'])
            for ne in next_elements[:3]:
                txt = ne.get_text(strip=True)
                if txt and 20 <= len(txt) < 500:
                    description = txt
                    break
        description = (description or "Açıklama bulunamadı")[:500]

        # Filtreler
        if not _is_valid_url(full_url):
            continue
        lower_url = full_url.lower()
        skip_patterns = ['javascript:', 'mailto:', 'tel:', '#', '.pdf', '.doc', '.docx', '.xls', '.xlsx', 'facebook.com', 'twitter.com', 'instagram.com', 'youtube.com']
        if any(p in lower_url for p in skip_patterns):
            continue
        if not title or len(title.strip()) < 3:
            continue
        if 'turkiye.gov.tr' in lower_url:
            # Daha kısa başlıklara izin ver
            if len(title.strip()) < 8:
                continue

        results.append({
            "baslik": title,
            "aciklama": description,
            "url": full_url
        })
        seen_urls_result.add(full_url)

    return results


@app.post("/api/mongo/edevlet/scrape", tags=["e-Devlet Scraper"], summary="e-Devlet linkleri topla ve kaydet")
async def scrape_edevlet_links(body: Dict[str, Any]):
    """
    Verilen e-Devlet/Türkiye.gov.tr sayfasından hizmet linklerini toplayıp `links` koleksiyonuna kaydeder.
    Beklenen body: {"kurum_id": "ObjectId string", "url": "https://www.turkiye.gov.tr/..."}
    """
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")

        kurum_id = (body or {}).get("kurum_id")
        url = (body or {}).get("url")
        if not kurum_id:
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_id' zorunlu")
        if not url:
            client.close()
            raise HTTPException(status_code=400, detail="'url' zorunlu")

        # kurum_id doğrula
        try:
            kurum_oid = ObjectId(str(kurum_id).strip())
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_id' geçersiz ObjectId")

        # URL güvenlik ve format kontrolleri
        if not _is_valid_url(url):
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz URL formatı")
        if not _is_safe_edevlet_url(url):
            client.close()
            raise HTTPException(status_code=400, detail="Bu URL izin verilen domainlerde değil")

        # E-devlet scraper'ında proxy kullanılıyor
        proxies = get_proxy_from_db()
        if proxies:
            print("🔐 E-devlet scraper'ında proxy kullanılıyor...")
        else:
            print("⚠️ Proxy bulunamadı, direkt bağlantı deneniyor...")
        
        # Sayfayı çek - Gerçek bir Chrome tarayıcısının gönderdiği tüm header'lar
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.google.com/',
                'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
                'Connection': 'keep-alive',
                'Cache-Control': 'max-age=0'
            }
            
            # curl_cffi ile Chrome taklidi yap (eğer mevcut ise)
            if CURL_CFFI_AVAILABLE:
                resp = requests.get(
                    url,
                    headers=headers,
                    timeout=15,
                    proxies=proxies,
                    impersonate="chrome110"  # Chrome 110 TLS fingerprint
                )
            else:
                resp = requests.get(url, headers=headers, timeout=15, proxies=proxies)
            resp.raise_for_status()
        except Exception as e:
            client.close()
            raise HTTPException(status_code=502, detail=f"HTTP hatası: {str(e)}")

        links = _extract_links_from_page(url, resp.content)

        if not links:
            client.close()
            return {"success": True, "inserted_count": 0, "data": []}

        # Dokümanları hazırla
        now_iso = datetime.now().isoformat()
        docs = []
        for item in links:
            docs.append({
                "baslik": item.get("baslik", ""),
                "aciklama": item.get("aciklama", ""),
                "url": item.get("url", ""),
                "kurum_id": kurum_oid,
                "created_at": now_iso
            })

        # Ekle (toplu)
        try:
            res = col.insert_many(docs, ordered=False)
            inserted_count = len(res.inserted_ids)
        except Exception as e:
            client.close()
            raise HTTPException(status_code=500, detail=f"MongoDB ekleme hatası: {str(e)}")

        # JSON uyumlu dönüş (ObjectId dönüştür)
        def _to_jsonable(o):
            if isinstance(o, ObjectId):
                return str(o)
            if isinstance(o, dict):
                return {k: _to_jsonable(v) for k, v in o.items()}
            if isinstance(o, list):
                return [_to_jsonable(x) for x in o]
            return o

        all_data = _to_jsonable(docs)
        
        client.close()
        return {"success": True, "inserted_count": inserted_count, "data": all_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


# ==============================
# Links Koleksiyonu CRUD Endpoints
# ==============================

@app.get("/api/mongo/links", tags=["Links"], summary="Linkleri listele")
async def list_links(limit: int = 100, offset: int = 0):
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        if limit <= 0:
            limit = 100
        if limit > 1000:
            limit = 1000
        if offset < 0:
            offset = 0
        total = col.count_documents({})
        cursor = col.find({}).skip(offset).limit(limit).sort("_id", -1)
        items = []
        for d in cursor:
            d["_id"] = str(d["_id"]) 
            if "kurum_id" in d and isinstance(d["kurum_id"], ObjectId):
                d["kurum_id"] = str(d["kurum_id"]) 
            items.append(d)
        client.close()
        return {"success": True, "total": total, "limit": limit, "offset": offset, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.post("/api/mongo/links", tags=["Links"], summary="Link oluştur")
async def create_link(body: Dict[str, Any]):
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        data = body or {}
        baslik = (data.get("baslik") or "").strip()
        aciklama = (data.get("aciklama") or "").strip()
        url = (data.get("url") or "").strip()
        kurum_id = (data.get("kurum_id") or "").strip()

        if not baslik or not url or not kurum_id:
            client.close()
            raise HTTPException(status_code=400, detail="'baslik', 'url' ve 'kurum_id' zorunludur")
        if not _is_valid_url(url):
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz URL formatı")
        try:
            kurum_oid = ObjectId(kurum_id)
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_id' geçersiz ObjectId")

        doc = {
            "baslik": baslik,
            "aciklama": aciklama,
            "url": url,
            "kurum_id": kurum_oid,
            "created_at": datetime.now().isoformat()
        }
        res = col.insert_one(doc)
        new_id = str(res.inserted_id)
        client.close()
        return {"success": True, "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.get("/api/mongo/links/{id}", tags=["Links"], summary="Link getir")
async def get_link(id: str):
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            d = col.find_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz link id")
        if not d:
            client.close()
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        d["_id"] = str(d["_id"]) 
        if "kurum_id" in d and isinstance(d["kurum_id"], ObjectId):
            d["kurum_id"] = str(d["kurum_id"]) 
        client.close()
        return {"success": True, "data": d}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.put("/api/mongo/links/{id}", tags=["Links"], summary="Link güncelle")
async def update_link(id: str, body: Dict[str, Any]):
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        update_data: Dict[str, Any] = {}
        data = body or {}

        if "baslik" in data and data["baslik"] is not None:
            update_data["baslik"] = str(data["baslik"]).strip()
        if "aciklama" in data and data["aciklama"] is not None:
            update_data["aciklama"] = str(data["aciklama"]).strip()
        if "url" in data and data["url"] is not None:
            link_url = str(data["url"]).strip()
            if not _is_valid_url(link_url):
                client.close()
                raise HTTPException(status_code=400, detail="Geçersiz URL formatı")
            update_data["url"] = link_url
        if "kurum_id" in data and data["kurum_id"] is not None:
            try:
                update_data["kurum_id"] = ObjectId(str(data["kurum_id"]).strip())
            except Exception:
                client.close()
                raise HTTPException(status_code=400, detail="'kurum_id' geçersiz ObjectId")

        if not update_data:
            client.close()
            return {"success": True, "modified": 0, "message": "Güncellenecek alan yok"}

        try:
            res = col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz link id")
        client.close()
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        return {"success": True, "modified": res.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.delete("/api/mongo/links/{id}", tags=["Links"], summary="Link sil")
async def delete_link(id: str):
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            res = col.delete_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz link id")
        client.close()
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        return {"success": True, "deleted": res.deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.delete("/api/mongo/links/by-kurum/{kurum_id}", tags=["Links"], summary="Kurumdaki tüm linkleri sil")
async def delete_links_by_kurum(kurum_id: str):
    """
    Verilen kurum_id için links koleksiyonundaki TÜM kayıtları siler.
    """
    try:
        client, col = _get_links_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            kurum_oid = ObjectId(kurum_id)
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_id' geçersiz ObjectId")
        res = col.delete_many({"kurum_id": kurum_oid})
        deleted = res.deleted_count if res else 0
        client.close()
        return {"success": True, "deleted_count": deleted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")

# Kurum Duyuru CRUD Endpoints
# ==============================

@app.get("/api/mongo/kurum-duyuru", tags=["Kurum Duyuru"], summary="Kurum duyuruları listele")
async def list_kurum_duyuru(limit: int = 100, offset: int = 0):
    try:
        client, col = _get_kurum_duyuru_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        if limit <= 0:
            limit = 100
        if limit > 1000:
            limit = 1000
        if offset < 0:
            offset = 0
        total = col.count_documents({})
        cursor = col.find({}).skip(offset).limit(limit).sort("_id", -1)
        items = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            if "kurum_id" in d and isinstance(d["kurum_id"], ObjectId):
                d["kurum_id"] = str(d["kurum_id"])
            items.append(d)
        client.close()
        return {"success": True, "total": total, "limit": limit, "offset": offset, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.post("/api/mongo/kurum-duyuru", tags=["Kurum Duyuru"], summary="Kurum duyurusu oluştur")
async def create_kurum_duyuru(body: Dict[str, Any]):
    try:
        client, col = _get_kurum_duyuru_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        data = body or {}
        kurum_id = (data.get("kurum_id") or "").strip()
        duyuru_linki = (data.get("duyuru_linki") or "").strip()
        if not kurum_id:
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_id' zorunlu")
        if not duyuru_linki:
            client.close()
            raise HTTPException(status_code=400, detail="'duyuru_linki' zorunlu")
        # Basit URL kontrolü
        if not re.match(r"^https?://", duyuru_linki):
            client.close()
            raise HTTPException(status_code=400, detail="'duyuru_linki' geçerli bir URL olmalı")
        # kurum_id ObjectId'e dönüştür
        try:
            kurum_oid = ObjectId(kurum_id)
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_id' geçersiz ObjectId")
        doc = {
            "kurum_id": kurum_oid,
            "duyuru_linki": duyuru_linki,
            "olusturulma_tarihi": datetime.now().isoformat()
        }
        res = col.insert_one(doc)
        new_id = str(res.inserted_id)
        client.close()
        return {"success": True, "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.get("/api/mongo/kurum-duyuru/{id}", tags=["Kurum Duyuru"], summary="Kurum duyurusu getir")
async def get_kurum_duyuru(id: str):
    try:
        client, col = _get_kurum_duyuru_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            d = col.find_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz duyuru id")
        if not d:
            client.close()
            raise HTTPException(status_code=404, detail="Duyuru bulunamadı")
        d["_id"] = str(d["_id"])
        if "kurum_id" in d and isinstance(d["kurum_id"], ObjectId):
            d["kurum_id"] = str(d["kurum_id"])
        client.close()
        return {"success": True, "data": d}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.put("/api/mongo/kurum-duyuru/{id}", tags=["Kurum Duyuru"], summary="Kurum duyurusu güncelle")
async def update_kurum_duyuru(id: str, body: Dict[str, Any]):
    try:
        client, col = _get_kurum_duyuru_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        update_data: Dict[str, Any] = {}
        data = body or {}
        if "kurum_id" in data and data["kurum_id"] is not None:
            try:
                update_data["kurum_id"] = ObjectId(str(data["kurum_id"]).strip())
            except Exception:
                client.close()
                raise HTTPException(status_code=400, detail="'kurum_id' geçersiz ObjectId")
        if "duyuru_linki" in data and data["duyuru_linki"] is not None:
            link = str(data["duyuru_linki"]).strip()
            if not link:
                client.close()
                raise HTTPException(status_code=400, detail="'duyuru_linki' boş olamaz")
            if not re.match(r"^https?://", link):
                client.close()
                raise HTTPException(status_code=400, detail="'duyuru_linki' geçerli bir URL olmalı")
            update_data["duyuru_linki"] = link
        if not update_data:
            client.close()
            return {"success": True, "modified": 0, "message": "Güncellenecek alan yok"}
        try:
            res = col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz duyuru id")
        client.close()
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Duyuru bulunamadı")
        return {"success": True, "modified": res.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.delete("/api/mongo/kurum-duyuru/{id}", tags=["Kurum Duyuru"], summary="Kurum duyurusu sil")
async def delete_kurum_duyuru(id: str):
    try:
        client, col = _get_kurum_duyuru_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            res = col.delete_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz duyuru id")
        client.close()
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Duyuru bulunamadı")
        return {"success": True, "deleted": res.deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


# ==============================
# Proxy Koleksiyonu Yardımcı Fonksiyonları
# ==============================

def _get_proxy_collection():
    """Proxy koleksiyonunu döner"""
    client = _get_mongodb_client()
    if not client:
        return None, None
    database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
    db = client[database_name]
    return client, db["proxies"]


def get_proxy_from_db() -> Optional[Dict[str, str]]:
    """
    MongoDB'den aktif proxy bilgilerini çeker.
    Returns: {'http': 'http://user:pass@host:port', 'https': 'http://user:pass@host:port'} veya None
    """
    try:
        client, col = _get_proxy_collection()
        if not client:
            return None
        
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


# ==============================
# Proxy Koleksiyonu CRUD Endpoints
# ==============================

@app.get("/api/mongo/proxies", tags=["Proxy"], summary="Proxy listele")
async def list_proxies(limit: int = 100, offset: int = 0):
    try:
        client, col = _get_proxy_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        # Password'u gizle
        cursor = col.find().sort("created_at", -1).skip(offset).limit(limit)
        proxies = []
        for doc in cursor:
            proxy_data = {
                "id": str(doc["_id"]),
                "host": doc.get("host", ""),
                "port": doc.get("port", ""),
                "username": doc.get("username", ""),
                "password": "***" if doc.get("password") else "",  # Password'u gizle
                "is_active": doc.get("is_active", False),
                "created_at": doc.get("created_at", ""),
                "updated_at": doc.get("updated_at", "")
            }
            proxies.append(proxy_data)
        
        total = col.count_documents({})
        client.close()
        return {"success": True, "total": total, "data": proxies}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.post("/api/mongo/proxies", tags=["Proxy"], summary="Proxy oluştur")
async def create_proxy(body: Dict[str, Any]):
    try:
        client, col = _get_proxy_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        data = body or {}
        host = (data.get("host") or "").strip()
        port = (data.get("port") or "").strip()
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        is_active = data.get("is_active", True)
        
        if not host or not port:
            client.close()
            raise HTTPException(status_code=400, detail="'host' ve 'port' zorunludur")
        
        # Port'un sayısal olup olmadığını kontrol et
        try:
            port_int = int(port)
            if port_int < 1 or port_int > 65535:
                client.close()
                raise HTTPException(status_code=400, detail="Port 1-65535 arasında olmalıdır")
        except ValueError:
            client.close()
            raise HTTPException(status_code=400, detail="Port geçerli bir sayı olmalıdır")
        
        # Eğer yeni proxy aktif yapılıyorsa, diğer aktif proxy'leri pasif yap
        if is_active:
            col.update_many({"is_active": True}, {"$set": {"is_active": False, "updated_at": datetime.now().isoformat()}})
        
        doc = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "is_active": is_active,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        res = col.insert_one(doc)
        new_id = str(res.inserted_id)
        client.close()
        return {"success": True, "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.get("/api/mongo/proxies/{id}", tags=["Proxy"], summary="Proxy getir")
async def get_proxy(id: str):
    try:
        client, col = _get_proxy_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        try:
            doc = col.find_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz proxy id")
        
        client.close()
        if not doc:
            raise HTTPException(status_code=404, detail="Proxy bulunamadı")
        
        return {
            "success": True,
            "data": {
                "id": str(doc["_id"]),
                "host": doc.get("host", ""),
                "port": doc.get("port", ""),
                "username": doc.get("username", ""),
                "password": "***" if doc.get("password") else "",  # Password'u gizle
                "is_active": doc.get("is_active", False),
                "created_at": doc.get("created_at", ""),
                "updated_at": doc.get("updated_at", "")
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.put("/api/mongo/proxies/{id}", tags=["Proxy"], summary="Proxy güncelle")
async def update_proxy(id: str, body: Dict[str, Any]):
    try:
        client, col = _get_proxy_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        data = body or {}
        update_data = {"updated_at": datetime.now().isoformat()}
        
        if "host" in data:
            host = (data.get("host") or "").strip()
            if not host:
                client.close()
                raise HTTPException(status_code=400, detail="'host' boş olamaz")
            update_data["host"] = host
        
        if "port" in data:
            port = (data.get("port") or "").strip()
            if not port:
                client.close()
                raise HTTPException(status_code=400, detail="'port' boş olamaz")
            try:
                port_int = int(port)
                if port_int < 1 or port_int > 65535:
                    client.close()
                    raise HTTPException(status_code=400, detail="Port 1-65535 arasında olmalıdır")
            except ValueError:
                client.close()
                raise HTTPException(status_code=400, detail="Port geçerli bir sayı olmalıdır")
            update_data["port"] = port
        
        if "username" in data:
            update_data["username"] = (data.get("username") or "").strip()
        
        if "password" in data:
            update_data["password"] = (data.get("password") or "").strip()
        
        if "is_active" in data:
            is_active = data.get("is_active", False)
            # Eğer proxy aktif yapılıyorsa, diğer aktif proxy'leri pasif yap
            if is_active:
                col.update_many(
                    {"is_active": True, "_id": {"$ne": ObjectId(id)}},
                    {"$set": {"is_active": False, "updated_at": datetime.now().isoformat()}}
                )
            update_data["is_active"] = is_active
        
        if not update_data or len(update_data) == 1:  # Sadece updated_at varsa
            client.close()
            return {"success": True, "modified": 0, "message": "Güncellenecek alan yok"}
        
        try:
            res = col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz proxy id")
        
        client.close()
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Proxy bulunamadı")
        return {"success": True, "modified": res.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.delete("/api/mongo/proxies/{id}", tags=["Proxy"], summary="Proxy sil")
async def delete_proxy(id: str):
    try:
        client, col = _get_proxy_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        try:
            res = col.delete_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz proxy id")
        
        client.close()
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Proxy bulunamadı")
        return {"success": True, "deleted": res.deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.post("/api/mongo/proxies/test", tags=["Proxy"], summary="Proxy bağlantı testi (KAYSİS)")
async def test_proxy_connection(body: Dict[str, Any]):
    """
    Proxy bağlantısını KAYSİS sitesine test eder.
    curl_cffi kullanarak Chrome tarayıcısını taklit eder ve WAF engellemelerini aşar.
    
    Args:
        body: {"id": "proxy_id", "detsis": "22620739"} (detsis opsiyonel, varsayılan: 22620739 - SGK)
    
    Returns:
        Test sonuçları (IP bilgisi, bağlantı durumu, hata mesajları)
    """
    try:
        # Body'den proxy ID'yi al
        if not body or not body.get("id"):
            raise HTTPException(status_code=400, detail="Body'de 'id' alanı zorunludur")
        
        proxy_id = str(body.get("id")).strip()
        if not proxy_id:
            raise HTTPException(status_code=400, detail="Proxy ID boş olamaz")
        
        # Proxy bilgilerini MongoDB'den çek
        client, col = _get_proxy_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        try:
            proxy_doc = col.find_one({"_id": ObjectId(proxy_id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz proxy id formatı")
        
        client.close()
        
        if not proxy_doc:
            raise HTTPException(status_code=404, detail=f"Proxy bulunamadı (ID: {proxy_id})")
        
        # Proxy bilgilerini hazırla
        host = proxy_doc.get("host", "").strip()
        port = proxy_doc.get("port", "").strip()
        username = proxy_doc.get("username", "").strip()
        password = proxy_doc.get("password", "").strip()
        
        if not host or not port:
            raise HTTPException(status_code=400, detail="Proxy bilgileri eksik (host veya port)")
        
        # Proxy URL'ini oluştur
        if username and password:
            proxy_auth = f"{username}:{password}"
            proxy_url = f"{proxy_auth}@{host}:{port}"
        else:
            proxy_url = f"{host}:{port}"
        
        proxies = {
            'http': f'http://{proxy_url}',
            'https': f'http://{proxy_url}'
        }
        
        # DETSIS numarasını al (varsayılan: 22620739 - SGK)
        detsis = "22620739"
        if body.get("detsis"):
            detsis = str(body.get("detsis")).strip()
        
        test_url = f"https://kms.kaysis.gov.tr/Home/Kurum/{detsis}"
        
        result = {
            "success": False,
            "proxy_id": proxy_id,
            "proxy_host": host,
            "proxy_port": port,
            "test_url": test_url,
            "detsis": detsis,
            "ip_info": None,
            "connection_status": None,
            "http_status": None,
            "response_size": None,
            "error": None,
            "curl_cffi_available": CURL_CFFI_AVAILABLE
        }
        
        # 1. IP kontrolü
        try:
            print(f"🌍 Proxy IP adresi kontrol ediliyor... (Proxy ID: {proxy_id})")
            ip_response = requests.get(
                'https://ipv4.icanhazip.com',
                proxies=proxies,
                timeout=10,
                impersonate="chrome110" if CURL_CFFI_AVAILABLE else None
            )
            ip_address = ip_response.text.strip()
            
            # IP lokasyon bilgisini al
            try:
                geo_response = requests.get(
                    f'http://ip-api.com/json/{ip_address}?fields=status,country,countryCode,city,query',
                    proxies=proxies,
                    timeout=10,
                    impersonate="chrome110" if CURL_CFFI_AVAILABLE else None
                )
                geo_data = geo_response.json()
                
                if geo_data.get('status') == 'success':
                    result["ip_info"] = {
                        "ip": ip_address,
                        "country": geo_data.get('country', 'Bilinmiyor'),
                        "country_code": geo_data.get('countryCode', 'Bilinmiyor'),
                        "city": geo_data.get('city', 'Bilinmiyor'),
                        "is_turkey": geo_data.get('countryCode') == 'TR'
                    }
                else:
                    result["ip_info"] = {"ip": ip_address}
            except Exception as e:
                result["ip_info"] = {"ip": ip_address, "error": str(e)}
        except Exception as e:
            result["ip_info"] = {"error": f"IP kontrolü başarısız: {str(e)}"}
        
        # 2. KAYSİS bağlantı testi
        try:
            print(f"🌐 KAYSİS sitesine bağlanılıyor... (Proxy ID: {proxy_id})")
            
            # Gerçek bir Chrome tarayıcısının gönderdiği tüm header'lar
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.google.com/',
                'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
                'Connection': 'keep-alive',
                'Cache-Control': 'max-age=0'
            }
            
            # curl_cffi ile Chrome taklidi yap (eğer mevcut ise)
            if CURL_CFFI_AVAILABLE:
                response = requests.get(
                    test_url,
                    headers=headers,
                    proxies=proxies,
                    timeout=1200,  # 20 dakika timeout
                    impersonate="chrome110"  # Chrome 110 TLS fingerprint
                )
            else:
                response = requests.get(test_url, headers=headers, timeout=1200, proxies=proxies)  # 20 dakika timeout
            
            result["http_status"] = response.status_code
            result["response_size"] = len(response.content)
            
            if response.status_code == 200:
                result["success"] = True
                result["connection_status"] = "success"
                
                # HTML içeriğinde başarılı yükleme işaretleri kontrol et
                content = response.text.lower()
                if 'accordion' in content or 'panel' in content or 'kurum' in content:
                    result["content_check"] = "KAYSİS yapısı tespit edildi"
                else:
                    result["content_check"] = "Sayfa yüklendi ancak beklenen içerik bulunamadı"
            else:
                result["connection_status"] = "failed"
                result["error"] = f"HTTP {response.status_code}: {response.text[:200] if response.text else 'Boş yanıt'}"
                
        except requests.exceptions.ProxyError as e:
            result["connection_status"] = "proxy_error"
            result["error"] = f"Proxy hatası: {str(e)}"
        except requests.exceptions.Timeout:
            result["connection_status"] = "timeout"
            result["error"] = "Zaman aşımı: Bağlantı 30 saniye içinde tamamlanamadı"
        except requests.exceptions.ConnectionError as e:
            result["connection_status"] = "connection_error"
            result["error"] = f"Bağlantı hatası: {str(e)}"
        except Exception as e:
            result["connection_status"] = "error"
            result["error"] = f"Beklenmeyen hata: {str(e)}"
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test sırasında hata: {str(e)}")

@app.post("/api/mongo/kurumlar", tags=["Kurumlar"], summary="Kurum oluştur")
async def create_kurum(
    kurum_adi: str = Form(...),
    aciklama: Optional[str] = Form(None),
    detsis: Optional[str] = Form(None),
    logo: Optional[UploadFile] = File(None)
):
    """
    Yeni kurum oluşturur (multipart/form-data).
    - kurum_adi: Zorunlu
    - aciklama: Opsiyonel
    - detsis: Opsiyonel (DETSIS numarası)
    - logo: Opsiyonel (PNG, JPG, JPEG, SVG, GIF, WEBP)
    """
    try:
        client, col = _get_kurumlar_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        if not kurum_adi or not str(kurum_adi).strip():
            client.close()
            raise HTTPException(status_code=400, detail="'kurum_adi' zorunlu")
        
        # Logo varsa yükle
        logo_url = None
        if logo:
            # Dosya formatını kontrol et
            allowed_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp'}
            file_extension = Path(logo.filename or '').suffix.lower()
            
            if file_extension not in allowed_extensions:
                client.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"Desteklenmeyen dosya formatı. İzin verilen formatlar: {', '.join(allowed_extensions)}"
                )
            
            # Content type'ı belirle
            content_type_map = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.svg': 'image/svg+xml',
                '.gif': 'image/gif',
                '.webp': 'image/webp'
            }
            content_type = content_type_map.get(file_extension, logo.content_type or 'image/png')
            
            # Dosya içeriğini oku
            file_data = await logo.read()
            
            # Dosya adını oluştur
            safe_filename = _transliterate_turkish(kurum_adi)
            safe_filename = re.sub(r'[^a-zA-Z0-9\s-]', '', safe_filename).strip()
            safe_filename = re.sub(r'\s+', '_', safe_filename)
            safe_filename = re.sub(r'_+', '_', safe_filename)
            
            # Geçici ID oluştur (henüz MongoDB'de yok)
            temp_id = str(ObjectId())
            logo_filename = f"{safe_filename}_{temp_id}{file_extension}"
            
            # Bunny.net'e yükle
            logo_url = _upload_logo_to_bunny(file_data, logo_filename, content_type)
            
            if not logo_url:
                client.close()
                raise HTTPException(status_code=500, detail="Logo Bunny.net'e yüklenemedi")
        
        # MongoDB'ye kaydet
        data = {
            "kurum_adi": kurum_adi.strip(),
            "olusturulma_tarihi": datetime.now().isoformat()
        }
        
        if aciklama:
            data["aciklama"] = aciklama.strip()
        
        if detsis:
            data["detsis"] = detsis.strip()
        
        if logo_url:
            data["kurum_logo"] = logo_url
        
        res = col.insert_one(data)
        new_id = str(res.inserted_id)
        client.close()
        
        return {
            "success": True,
            "id": new_id,
            "logo_url": logo_url
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.get("/api/mongo/kurumlar/{id}", tags=["Kurumlar"], summary="Kurum getir")
async def get_kurum(id: str):
    try:
        client, col = _get_kurumlar_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            d = col.find_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz kurum id")
        if not d:
            client.close()
            raise HTTPException(status_code=404, detail="Kurum bulunamadı")
        d["_id"] = str(d["_id"])
        client.close()
        return {"success": True, "data": d}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.put("/api/mongo/kurumlar/{id}", tags=["Kurumlar"], summary="Kurum güncelle (logo destekli)")
async def update_kurum(
    id: str,
    kurum_adi: Optional[str] = Form(None),
    aciklama: Optional[str] = Form(None),
    detsis: Optional[str] = Form(None),
    logo: Optional[UploadFile] = File(None)
):
    """
    Kurum bilgilerini günceller (multipart/form-data).
    - kurum_adi: Opsiyonel (gönderilirse güncellenir)
    - aciklama: Opsiyonel (gönderilirse güncellenir)
    - detsis: Opsiyonel (gönderilirse güncellenir - DETSIS numarası)
    - logo: Opsiyonel (gönderilirse yüklenir ve güncellenir) (PNG, JPG, JPEG, SVG, GIF, WEBP)
    """
    try:
        client, col = _get_kurumlar_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        
        # Kurum var mı kontrol et
        try:
            kurum = col.find_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz kurum id")
        
        if not kurum:
            client.close()
            raise HTTPException(status_code=404, detail="Kurum bulunamadı")
        
        update_data: Dict[str, Any] = {}
        
        # Logo varsa yükle
        if logo:
            # Dosya formatını kontrol et
            allowed_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp'}
            file_extension = Path(logo.filename or '').suffix.lower()
            
            if file_extension not in allowed_extensions:
                client.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"Desteklenmeyen dosya formatı. İzin verilen formatlar: {', '.join(allowed_extensions)}"
                )
            
            # Content type'ı belirle
            content_type_map = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.svg': 'image/svg+xml',
                '.gif': 'image/gif',
                '.webp': 'image/webp'
            }
            content_type = content_type_map.get(file_extension, logo.content_type or 'image/png')
            
            # Dosya içeriğini oku
            file_data = await logo.read()
            
            # Dosya adını oluştur (kurum adından veya mevcut kurum adından)
            kurum_adi_for_filename = kurum_adi.strip() if kurum_adi else kurum.get('kurum_adi', 'kurum')
            safe_filename = _transliterate_turkish(kurum_adi_for_filename)
            safe_filename = re.sub(r'[^a-zA-Z0-9\s-]', '', safe_filename).strip()
            safe_filename = re.sub(r'\s+', '_', safe_filename)
            safe_filename = re.sub(r'_+', '_', safe_filename)
            logo_filename = f"{safe_filename}_{id}{file_extension}"
            
            # Bunny.net'e yükle
            logo_url = _upload_logo_to_bunny(file_data, logo_filename, content_type)
            
            if not logo_url:
                client.close()
                raise HTTPException(status_code=500, detail="Logo Bunny.net'e yüklenemedi")
            
            update_data["kurum_logo"] = logo_url
        
        # Diğer alanları güncelle
        if kurum_adi is not None:
            update_data["kurum_adi"] = kurum_adi.strip()
        
        if aciklama is not None:
            update_data["aciklama"] = aciklama.strip()
        
        if detsis is not None:
            update_data["detsis"] = detsis.strip()
        
        if not update_data:
            client.close()
            return {"success": True, "modified": 0, "message": "Güncellenecek alan yok"}
        
        # MongoDB'yi güncelle
        try:
            res = col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz kurum id")
        
        client.close()
        
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Kurum bulunamadı")
        
        return {
            "success": True,
            "modified": res.modified_count,
            "logo_url": update_data.get("kurum_logo") if "kurum_logo" in update_data else None
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


@app.delete("/api/mongo/kurumlar/{id}", tags=["Kurumlar"], summary="Kurum sil")
async def delete_kurum(id: str):
    try:
        client, col = _get_kurumlar_collection()
        if not client:
            raise HTTPException(status_code=500, detail="MongoDB bağlantısı kurulamadı")
        try:
            res = col.delete_one({"_id": ObjectId(id)})
        except Exception:
            client.close()
            raise HTTPException(status_code=400, detail="Geçersiz kurum id")
        client.close()
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Kurum bulunamadı")
        return {"success": True, "deleted": res.deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hata: {str(e)}")


def _get_deepseek_api_key() -> Optional[str]:
    # 1) Env
    env_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    # 2) config.json
    cfg = _load_config()
    if cfg:
        cfg_key = (cfg.get("deepseek_api_key") or "").strip()
        if cfg_key:
            return cfg_key
    # 3) Replit local state (opsiyonel)
    try:
        replit_state = Path(".local/state/replit/agent/filesystem/filesystem_state.json")
        if replit_state.exists():
            content = replit_state.read_text(encoding='utf-8', errors='ignore')
            # Basit bir anahtar araması
            import re as _re
            m = _re.search(r"sk-[A-Za-z0-9]{32,}", content)
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def _login_with_config(cfg: Dict[str, Any]) -> Optional[str]:
    try:
        api_base_url = cfg.get("api_base_url")
        email = cfg.get("admin_email")
        password = cfg.get("admin_password")
        if not all([api_base_url, email, password]):
            return None
        # API isteklerinde proxy kullanılmıyor
        
        login_url = f"{api_base_url.rstrip('/')}/api/auth/login"
        resp = requests.post(login_url, headers={"Content-Type": "application/json"}, json={
            "email": email,
            "password": password
        }, timeout=1200)  # 20 dakika timeout (MevzuatGPT yükleme sürecinin parçası)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("access_token")
        return None
    except Exception:
        return None


def _transliterate_turkish(text: str) -> str:
    """Türkçe karakterleri İngilizce karşılıklarına çevirir (kaldırmaz)"""
    if not text:
        return ""
    
    # Türkçe karakterleri İngilizce karşılıklarına çevir
    char_map = {
        'ç': 'c', 'ğ': 'g', 'ı': 'i', 'ö': 'o', 'ş': 's', 'ü': 'u',
        'Ç': 'C', 'Ğ': 'G', 'İ': 'I', 'Ö': 'O', 'Ş': 'S', 'Ü': 'U'
    }
    
    result = text
    for tr_char, en_char in char_map.items():
        result = result.replace(tr_char, en_char)
    
    return result


def _create_url_slug(text: str) -> str:
    """URL-friendly slug oluşturur (alt tire ile, sınırsız)"""
    if not text:
        return "pdf_document"
    
    # Türkçe karakterleri İngilizce karşılıklarına çevir
    slug = _transliterate_turkish(text)
    
    # Unicode normalize
    slug = unicodedata.normalize('NFKD', slug)
    
    # Küçük harf yap
    slug = slug.lower()
    
    # Sadece harfler, rakamlar ve boşluk
    slug = re.sub(r'[^a-z0-9\s]', '', slug)
    
    # Çoklu boşlukları alt tire ile değiştir
    slug = re.sub(r'\s+', '_', slug)
    
    # Çoklu alt tireleri tek alt tire yap
    slug = re.sub(r'_+', '_', slug)
    
    # Başındaki ve sonundaki alt tireleri kaldır
    slug = slug.strip('_')
    
    # Kısaltma yok, tam uzunluk
    
    return slug or "pdf_document"


def _upload_to_bunny(pdf_path: str, filename: str) -> Optional[str]:
    """PDF'i Bunny.net'e yükler ve public URL döner"""
    try:
        print(f"📤 [Bunny.net Upload] Başlatılıyor...")
        print(f"   📄 Dosya: {pdf_path}")
        print(f"   📝 Filename: {filename}")
        
        api_key = os.getenv("BUNNY_STORAGE_API_KEY")
        storage_zone = os.getenv("BUNNY_STORAGE_ZONE", "mevzuatgpt")
        storage_region = os.getenv("BUNNY_STORAGE_REGION", "storage.bunnycdn.com")
        storage_endpoint = os.getenv("BUNNY_STORAGE_ENDPOINT", "https://cdn.mevzuatgpt.org")
        storage_folder = os.getenv("BUNNY_STORAGE_FOLDER", "portal")
        
        print(f"   🌐 Storage Zone: {storage_zone}")
        print(f"   🌐 Storage Region: {storage_region}")
        print(f"   📂 Storage Folder: {storage_folder}")
        
        if not api_key:
            print("❌ [Bunny.net Upload] API anahtarı bulunamadı")
            return None
        
        # PDF dosyasını oku
        print(f"   📖 PDF dosyası okunuyor...")
        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()
        file_size = len(pdf_data)
        file_size_mb = round(file_size / (1024 * 1024), 2)
        print(f"   ✅ Dosya okundu: {file_size:,} bytes ({file_size_mb} MB)")
        
        # URL-safe filename
        safe_filename = urllib.parse.quote(filename)
        upload_url = f"https://{storage_region}/{storage_zone}/{storage_folder}/{safe_filename}"
        print(f"   🌐 Upload URL: {upload_url}")
        
        headers = {
            'AccessKey': api_key,
            'Content-Type': 'application/pdf',
            'User-Agent': 'SGK-Scraper-API/1.0'
        }
        
        print(f"   🚀 Bunny.net'e yükleme başlatılıyor...")
        print(f"   ⏱️ Timeout: 1200 saniye (20 dakika)")
        response = requests.put(upload_url, headers=headers, data=pdf_data, timeout=1200)  # 20 dakika timeout
        
        print(f"   📡 Response alındı")
        print(f"   📊 Status Code: {response.status_code}")
        print(f"   📋 Response headers: {dict(response.headers)}")
        
        if response.status_code == 201:
            public_url = f"{storage_endpoint}/{storage_folder}/{safe_filename}"
            print(f"✅ [Bunny.net Upload] Başarılı!")
            print(f"   🔗 Public URL: {public_url}")
            return public_url
        else:
            print(f"❌ [Bunny.net Upload] Başarısız!")
            print(f"   📝 Response body (ilk 500 karakter): {response.text[:500]}")
            if len(response.text) > 500:
                print(f"      ... (toplam {len(response.text)} karakter)")
            return None
            
    except requests.exceptions.Timeout:
        print(f"❌ [Bunny.net Upload] Zaman aşımı (20 dakika)")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ [Bunny.net Upload] Ağ hatası: {str(e)}")
        return None
    except Exception as e:
        print(f"❌ [Bunny.net Upload] Beklenmeyen hata: {str(e)}")
        import traceback
        print(f"   📋 Traceback: {traceback.format_exc()}")
        return None


def _upload_logo_to_bunny(file_data: bytes, filename: str, content_type: str) -> Optional[str]:
    """Logo/resim dosyasını Bunny.net'e yükler ve public URL döner (referans koddaki mantık)"""
    try:
        api_key = os.getenv("BUNNY_STORAGE_API_KEY")
        storage_zone = os.getenv("BUNNY_STORAGE_ZONE", "mevzuatgpt")
        storage_region = os.getenv("BUNNY_STORAGE_REGION", "storage.bunnycdn.com")
        storage_endpoint = os.getenv("BUNNY_STORAGE_ENDPOINT", "https://cdn.mevzuatgpt.org")
        storage_folder = os.getenv("BUNNY_STORAGE_FOLDER", "portal")
        
        if not api_key:
            print("Bunny.net API anahtarı bulunamadı")
            return None
        
        # URL-safe filename
        safe_filename = urllib.parse.quote(filename)
        upload_url = f"https://{storage_region}/{storage_zone}/{storage_folder}/{safe_filename}"
        
        print(f"Logo yükleniyor: {upload_url}")
        
        headers = {
            'AccessKey': api_key,
            'Content-Type': content_type,
            'User-Agent': 'SGK-Scraper-API/1.0'
        }
        
        # Upload file
        response = requests.put(upload_url, headers=headers, data=file_data, timeout=1200)  # 20 dakika timeout
        
        if response.status_code == 201:
            # Return public URL
            public_url = f"{storage_endpoint}/{storage_folder}/{safe_filename}"
            print("Logo başarıyla Bunny.net'e yüklendi")
            return public_url
        else:
            print(f"Logo yükleme hatası: {response.status_code} - {response.text}")
            return None
            
    except requests.exceptions.Timeout:
        print("Logo yükleme zaman aşımına uğradı")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Logo yükleme ağ hatası: {str(e)}")
        return None
    except Exception as e:
        print(f"Beklenmeyen logo yükleme hatası: {str(e)}")
        return None


def _delete_from_bunny(pdf_url: str) -> bool:
    """Bunny.net'ten PDF dosyasını siler"""
    try:
        if not pdf_url or not pdf_url.strip():
            print("⚠️ PDF URL boş, silme işlemi atlandı")
            return False
        
        api_key = os.getenv("BUNNY_STORAGE_API_KEY")
        storage_zone = os.getenv("BUNNY_STORAGE_ZONE", "mevzuatgpt")
        storage_region = os.getenv("BUNNY_STORAGE_REGION", "storage.bunnycdn.com")
        storage_endpoint = os.getenv("BUNNY_STORAGE_ENDPOINT", "https://cdn.mevzuatgpt.org")
        storage_folder = os.getenv("BUNNY_STORAGE_FOLDER", "portal")
        
        if not api_key:
            print("⚠️ Bunny.net API anahtarı bulunamadı, silme işlemi atlandı")
            return False
        
        # PDF URL'den dosya adını çıkar
        # Format: https://cdn.mevzuatgpt.org/portal/filename.pdf
        # veya: https://cdn.mevzuatgpt.org/portal/filename%20with%20spaces.pdf
        try:
            # URL'den dosya adını al
            if storage_endpoint in pdf_url:
                # Endpoint'ten sonraki kısmı al
                file_path = pdf_url.split(storage_endpoint, 1)[1]
                # Başındaki /portal/ kısmını kaldır
                if file_path.startswith(f"/{storage_folder}/"):
                    filename = file_path[len(f"/{storage_folder}/"):]
                else:
                    filename = file_path.lstrip("/")
            else:
                # Farklı format olabilir, direkt dosya adını çıkar
                filename = os.path.basename(pdf_url)
            
            if not filename:
                print(f"⚠️ PDF URL'den dosya adı çıkarılamadı: {pdf_url}")
                return False
            
            # URL decode yap (eğer encoded ise)
            filename = urllib.parse.unquote(filename)
            
            # URL-safe filename (tekrar encode et)
            safe_filename = urllib.parse.quote(filename)
            
            # Delete URL oluştur
            delete_url = f"https://{storage_region}/{storage_zone}/{storage_folder}/{safe_filename}"
            
            headers = {
                'AccessKey': api_key,
                'User-Agent': 'SGK-Scraper-API/1.0'
            }
            
            print(f"🗑️ Bunny.net'ten siliniyor: {filename}")
            response = requests.delete(delete_url, headers=headers, timeout=30)
            
            if response.status_code == 200 or response.status_code == 204:
                print(f"✅ PDF Bunny.net'ten başarıyla silindi: {filename}")
                return True
            elif response.status_code == 404:
                print(f"⚠️ PDF Bunny.net'te bulunamadı (zaten silinmiş olabilir): {filename}")
                return True  # Zaten yoksa başarılı say
            else:
                print(f"⚠️ Bunny.net silme hatası: {response.status_code} - {response.text}")
                return False
                
        except Exception as parse_error:
            print(f"⚠️ PDF URL parse hatası: {str(parse_error)}")
            return False
            
    except Exception as e:
        print(f"⚠️ Bunny.net silme hatası: {str(e)}")
        return False


def _get_mongodb_client() -> Optional[MongoClient]:
    """MongoDB bağlantısı oluşturur"""
    try:
        connection_string = os.getenv("MONGODB_CONNECTION_STRING")
        if not connection_string:
            print("MongoDB bağlantı dizesi bulunamadı")
            return None
        
        client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
        # Test connection
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"MongoDB bağlantı hatası: {str(e)}")
        return None


def _check_document_name_exists(belge_adi: str, mode: str) -> Tuple[bool, bool, Optional[str]]:
    """
    Belge adının hem Supabase (MevzuatGPT API) hem de MongoDB (Portal) üzerinde 
    daha önce yüklenip yüklenmediğini kontrol eder.
    
    Args:
        belge_adi: Kontrol edilecek belge adı
        mode: İşlem modu ('m': MevzuatGPT, 'p': Portal, 't': Tamamı)
    
    Returns:
        (exists_in_mevzuatgpt, exists_in_portal, error_message) tuple:
        - exists_in_mevzuatgpt: True ise MevzuatGPT'de mevcut
        - exists_in_portal: True ise Portal'da mevcut
        - error_message: Hata mesajı (varsa)
    """
    exists_in_mevzuatgpt = False
    exists_in_portal = False
    
    try:
        print("=" * 80)
        print("🔍 BELGE ADI KONTROLÜ")
        print("=" * 80)
        print(f"   📄 Kontrol edilen belge adı: {belge_adi}")
        print(f"   🔧 İşlem modu: {mode.upper()}")
        
        belge_normalized = normalize_for_exact_match(belge_adi)
        print(f"   🔤 Normalize edilmiş ad: {belge_normalized}")
        
        # MevzuatGPT (Supabase/API) kontrolü - 'm' ve 't' modları için
        if mode in ["m", "t"]:
            print("\n   📡 [1/2] MevzuatGPT (Supabase) kontrolü yapılıyor...")
            print(f"   🌐 Endpoint: /api/admin/documents")
            try:
                cfg = _load_config()
                if cfg:
                    token = _login_with_config(cfg)
                    if token:
                        api_base_url = cfg.get("api_base_url")
                        uploaded_docs = get_uploaded_documents(api_base_url, token, use_streamlit=False)
                        print(f"   📊 API'den {len(uploaded_docs)} belge çekildi")
                        
                        for doc in uploaded_docs:
                            # Birden fazla alan kontrol et (API'den dönen belgelerde farklı alan isimleri olabilir)
                            doc_titles = [
                                doc.get("belge_adi", ""),
                                doc.get("document_name", ""),
                                doc.get("title", ""),
                                doc.get("filename", ""),
                                doc.get("name", "")
                            ]
                            
                            for doc_title in doc_titles:
                                if doc_title:
                                    doc_normalized = normalize_for_exact_match(doc_title)
                                    if belge_normalized == doc_normalized:
                                        exists_in_mevzuatgpt = True
                                        print(f"   ✅ MevzuatGPT'de bulundu: '{doc_title}'")
                                        break
                            
                            if exists_in_mevzuatgpt:
                                break
                        
                        if not exists_in_mevzuatgpt:
                            print(f"   ❌ MevzuatGPT'de bulunamadı ({len(uploaded_docs)} belge kontrol edildi)")
                    else:
                        print("   ⚠️ MevzuatGPT login başarısız, kontrol atlandı")
                else:
                    print("   ⚠️ Config bulunamadı, MevzuatGPT kontrolü atlandı")
            except Exception as e:
                print(f"   ⚠️ MevzuatGPT kontrolü sırasında hata: {str(e)}")
                import traceback
                print(f"   📋 Traceback: {traceback.format_exc()}")
                # Hata olsa bile devam et, sadece uyarı ver
        
        # Portal (MongoDB) kontrolü - 'p' ve 't' modları için
        if mode in ["p", "t"]:
            print("\n   🗄️ [2/2] Portal (MongoDB) kontrolü yapılıyor...")
            try:
                client = _get_mongodb_client()
                if client:
                    database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                    metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
                    db = client[database_name]
                    metadata_collection = db[metadata_collection_name]
                    
                    # MongoDB'den tüm pdf_adi'leri çek ve kontrol et
                    cursor = metadata_collection.find({}, {"pdf_adi": 1})
                    count = 0
                    for doc in cursor:
                        pdf_adi = doc.get("pdf_adi", "")
                        if pdf_adi:
                            pdf_normalized = normalize_for_exact_match(pdf_adi)
                            if belge_normalized == pdf_normalized:
                                exists_in_portal = True
                                print(f"   ✅ Portal'da bulundu: {pdf_adi}")
                                break
                        count += 1
                    
                    client.close()
                    if not exists_in_portal:
                        print(f"   ❌ Portal'da bulunamadı ({count} belge kontrol edildi)")
                else:
                    print("   ⚠️ MongoDB bağlantısı kurulamadı, Portal kontrolü atlandı")
            except Exception as e:
                print(f"   ⚠️ Portal kontrolü sırasında hata: {str(e)}")
                # Hata olsa bile devam et, sadece uyarı ver
        
        # Sonuç özeti
        print("\n   📊 Kontrol Sonuçları:")
        print(f"      - MevzuatGPT: {'✅ Mevcut' if exists_in_mevzuatgpt else '❌ Yok'}")
        print(f"      - Portal: {'✅ Mevcut' if exists_in_portal else '❌ Yok'}")
        
        # Her ikisinde de varsa hata mesajı oluştur
        if exists_in_mevzuatgpt and exists_in_portal:
            error_msg = f"Bu belge adı ('{belge_adi}') hem MevzuatGPT'de hem de Portal'da zaten mevcut. Yükleme yapılmayacak."
            print(f"\n   ❌ {error_msg}")
            return exists_in_mevzuatgpt, exists_in_portal, error_msg
        
        print("\n   ✅ Belge adı kontrolü tamamlandı")
        return exists_in_mevzuatgpt, exists_in_portal, None
        
    except Exception as e:
        print(f"   ❌ Belge adı kontrolü sırasında beklenmeyen hata: {str(e)}")
        # Hata durumunda güvenli tarafta kal, kontrolü geç
        return False, False, None


def _save_to_mongodb(metadata: Dict[str, Any], content: str) -> Optional[str]:
    """Metadata ve content'i MongoDB'ye kaydeder, metadata_id döner"""
    try:
        print(f"💾 [MongoDB Save] Başlatılıyor...")
        
        client = _get_mongodb_client()
        if not client:
            print("❌ [MongoDB Save] MongoDB client bulunamadı")
            return None
        
        database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
        metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
        content_collection_name = os.getenv("MONGODB_CONTENT_COLLECTION", "content")
        
        print(f"   🗄️ Database: {database_name}")
        print(f"   📋 Metadata Collection: {metadata_collection_name}")
        print(f"   📄 Content Collection: {content_collection_name}")
        
        db = client[database_name]
        metadata_collection = db[metadata_collection_name]
        content_collection = db[content_collection_name]
        
        # Metadata kaydet
        print(f"   📝 Metadata temizleniyor...")
        clean_metadata = {}
        for key, value in metadata.items():
            if value is not None and value != '':
                clean_metadata[key] = value
        
        print(f"   📊 Metadata keys: {list(clean_metadata.keys())}")
        print(f"   📄 PDF Adı: {clean_metadata.get('pdf_adi', 'N/A')}")
        print(f"   🏢 Kurum ID: {clean_metadata.get('kurum_id', 'N/A')}")
        print(f"   📂 Belge Türü: {clean_metadata.get('belge_turu', 'N/A')}")
        print(f"   📊 Sayfa Sayısı: {clean_metadata.get('sayfa_sayisi', 'N/A')}")
        print(f"   💾 Dosya Boyutu: {clean_metadata.get('dosya_boyutu_mb', 'N/A')} MB")
        
        clean_metadata['olusturulma_tarihi'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"   💾 Metadata MongoDB'ye kaydediliyor...")
        metadata_result = metadata_collection.insert_one(clean_metadata)
        metadata_id = str(metadata_result.inserted_id)
        print(f"   ✅ Metadata kaydedildi: metadata_id={metadata_id}")
        
        # Content kaydet
        content_length = len(content)
        content_length_kb = round(content_length / 1024, 2)
        print(f"   📄 Content hazırlanıyor...")
        print(f"      📊 Content uzunluğu: {content_length:,} karakter ({content_length_kb} KB)")
        
        content_doc = {
            'metadata_id': ObjectId(metadata_id),
            'icerik': content,
            'olusturulma_tarihi': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        print(f"   💾 Content MongoDB'ye kaydediliyor...")
        content_result = content_collection.insert_one(content_doc)
        content_id = str(content_result.inserted_id)
        print(f"   ✅ Content kaydedildi: content_id={content_id}")
        
        client.close()
        print(f"✅ [MongoDB Save] Başarılı! metadata_id={metadata_id}")
        return metadata_id
        
    except Exception as e:
        print(f"❌ [MongoDB Save] Hata: {str(e)}")
        import traceback
        print(f"   📋 Traceback: {traceback.format_exc()}")
        return None


def _extract_pdf_text_markdown(pdf_path: str) -> Optional[str]:
    """PDF'den markdown formatında metin çıkarır (OCR desteği ile)"""
    try:
        import pdfplumber
        from io import BytesIO
        
        extracted_text = ""
        total_pages = 0
        
        # Önce PDF yapısını analiz et (daha doğru tespit için)
        processor = PDFProcessor()
        pdf_structure = processor.analyze_pdf_structure(pdf_path)
        total_pages = pdf_structure.get('total_pages', 0)
        text_coverage = pdf_structure.get('text_coverage', 0.0)
        has_text = pdf_structure.get('has_text', False)
        needs_ocr = pdf_structure.get('needs_ocr', False)
        
        # Resim formatı kontrolü: Eğer PDF resim formatındaysa direkt OCR ile başla
        # %30 eşiği: Metin kapsamı düşükse kalite zayıf olabilir, OCR daha iyi sonuç verebilir
        # Ayrıca, eğer metin varsa ama çok azsa (sadece başlıklar), OCR gerekli
        # Ortalama sayfa başına metin miktarını kontrol et
        avg_text_per_page = 0
        if total_pages > 0:
            # Hızlı kontrol: İlk 3 sayfadan ortalama metin miktarını hesapla
            import pdfplumber
            from io import BytesIO
            with open(pdf_path, 'rb') as f:
                pdf_bytes = f.read()
            pdf_file_obj = BytesIO(pdf_bytes)
            with pdfplumber.open(pdf_file_obj) as pdf:
                quick_check_pages = min(3, total_pages)
                quick_total_text = 0
                for page_num in range(quick_check_pages):
                    try:
                        page = pdf.pages[page_num]
                        page_text = page.extract_text()
                        if page_text:
                            quick_total_text += len(page_text.strip())
                    except Exception:
                        pass
                avg_text_per_page = quick_total_text / quick_check_pages if quick_check_pages > 0 else 0
        
        # Eğer ortalama sayfa başına metin 300 karakterden azsa, muhtemelen sadece başlıklar var
        is_image_pdf = not has_text or text_coverage < 0.3 or needs_ocr or (has_text and avg_text_per_page < 300)
        
        if is_image_pdf:
            print(f"📸 PDF resim formatında tespit edildi (kapsam: %{text_coverage*100:.1f}). OCR ile tüm {total_pages} sayfa işleniyor (sınırlama olmadan)...")
            try:
                if processor._check_ocr_available():
                    # Direkt OCR ile tüm sayfaları işle (sınırlama yok)
                    print(f"🔄 OCR başlatılıyor: {total_pages} sayfa işlenecek...")
                    ocr_text = processor.extract_text_from_pages(pdf_path, 1, total_pages, use_ocr=True)
                    if ocr_text and len(ocr_text.strip()) > 0:
                        extracted_text = _format_text_as_markdown(ocr_text)
                        ocr_char_count = len(ocr_text)
                        ocr_line_count = len([line for line in ocr_text.split('\n') if line.strip()])
                        print(f"✅ OCR tamamlandı: {total_pages} sayfa işlendi, {ocr_char_count:,} karakter, {ocr_line_count:,} satır çıkarıldı")
                        return extracted_text.strip()
                    else:
                        print("⚠️ OCR ile metin çıkarılamadı")
                else:
                    print("⚠️ OCR kütüphaneleri kurulu değil veya Poppler/RapidOCR eksik")
                    print("⚠️ Kurulum için: 'apt-get install poppler-utils' (Linux)")
                    print("⚠️ Python paketi: 'pip install rapidocr-onnxruntime'")
            except Exception as ocr_error:
                error_msg = str(ocr_error)
                print(f"❌ OCR hatası: {error_msg}")
                if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                    print("❌ Poppler kurulu değil! 'apt-get install poppler-utils' komutunu çalıştırın.")
                elif "rapidocr" in error_msg.lower() or "rapid" in error_msg.lower():
                    print("❌ RapidOCR kurulu değil! 'pip install rapidocr-onnxruntime' komutunu çalıştırın.")
                import traceback
                traceback.print_exc()
                return None
        
        # Normal metin çıkarma: PDF'de yeterli metin var
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        pdf_file_obj = BytesIO(pdf_bytes)
        
        with pdfplumber.open(pdf_file_obj) as pdf:
            if total_pages == 0:
                total_pages = len(pdf.pages)
            
            # Hızlı kontrol: İlk 3 sayfadan metin çıkar
            quick_check_pages = min(3, total_pages)
            total_text_length = 0
            pages_with_text = 0
            
            for page_num in range(quick_check_pages):
                try:
                    page = pdf.pages[page_num]
                    page_text = page.extract_text()
                    if page_text and len(page_text.strip()) > 10:
                        total_text_length += len(page_text.strip())
                        pages_with_text += 1
                except Exception:
                    pass
            
            # Metin kapsamını hesapla (ilk 3 sayfadan)
            quick_coverage = pages_with_text / quick_check_pages if quick_check_pages > 0 else 0.0
            
            # Resim formatı kontrolü: Eğer ilk 3 sayfada hiç metin yoksa, çok az metin varsa 
            # veya metin kapsamı %30'dan azsa veya toplam metin çok azsa direkt OCR ile tüm sayfaları işle
            # %30 eşiği: Metin kapsamı düşükse kalite zayıf olabilir, OCR daha iyi sonuç verebilir
            # Ayrıca, eğer metin varsa ama çok azsa (1000 karakterden az), bu da resim formatı olabilir
            should_use_ocr_directly = (
                pages_with_text == 0 or 
                (pages_with_text < 2 and total_text_length < 500) or
                quick_coverage < 0.3 or
                (pages_with_text > 0 and total_text_length < 1000)  # Metin var ama çok az
            )
            
            if should_use_ocr_directly:
                print(f"📸 PDF resim formatında tespit edildi (ilk {quick_check_pages} sayfada kapsam: %{quick_coverage*100:.1f}, metin: {pages_with_text}/{quick_check_pages} sayfa). OCR ile tüm {total_pages} sayfa işleniyor (sınırlama olmadan)...")
                try:
                    processor = PDFProcessor()
                    if processor._check_ocr_available():
                        # Direkt OCR ile tüm sayfaları işle (sınırlama yok)
                        print(f"🔄 OCR başlatılıyor: {total_pages} sayfa işlenecek...")
                        ocr_text = processor.extract_text_from_pages(pdf_path, 1, total_pages, use_ocr=True)
                        if ocr_text and len(ocr_text.strip()) > 0:
                            extracted_text = _format_text_as_markdown(ocr_text)
                            ocr_char_count = len(ocr_text)
                            ocr_line_count = len([line for line in ocr_text.split('\n') if line.strip()])
                            print(f"✅ OCR tamamlandı: {total_pages} sayfa işlendi, {ocr_char_count:,} karakter, {ocr_line_count:,} satır çıkarıldı")
                            return extracted_text.strip()
                        else:
                            print("⚠️ OCR ile metin çıkarılamadı")
                    else:
                        print("⚠️ OCR kütüphaneleri kurulu değil veya Poppler/RapidOCR eksik")
                        print("⚠️ Kurulum için: 'apt-get install poppler-utils' (Linux)")
                        print("⚠️ Python paketi: 'pip install rapidocr-onnxruntime'")
                except Exception as ocr_error:
                    error_msg = str(ocr_error)
                    print(f"❌ OCR hatası: {error_msg}")
                    if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                        print("❌ Poppler kurulu değil! 'apt-get install poppler-utils' komutunu çalıştırın.")
                    elif "rapidocr" in error_msg.lower() or "rapid" in error_msg.lower():
                        print("❌ RapidOCR kurulu değil! 'pip install rapidocr-onnxruntime' komutunu çalıştırın.")
                    import traceback
                    traceback.print_exc()
                    return None
            
            # Normal metin çıkarma: Tüm sayfaları işle (metin kapsamı yeterliyse)
            total_text_length = 0
            pages_with_text = 0
            
            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    page_text = page.extract_text()
                    if page_text and len(page_text.strip()) > 10:
                        # Basit markdown formatı
                        formatted_text = _format_text_as_markdown(page_text)
                        extracted_text += formatted_text + "\n\n"
                        total_text_length += len(page_text.strip())
                        pages_with_text += 1
                    else:
                        # Metin yoksa OCR ile dene
                        processor = PDFProcessor()
                        if processor._check_ocr_available():
                            try:
                                ocr_text = processor._extract_text_with_ocr(pdf_path, page_num - 1)
                                if ocr_text and len(ocr_text.strip()) > 0:
                                    formatted_text = _format_text_as_markdown(ocr_text)
                                    extracted_text += formatted_text + "\n\n"
                                    total_text_length += len(ocr_text.strip())
                                    pages_with_text += 1
                            except Exception:
                                pass
                except Exception as page_error:
                    # Sayfa hatası varsa OCR ile dene
                    processor = PDFProcessor()
                    if processor._check_ocr_available():
                        try:
                            ocr_text = processor._extract_text_with_ocr(pdf_path, page_num - 1)
                            if ocr_text and len(ocr_text.strip()) > 0:
                                formatted_text = _format_text_as_markdown(ocr_text)
                                extracted_text += formatted_text + "\n\n"
                        except Exception:
                            pass
                    continue
        
            # Metin kapsamını kontrol et: Eğer %30'dan az sayfa metin içeriyorsa veya toplam metin çok azsa OCR kullan
            # %30 eşiği: Metin kapsamı düşükse kalite zayıf olabilir, OCR daha iyi sonuç verebilir
            text_coverage = pages_with_text / total_pages if total_pages > 0 else 0.0
            should_use_ocr = text_coverage < 0.3 or total_text_length < 1000
        
        # Eğer metin yetersizse OCR ile tüm sayfaları işle
        if should_use_ocr and total_pages > 0:
            print(f"📸 PDF'de metin bulunamadı veya yetersiz (kapsam: %{text_coverage*100:.1f}, toplam: {total_text_length} karakter), OCR ile tüm {total_pages} sayfa işleniyor...")
            try:
                processor = PDFProcessor()
                if processor._check_ocr_available():
                    # Tüm sayfalar için OCR yap (use_ocr=True ile zorunlu OCR)
                    # end_page dahil olacak şekilde total_pages kullan
                    print(f"🔄 OCR başlatılıyor: {total_pages} sayfa işlenecek...")
                    ocr_text = processor.extract_text_from_pages(pdf_path, 1, total_pages, use_ocr=True)
                    if ocr_text and len(ocr_text.strip()) > 100:
                        extracted_text = _format_text_as_markdown(ocr_text)
                        ocr_char_count = len(ocr_text)
                        ocr_line_count = len([line for line in ocr_text.split('\n') if line.strip()])
                        print(f"✅ OCR tamamlandı: {total_pages} sayfa işlendi, {ocr_char_count:,} karakter, {ocr_line_count:,} satır çıkarıldı")
                    else:
                        print("⚠️ OCR ile metin çıkarılamadı veya çok az metin çıkarıldı")
                        if ocr_text:
                            print(f"⚠️ Çıkarılan metin uzunluğu: {len(ocr_text)} karakter (çok kısa)")
                else:
                    print("⚠️ OCR kütüphaneleri kurulu değil veya Poppler/RapidOCR eksik")
                    print("⚠️ Kurulum için: 'apt-get install poppler-utils' (Linux)")
                    print("⚠️ Python paketi: 'pip install rapidocr-onnxruntime'")
            except Exception as ocr_error:
                error_msg = str(ocr_error)
                print(f"❌ OCR hatası: {error_msg}")
                # Poppler veya RapidOCR eksikse özel mesaj
                if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                    print("❌ Poppler kurulu değil! 'apt-get install poppler-utils' komutunu çalıştırın.")
                elif "rapidocr" in error_msg.lower() or "rapid" in error_msg.lower():
                    print("❌ RapidOCR kurulu değil! 'pip install rapidocr-onnxruntime' komutunu çalıştırın.")
                import traceback
                traceback.print_exc()
        
        return extracted_text.strip() if extracted_text.strip() else None
        
    except Exception as e:
        print(f"PDF metin çıkarma hatası: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def _format_text_as_markdown(text: str) -> str:
    """Metni markdown formatına çevirir"""
    try:
        if not text:
            return ""
        
        lines = text.split('\n')
        formatted_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Sayfa numaralarını atla
            if re.match(r'^\d+$', line) or re.match(r'^sayfa\s+\d+', line.lower()):
                continue
            
            # Ana başlıklar (büyük harf, 10+ karakter)
            if line.isupper() and len(line) > 10 and not re.match(r'^\d+', line):
                formatted_lines.append(f"\n## {line.title()}\n")
            
            # Madde başlıkları
            elif re.match(r'^MADDE\s+\d+', line, re.IGNORECASE):
                formatted_lines.append(f"\n### {line.title()}\n")
            
            # Bölüm başlıkları
            elif re.match(r'^BÖLÜM\s+[IVX\d]+', line, re.IGNORECASE):
                formatted_lines.append(f"\n## {line.title()}\n")
            
            # Alt başlıklar (numaralı)
            elif re.match(r'^\d+\.\s+[A-ZÜÇĞIİÖŞ]', line):
                formatted_lines.append(f"\n**{line}**\n")
            
            # Normal paragraflar
            else:
                if len(line) > 50:
                    formatted_lines.append(f"{line}\n")
                else:
                    formatted_lines.append(f"**{line}**\n")
        
        return '\n'.join(formatted_lines)
        
    except Exception:
        return text


def _analyze_and_prepare_headless(pdf_path: str, pdf_base_name: str, api_key: Optional[str], use_ocr: bool = False) -> Dict[str, Any]:
    """Streamlit'e bağlı olmadan analiz ve metadata üretimini yapar.
    
    Args:
        pdf_path: PDF dosya yolu
        pdf_base_name: PDF dosya adı (base)
        api_key: DeepSeek API anahtarı (zorunlu - bölümleme için gerekli)
        use_ocr: OCR kullanımı (True: zorunlu OCR, False: OCR kullanma, varsayılan: False)
    """
    print("=" * 80)
    print("🔍 [AŞAMA 0.1] PDF ANALİZİ BAŞLATILIYOR")
    print("=" * 80)
    
    # DeepSeek API anahtarı zorunlu
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="DeepSeek API anahtarı bulunamadı. Bölümleme için DeepSeek API anahtarı zorunludur."
        )
    print("✅ [AŞAMA 0.1] DeepSeek API anahtarı bulundu")
    
    processor = PDFProcessor()
    
    # OCR kullanımı kontrolü
    if use_ocr is True:
        print("📸 [AŞAMA 0.1] OCR kullanımı: Aktif (kullanıcı tarafından belirlendi)")
        # OCR kullanılacaksa önce kontrol et
        if not processor._check_ocr_available():
            raise HTTPException(
                status_code=500,
                detail="OCR kullanımı isteniyor ancak RapidOCR kurulu değil. Lütfen 'pip install rapidocr-onnxruntime' komutunu çalıştırın."
            )
        # use_ocr=True ise sadece total_pages için minimal analiz yap (metin kontrolü yapma)
        pdf_structure = processor.analyze_pdf_structure(pdf_path, skip_text_analysis=True)
        total_pages = pdf_structure['total_pages']
        print(f"   📄 Toplam sayfa: {total_pages}")
        print(f"   📸 Tüm {total_pages} sayfa OCR ile işlenecek")
    else:
        print("📄 [AŞAMA 0.1] OCR kullanımı: Pasif (normal metin çıkarma)")
        # OCR kullanılmayacak, normal analiz yap
        pdf_structure = processor.analyze_pdf_structure(pdf_path)
        total_pages = pdf_structure['total_pages']
        print(f"   📄 Toplam sayfa: {total_pages}")
    
    print("=" * 80)
    print("🔍 [AŞAMA 0.2] PDF BÖLÜMLEME (DeepSeek API ile)")
    print("=" * 80)
    
    # Her zaman DeepSeek API ile bölümleme yap
    analyzer = DeepSeekAnalyzer(api_key)
    print("✅ [AŞAMA 0.2] DeepSeek Analyzer oluşturuldu")
    
    try:
        print("   🔄 Intelligent sections oluşturuluyor...")
        sections = processor.create_intelligent_sections(pdf_path, total_pages, analyzer, use_ocr=use_ocr)
        if use_ocr:
            cache_size = processor.get_ocr_cache_size()
            print(f"   💾 OCR cache: {cache_size} sayfa önbelleğe alındı")
        print(f"✅ [AŞAMA 0.2] {len(sections)} bölüm oluşturuldu (DeepSeek API ile)")
    except Exception as e:
        print(f"❌ [AŞAMA 0.2] Intelligent sections hatası: {str(e)}")
        import traceback
        print(f"   📋 Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"DeepSeek API ile bölümleme başarısız: {str(e)}"
        )

    print("=" * 80)
    print("🔍 [AŞAMA 0.3] METADATA ÜRETİMİ (DeepSeek API ile)")
    print("=" * 80)

    metadata_list: List[Dict[str, Any]] = []
    
    if use_ocr:
        cache_size = processor.get_ocr_cache_size()
        print(f"📸 OCR modu aktif: Metin çıkarma cache'den yapılacak ({cache_size} sayfa önbellekte)")
    
    for i, section in enumerate(sections):
        print(f"   📎 [{i+1}/{len(sections)}] Bölüm metadata üretiliyor...")
        print(f"      📄 Sayfa aralığı: {section['start_page']}-{section['end_page']}")
        
        section_text = processor.extract_text_from_pages(pdf_path, section['start_page'], section['end_page'], use_ocr=use_ocr)
        
        if section_text.strip():
            print(f"      📝 Metin çıkarıldı: {len(section_text)} karakter")
            print(f"      🤖 DeepSeek API ile analiz yapılıyor...")
            try:
                analysis = analyzer.analyze_section_content(section_text)
                title = analysis.get('title', f'Bölüm {i + 1}')
                description = analysis.get('description', 'Bu bölüm için açıklama oluşturulamadı.')
                keywords = analysis.get('keywords', f'bölüm {i + 1}')
                print(f"      ✅ Metadata üretildi: {title}")
            except Exception as e:
                print(f"      ⚠️ DeepSeek API analiz hatası: {str(e)}")
                title = f"Bölüm {i + 1}"
                description = "Bu bölüm için otomatik açıklama oluşturulamadı."
                keywords = f"bölüm {i + 1}"
        else:
            print(f"      ⚠️ Bölümde metin bulunamadı")
            title = f"Bölüm {i + 1}"
            description = "Bu bölüm için otomatik açıklama oluşturulamadı."
            keywords = f"bölüm {i + 1}"

        output_filename = create_pdf_filename(pdf_base_name, i + 1, section['start_page'], section['end_page'], title)
        metadata_list.append({
            "output_filename": output_filename,
            "start_page": section['start_page'],
            "end_page": section['end_page'],
            "title": title,
            "description": description,
            "keywords": keywords
        })
        print(f"      ✅ Bölüm {i+1} tamamlandı")

    print(f"✅ [AŞAMA 0.3] {len(metadata_list)} bölüm için metadata üretildi")
    print("=" * 80)

    return {"sections": sections, "metadata_list": metadata_list, "total_pages": total_pages}


def _split_pdfs(pdf_path: str, sections: List[Dict[str, int]], metadata_list: List[Dict[str, Any]]) -> str:
    """PDF'leri bölümlere ayırır ve chunk'lar oluşturur"""
    print(f"   📂 PDF dosyası: {pdf_path}")
    print(f"   📊 Toplam bölüm: {len(sections)}")
    
    output_dir = create_output_directories()
    print(f"   📁 Output dizini oluşturuldu: {output_dir}")
    
    from pypdf import PdfReader, PdfWriter
    with open(pdf_path, 'rb') as source:
        reader = PdfReader(source)
        total_pages = len(reader.pages)
        print(f"   📄 Kaynak PDF sayfa sayısı: {total_pages}")
        
        for i, (section, metadata) in enumerate(zip(sections, metadata_list), 1):
            start_page = section['start_page']
            end_page = section['end_page']
            output_filename = metadata.get('output_filename', f'section_{i}.pdf')
            
            print(f"   📎 [{i}/{len(sections)}] Bölüm işleniyor: {output_filename}")
            print(f"      📄 Sayfa aralığı: {start_page}-{end_page}")
            
            writer = PdfWriter()
            pages_added = 0
            for page_num in range(start_page - 1, end_page):
                if page_num < len(reader.pages):
                    writer.add_page(reader.pages[page_num])
                    pages_added += 1
            
            print(f"      ✅ {pages_added} sayfa eklendi")
            
            out_path = Path(output_dir) / output_filename
            try:
                with open(out_path, 'wb') as f:
                    writer.write(f)
                    file_size = out_path.stat().st_size
                    print(f"      💾 Dosya kaydedildi: {file_size:,} bytes")
            except Exception as e:
                print(f"      ❌ Dosya kaydetme hatası: {str(e)}")
                raise
    
    # JSON metadata dosyası da kaydedilsin
    json_path = Path(output_dir) / "pdf_sections_metadata.json"
    print(f"   📋 Metadata JSON dosyası kaydediliyor: {json_path}")
    try:
        with open(json_path, 'w', encoding='utf-8') as jf:
            json.dump({"pdf_sections": metadata_list}, jf, ensure_ascii=False, indent=2)
            json_size = json_path.stat().st_size
            print(f"   ✅ Metadata JSON kaydedildi: {json_size:,} bytes")
    except Exception as e:
        print(f"   ⚠️ Metadata JSON kaydetme hatası: {str(e)}")
    
    return output_dir


def _upload_bulk(cfg: Dict[str, Any], token: str, output_dir: str, category: str, institution: str, belge_adi: str, metadata_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """MevzuatGPT'ye bulk upload yapar"""
    try:
        print(f"🔧 [MevzuatGPT Upload] Başlatılıyor...")
        print(f"   📂 Output dizini: {output_dir}")
        print(f"   📋 Kategori: {category}")
        print(f"   🏢 Kurum: {institution}")
        print(f"   📄 Belge: {belge_adi}")
        print(f"   📊 Metadata sayısı: {len(metadata_list)}")
        
        api_base_url = cfg.get("api_base_url")
        if not api_base_url:
            print("❌ [MevzuatGPT Upload] API base URL bulunamadı!")
            return None
        
        upload_url = f"{api_base_url.rstrip('/')}/api/admin/documents/bulk-upload"
        print(f"🌐 [MevzuatGPT Upload] Upload URL: {upload_url}")
        
        # PDF dosyalarını bul
        print(f"📁 [MevzuatGPT Upload] PDF dosyaları aranıyor: {output_dir}")
        pdf_files = list(sorted(Path(output_dir).glob('*.pdf')))
        print(f"   📄 Bulunan PDF sayısı: {len(pdf_files)}")
        
        if len(pdf_files) == 0:
            print("❌ [MevzuatGPT Upload] Yüklenecek PDF dosyası bulunamadı!")
            return None
        
        # PDF dosyalarını oku ve içeriklerini al
        files_content = []
        for i, pdf_file in enumerate(pdf_files, 1):
            print(f"   📎 [{i}/{len(pdf_files)}] PDF dosyası hazırlanıyor: {pdf_file.name}")
            try:
                with open(pdf_file, 'rb') as f:
                    file_content = f.read()
                    file_size = len(file_content)
                    files_content.append((pdf_file.name, file_content, 'application/pdf'))
                    print(f"      ✅ Dosya okundu: {file_size:,} bytes")
            except Exception as e:
                print(f"   ⚠️ [{i}/{len(pdf_files)}] PDF dosyası açılamadı: {pdf_file.name} - {str(e)}")
        
        if len(files_content) == 0:
            print("❌ [MevzuatGPT Upload] Yüklenecek PDF dosyası bulunamadı!")
            return None
        
        print(f"✅ [MevzuatGPT Upload] {len(files_content)} PDF dosyası hazırlandı")
        
        # Metadata hazırla
        print(f"📋 [MevzuatGPT Upload] Metadata hazırlanıyor...")
        metadata_json = json.dumps({"pdf_sections": [
                {
                    "output_filename": m.get("output_filename", ""),
                    "title": m.get("title", ""),
                    "description": m.get("description", ""),
                    "keywords": m.get("keywords", "")
                } for m in metadata_list
            ]}, ensure_ascii=False)
        print(f"   📊 Metadata JSON uzunluğu: {len(metadata_json)} karakter")
        
        headers = {'Authorization': f'Bearer {token}'}
        print(f"🚀 [MevzuatGPT Upload] API'ye istek gönderiliyor...")
        print(f"   ⏱️ Timeout: 1200 saniye (20 dakika)")
        
        # curl_cffi için CurlMime kullan
        if CURL_CFFI_AVAILABLE:
            print(f"   📦 CurlMime formatı kullanılıyor (curl_cffi)")
            multipart = CurlMime()
            
            # Her PDF dosyasını ekle (aynı field name 'files' ile)
            for filename, content, content_type in files_content:
                multipart.addpart(name='files', filename=filename, data=content, mimetype=content_type)
                print(f"      ✅ Dosya eklendi: {filename}")
            
            # Form verilerini ekle
            multipart.addpart(name='category', data=category)
            multipart.addpart(name='institution', data=institution)
            multipart.addpart(name='belge_adi', data=belge_adi)
            multipart.addpart(name='metadata', data=metadata_json)
            
            print(f"   📋 Form verileri eklendi: category, institution, belge_adi, metadata")
            resp = requests.post(upload_url, headers=headers, multipart=multipart, timeout=1200)
        else:
            # Standart requests kütüphanesi için
            form_data = {
                'category': category,
                'institution': institution,
                'belge_adi': belge_adi,
                'metadata': metadata_json
            }
            files_to_upload = [('files', (name, content, content_type)) for name, content, content_type in files_content]
            print(f"   📦 Standart requests formatı kullanılıyor")
            resp = requests.post(upload_url, headers=headers, data=form_data, files=files_to_upload, timeout=1200)
        
        print(f"📡 [MevzuatGPT Upload] API yanıtı alındı")
        print(f"   📊 Status Code: {resp.status_code}")
        print(f"   📝 Response uzunluğu: {len(resp.text)} karakter")
        print(f"   📋 Response headers: {dict(resp.headers)}")
        
        if resp.status_code == 200:
            try:
                response_data = resp.json()
                print(f"✅ [MevzuatGPT Upload] Başarılı!")
                print(f"   📦 Response type: {type(response_data)}")
                if isinstance(response_data, dict):
                    print(f"   📊 Response keys: {list(response_data.keys())}")
                    # Önemli alanları göster
                    if "success" in response_data:
                        print(f"   ✅ Success: {response_data.get('success')}")
                    if "message" in response_data:
                        print(f"   💬 Message: {response_data.get('message')}")
                    if "data" in response_data:
                        data = response_data.get('data')
                        if isinstance(data, dict):
                            print(f"   📊 Data keys: {list(data.keys())}")
                        elif isinstance(data, list):
                            print(f"   📊 Data list uzunluğu: {len(data)}")
                    if "inserted_count" in response_data:
                        print(f"   📈 Inserted count: {response_data.get('inserted_count')}")
                    if "chunks" in response_data:
                        chunks = response_data.get('chunks')
                        if isinstance(chunks, list):
                            print(f"   📦 Chunks sayısı: {len(chunks)}")
                            if len(chunks) > 0:
                                print(f"   📋 İlk chunk örneği: {json.dumps(chunks[0], ensure_ascii=False)[:200]}...")
                
                # Full response'u göster (kısaltılmış)
                response_str = json.dumps(response_data, ensure_ascii=False, indent=2)
                print(f"   📄 Full response (ilk 2000 karakter):")
                print(f"      {response_str[:2000]}")
                if len(response_str) > 2000:
                    print(f"      ... (toplam {len(response_str)} karakter)")
                
                return response_data
            except json.JSONDecodeError as e:
                print(f"⚠️ [MevzuatGPT Upload] JSON parse hatası: {str(e)}")
                print(f"   📝 Raw response: {resp.text[:1000]}")
                return {"status_code": 200, "text": resp.text, "parse_error": str(e)}
        else:
            print(f"❌ [MevzuatGPT Upload] Başarısız!")
            print(f"   📊 Status Code: {resp.status_code}")
            print(f"   📝 Response headers: {dict(resp.headers)}")
            print(f"   📝 Response body (ilk 2000 karakter):")
            print(f"      {resp.text[:2000]}")
            if len(resp.text) > 2000:
                print(f"      ... (toplam {len(resp.text)} karakter)")
        return {"status_code": resp.status_code, "text": resp.text}
            
    except requests.exceptions.Timeout as e:
        print(f"❌ [MevzuatGPT Upload] Timeout hatası: {str(e)}")
        return {"error": f"Timeout: {str(e)}"}
    except requests.exceptions.RequestException as e:
        print(f"❌ [MevzuatGPT Upload] Request hatası: {str(e)}")
        return {"error": f"Request error: {str(e)}"}
    except Exception as e:
        print(f"❌ [MevzuatGPT Upload] Beklenmeyen hata: {str(e)}")
        import traceback
        print(f"   📋 Traceback: {traceback.format_exc()}")
        return {"error": str(e)}


@app.post("/api/kurum/process", response_model=ProcessResponse, tags=["SGK Scraper"], summary="Link ile PDF indir, analiz et ve yükle")
async def process_item(req: ProcessRequest):
    try:
        # Type kontrolü
        if req.type.lower() != "kaysis":
            raise HTTPException(
                status_code=400,
                detail=f"Desteklenmeyen scraper tipi: {req.type}. Şu an için sadece 'kaysis' desteklenmektedir."
            )
        
        # Mode kontrolü
        mode = req.mode.lower() if req.mode else "t"
        if mode not in ["m", "p", "t"]:
            raise HTTPException(status_code=400, detail="Geçersiz mode. 'm', 'p' veya 't' olmalı.")
        
        print(f"🔧 İşlem modu: {mode.upper()} ({'MevzuatGPT' if mode == 'm' else 'Portal' if mode == 'p' else 'Tamamı'})")
        print(f"📋 Scraper tipi: {req.type}")
        
        # MongoDB'den kurum bilgisini çek
        kurum_adi = None
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                db = client[database_name]
                kurumlar_collection = db["kurumlar"]
                from bson import ObjectId
                kurum_doc = kurumlar_collection.find_one({"_id": ObjectId(req.kurum_id)})
                if kurum_doc:
                    kurum_adi = kurum_doc.get("kurum_adi", "Bilinmeyen Kurum")
                client.close()
        except Exception as e:
            print(f"⚠️ MongoDB'den kurum bilgisi alınamadı: {str(e)}")
            kurum_adi = "Bilinmeyen Kurum"
        
        print(f"📋 Kurum: {kurum_adi}")
        print(f"🔢 DETSIS: {req.detsis}")
        
        # Link ve diğer bilgileri request'ten al
        pdf_url = req.link
        if not pdf_url:
            raise HTTPException(status_code=400, detail="Link parametresi zorunludur.")
        
        # Category ve document_name request'ten al veya varsayılan değerler kullan
        category = req.category if req.category else "Genel"
        document_name = req.document_name if req.document_name else "Belge"
        institution = kurum_adi  # Kurum adını kullan
        
        print(f"🔗 PDF Link: {pdf_url}")
        print(f"📄 Belge Adı: {document_name}")
        print(f"📂 Kategori: {category}")

        # Belge adı kontrolü (PDF indirmeden önce)
        print("=" * 80)
        print("🔍 BELGE ADI KONTROLÜ (PDF indirmeden önce)")
        print("=" * 80)
        exists_in_mevzuatgpt, exists_in_portal, error_msg = _check_document_name_exists(document_name, mode)
        
        # Mode'a göre kontrol ve dinamik mode ayarlama
        if mode == "t":  # "Hepsini yükle" modu
            if exists_in_mevzuatgpt and exists_in_portal:
                # Her ikisinde de varsa -> Hata ver
                print(f"❌ Belge adı kontrolü başarısız: {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg or "Bu belge adı her iki yerde de zaten mevcut.")
            elif exists_in_mevzuatgpt and not exists_in_portal:
                # Sadece MevzuatGPT'de varsa -> Sadece Portal'a yükle
                print(f"ℹ️ Belge MevzuatGPT'de zaten yüklü, sadece Portal'a yüklenecek.")
                mode = "p"
            elif exists_in_portal and not exists_in_mevzuatgpt:
                # Sadece Portal'da varsa -> Sadece MevzuatGPT'ye yükle
                print(f"ℹ️ Belge Portal'da zaten yüklü, sadece MevzuatGPT'ye yüklenecek.")
                mode = "m"
            else:
                # Hiçbirinde yoksa -> Her ikisine de yükle (mode 't' kalır)
                print(f"✅ Belge her iki yerde de yok, her ikisine de yüklenecek.")
        else:
            # 'm' veya 'p' modu için sadece ilgili kontrolü yap
            if mode == "m" and exists_in_mevzuatgpt:
                print(f"❌ Belge adı kontrolü başarısız: Bu belge adı MevzuatGPT'de zaten mevcut.")
                raise HTTPException(status_code=400, detail="Bu belge adı MevzuatGPT'de zaten mevcut.")
            elif mode == "p" and exists_in_portal:
                print(f"❌ Belge adı kontrolü başarısız: Bu belge adı Portal'da zaten mevcut.")
                raise HTTPException(status_code=400, detail="Bu belge adı Portal'da zaten mevcut.")
        
        print(f"✅ Belge adı kontrolü tamamlandı - İşlem modu: {mode.upper()}")
        print("📥 PDF indirme işlemine geçiliyor...")

        # PDF'i indir
        print("=" * 80)
        print("📥 PDF İNDİRME")
        print("=" * 80)
        print("📥 PDF indiriliyor...")
        pdf_path = await download_pdf_from_url(pdf_url)
        if not validate_pdf_file(pdf_path):
            raise HTTPException(status_code=500, detail="İndirilen dosya geçerli bir PDF değil.")
        print("✅ PDF indirme başarılı")

        # Analiz ve metadata (tüm modlar için: MevzuatGPT, Portal ve Tamamı)
        print("=" * 80)
        print("🔍 [AŞAMA 0] PDF ANALİZİ")
        print("=" * 80)
        print(f"   📄 PDF dosyası: {pdf_path}")
        
        api_key = _get_deepseek_api_key()
        if not api_key:
            print("   ⚠️ [AŞAMA 0] DeepSeek API anahtarı bulunamadı, manuel bölümleme ve basit metadata kullanılacak.")
        else:
            print(f"   ✅ [AŞAMA 0] DeepSeek API anahtarı bulundu")
        
        pdf_base_name = "document"
        # Kullanıcının OCR tercihini al (tüm modlar için geçerli: m, p, t)
        use_ocr = req.use_ocr if hasattr(req, 'use_ocr') else False
        print(f"   📸 OCR kullanımı: {'Aktif (tüm sayfalar OCR ile işlenecek)' if use_ocr else 'Pasif (normal metin çıkarma)'}")
        
        print(f"   🔄 Analiz başlatılıyor...")
        try:
            analysis_result = _analyze_and_prepare_headless(pdf_path, pdf_base_name, api_key, use_ocr=use_ocr)
            sections = analysis_result['sections']
            metadata_list = analysis_result['metadata_list']
            total_pages = analysis_result.get('total_pages', 0)
            
            print(f"✅ [AŞAMA 0] PDF analiz başarılı")
            print(f"   📊 Toplam sayfa: {total_pages}")
            print(f"   📋 Bölüm sayısı: {len(sections)}")
            print(f"   📝 Metadata sayısı: {len(metadata_list)}")
            
            # Bölüm özeti
            for i, section in enumerate(sections[:5], 1):  # İlk 5 bölümü göster
                print(f"      [{i}] Sayfa {section.get('start_page', '?')}-{section.get('end_page', '?')}")
            if len(sections) > 5:
                print(f"      ... ve {len(sections) - 5} bölüm daha")
                
        except Exception as e:
            print(f"❌ [AŞAMA 0] PDF analiz hatası: {str(e)}")
            import traceback
            print(f"   📋 Traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"PDF analiz hatası: {str(e)}")

        # PDF'leri böl ve çıktıyı oluştur (sadece 'm' ve 't' modları için)
        output_dir = None
        if mode in ["m", "t"]:
            print("=" * 80)
            print("📄 [AŞAMA 1] PDF BÖLÜMLEME")
            print("=" * 80)
            print(f"   📊 Bölüm sayısı: {len(sections)}")
            print(f"   📋 Metadata sayısı: {len(metadata_list)}")
            try:
                output_dir = _split_pdfs(pdf_path, sections, metadata_list)
                print(f"✅ [AŞAMA 1] PDF bölümleme başarılı")
                print(f"   📂 Output dizini: {output_dir}")
                
                # Oluşturulan dosyaları kontrol et
                pdf_files = list(Path(output_dir).glob('*.pdf'))
                print(f"   📄 Oluşturulan PDF sayısı: {len(pdf_files)}")
                for pdf_file in pdf_files:
                    file_size = pdf_file.stat().st_size
                    print(f"      - {pdf_file.name} ({file_size:,} bytes)")
            except Exception as e:
                print(f"❌ [AŞAMA 1] PDF bölümleme hatası: {str(e)}")
                import traceback
                print(f"   📋 Traceback: {traceback.format_exc()}")
                raise HTTPException(status_code=500, detail=f"PDF bölümleme hatası: {str(e)}")
        else:
            print("⏭️ PDF bölümleme atlandı (Portal modu)")

        # MevzuatGPT'ye yükleme (sadece 'm' ve 't' modları için)
        upload_resp = None
        if mode in ["m", "t"]:
            print("=" * 80)
            print("📤 [AŞAMA 2] MEVZUATGPT'YE YÜKLEME")
            print("=" * 80)
            
            # Config kontrolü
            print("🔧 [AŞAMA 2.1] Config yükleniyor...")
            cfg = _load_config()
            if not cfg:
                print("❌ [AŞAMA 2.1] Config bulunamadı!")
                raise HTTPException(status_code=500, detail="Config dosyası bulunamadı")
            print(f"✅ [AŞAMA 2.1] Config yüklendi")
            print(f"   🌐 API Base URL: {cfg.get('api_base_url', 'N/A')}")
            
            # Login kontrolü
            print("🔐 [AŞAMA 2.2] MevzuatGPT'ye login yapılıyor...")
            token = _login_with_config(cfg)
            if not token:
                print("❌ [AŞAMA 2.2] Login başarısız!")
                raise HTTPException(status_code=500, detail="MevzuatGPT login başarısız")
            print(f"✅ [AŞAMA 2.2] Login başarılı")
            print(f"   🔑 Token uzunluğu: {len(token)} karakter")
            
            # Upload işlemi
            print("📤 [AŞAMA 2.3] Bulk upload başlatılıyor...")
            if not output_dir:
                print("❌ [AŞAMA 2.3] Output dizini bulunamadı!")
                raise HTTPException(status_code=500, detail="Output dizini bulunamadı")
            
            upload_resp = _upload_bulk(cfg, token, output_dir, category, institution, document_name, metadata_list)
            
            if upload_resp:
                # Response kontrolü
                if "error" in upload_resp:
                    print(f"❌ [AŞAMA 2.3] Upload hatası: {upload_resp.get('error')}")
                    raise HTTPException(status_code=500, detail=f"Upload hatası: {upload_resp.get('error')}")
                elif upload_resp.get("status_code") and upload_resp.get("status_code") != 200:
                    print(f"❌ [AŞAMA 2.3] Upload başarısız: HTTP {upload_resp.get('status_code')}")
                    print(f"   📝 Response: {upload_resp.get('text', '')[:500]}")
                    raise HTTPException(status_code=500, detail=f"Upload başarısız: HTTP {upload_resp.get('status_code')}")
                else:
                    print(f"✅ [AŞAMA 2.3] Upload başarılı!")
                    print(f"   📦 Response keys: {list(upload_resp.keys()) if isinstance(upload_resp, dict) else 'N/A'}")
                    if isinstance(upload_resp, dict):
                        response_str = json.dumps(upload_resp, ensure_ascii=False, indent=2)
                        print(f"   📊 Response detayları (ilk 1000 karakter):")
                        print(f"      {response_str[:1000]}")
                        if len(response_str) > 1000:
                            print(f"      ... (toplam {len(response_str)} karakter)")
            else:
                print("❌ [AŞAMA 2.3] Upload response None döndü!")
                raise HTTPException(status_code=500, detail="Upload response None")
        else:
            print("⏭️ MevzuatGPT yükleme atlandı (Portal modu)")

        # Portal'a yükleme (sadece 'p' ve 't' modları için)
        mongodb_metadata_id = None
        if mode in ["p", "t"]:
            print("=" * 80)
            print("📦 [AŞAMA 3] PORTAL'A YÜKLEME")
            print("=" * 80)
            try:
                # PDF bilgilerini al
                print("📊 [AŞAMA 3.1] PDF bilgileri alınıyor...")
                processor = PDFProcessor()
                pdf_info = processor.analyze_pdf_structure(pdf_path)
                total_pages = pdf_info.get('total_pages', 0)
                
                # PDF dosya boyutu (MB)
                pdf_size_bytes = os.path.getsize(pdf_path)
                pdf_size_mb = round(pdf_size_bytes / (1024 * 1024), 2)
                print(f"   ✅ PDF bilgileri alındı")
                print(f"      📄 Toplam sayfa: {total_pages}")
                print(f"      💾 Dosya boyutu: {pdf_size_bytes:,} bytes ({pdf_size_mb} MB)")
                
                # Keywords ve description'ları topla
                print("📋 [AŞAMA 3.2] Keywords ve descriptions toplanıyor...")
                all_keywords = []
                all_descriptions = []
                
                # Mode'a göre metadata kaynağını belirle
                if mode == "t" and output_dir:
                    # 't' modunda pdf_sections_metadata.json'dan al
                    print("   📂 Metadata kaynağı: pdf_sections_metadata.json")
                    metadata_json_path = Path(output_dir) / "pdf_sections_metadata.json"
                    if metadata_json_path.exists():
                        try:
                            print(f"   📄 JSON dosyası okunuyor: {metadata_json_path}")
                            with open(metadata_json_path, 'r', encoding='utf-8') as f:
                                metadata_json = json.load(f)
                                pdf_sections = metadata_json.get('pdf_sections', [])
                                print(f"   📊 Bölüm sayısı: {len(pdf_sections)}")
                                for i, section in enumerate(pdf_sections, 1):
                                    keywords = section.get('keywords', '')
                                    description = section.get('description', '')
                                    if keywords:
                                        # Keywords string ise virgülle ayrılmış olabilir
                                        if isinstance(keywords, str):
                                            keywords_list = [k.strip() for k in keywords.split(',') if k.strip()]
                                            all_keywords.extend(keywords_list)
                                        elif isinstance(keywords, list):
                                            all_keywords.extend(keywords)
                                    if description:
                                        all_descriptions.append(description.strip())
                            print(f"   ✅ JSON'dan {len(pdf_sections)} bölüm işlendi")
                        except Exception as e:
                            print(f"   ⚠️ Metadata JSON okuma hatası: {str(e)}")
                    else:
                        print(f"   ⚠️ JSON dosyası bulunamadı: {metadata_json_path}")
                else:
                    # 'p' modunda veya json yoksa analiz sonuçlarından al
                    print("   📂 Metadata kaynağı: Analiz sonuçları")
                    print(f"   📊 Metadata list uzunluğu: {len(metadata_list)}")
                    for i, section_meta in enumerate(metadata_list, 1):
                        keywords = section_meta.get('keywords', '')
                        description = section_meta.get('description', '')
                        if keywords:
                            if isinstance(keywords, str):
                                keywords_list = [k.strip() for k in keywords.split(',') if k.strip()]
                                all_keywords.extend(keywords_list)
                            elif isinstance(keywords, list):
                                all_keywords.extend(keywords)
                        if description:
                            all_descriptions.append(description.strip())
                    print(f"   ✅ {len(metadata_list)} bölüm işlendi")
                
                # Keywords ve descriptions birleştir
                combined_keywords = ', '.join(all_keywords) if all_keywords else ''
                combined_description = ' '.join(all_descriptions) if all_descriptions else ''
                
                print(f"   📊 Toplanan keywords sayısı: {len(all_keywords)}")
                print(f"   📊 Toplanan descriptions sayısı: {len(all_descriptions)}")
                print(f"   📝 Combined keywords uzunluğu: {len(combined_keywords)} karakter")
                print(f"   📝 Combined description uzunluğu: {len(combined_description)} karakter")
                
                # Açıklama karakter sınırı (max 500 karakter)
                if len(combined_description) > 500:
                    combined_description = combined_description[:497] + "..."
                    print(f"   ⚠️ Description 500 karaktere kısaltıldı")
                
                # Ana PDF'yi bunny.net'e yükle
                print("📤 [AŞAMA 3.3] Ana PDF Bunny.net'e yükleniyor...")
                # Dosya adını güvenli hale getir (Türkçe karakterleri İngilizce'ye çevir, kaldırma)
                transliterated_name = _transliterate_turkish(document_name)
                print(f"   📝 Orijinal ad: {document_name}")
                print(f"   📝 Transliterated ad: {transliterated_name}")
                # Sadece harfler, rakamlar, boşluk ve tireleri koru, diğer karakterleri kaldır
                safe_pdf_adi = re.sub(r'[^a-zA-Z0-9\s-]', '', transliterated_name).strip()
                # Boşlukları alt çizgi ile değiştir
                safe_pdf_adi = re.sub(r'\s+', '_', safe_pdf_adi)
                # Çoklu alt çizgileri tek alt çizgi yap
                safe_pdf_adi = re.sub(r'_+', '_', safe_pdf_adi)
                bunny_filename = f"{safe_pdf_adi}_{ObjectId()}.pdf"
                print(f"   📝 Güvenli dosya adı: {bunny_filename}")
                
                pdf_url = _upload_to_bunny(pdf_path, bunny_filename)
                
                if pdf_url:
                    print(f"✅ [AŞAMA 3.3] Ana PDF Bunny.net'e yüklendi")
                    print(f"   🔗 PDF URL: {pdf_url}")
                else:
                    print("⚠️ [AŞAMA 3.3] Bunny.net yükleme başarısız, MongoDB işlemi devam ediyor...")
                
                # pdf_adi: tekrar başlık metni olarak kaydedilecek
                pdf_adi = document_name
                
                # Slug oluştur (alt tire ile, sınırsız)
                print("🔗 [AŞAMA 3.4] URL slug oluşturuluyor...")
                url_slug = _create_url_slug(document_name)
                print(f"   ✅ URL slug: {url_slug}")
                
                # Yükleme tarihi
                now = datetime.now()
                upload_date_str = now.strftime('%Y-%m-%d')
                upload_datetime_str = now.isoformat()
                print(f"   📅 Yükleme tarihi: {upload_datetime_str}")
                
                # PDF'den markdown formatında metin çıkar
                print("📝 [AŞAMA 3.5] PDF içeriği markdown formatına çevriliyor...")
                markdown_content = _extract_pdf_text_markdown(pdf_path)
                if not markdown_content:
                    markdown_content = "PDF içeriği çıkarılamadı."
                    print("   ⚠️ PDF içeriği çıkarılamadı, varsayılan mesaj kullanılıyor")
                else:
                    content_length = len(markdown_content)
                    content_length_kb = round(content_length / 1024, 2)
                    print(f"   ✅ Markdown içerik oluşturuldu: {content_length:,} karakter ({content_length_kb} KB)")
                
                # Metadata oluştur
                print("💾 [AŞAMA 3.6] MongoDB metadata hazırlanıyor...")
                mongodb_metadata = {
                    "pdf_adi": pdf_adi,
                    "kurum_id": req.kurum_id,  # Request'ten gelen kurum ID'sini kullan
                    "belge_turu": category,
                    "belge_durumu": "Yürürlükte",
                    "belge_yayin_tarihi": upload_date_str,
                    "yururluluk_tarihi": upload_date_str,
                    "etiketler": "KAYSİS",
                    "anahtar_kelimeler": combined_keywords,
                    "aciklama": combined_description,
                    "url_slug": url_slug,
                    "status": "aktif",
                    "sayfa_sayisi": total_pages,
                    "dosya_boyutu_mb": pdf_size_mb,
                    "yukleme_tarihi": upload_datetime_str,
                    "pdf_url": pdf_url or ""
                }
                print(f"   ✅ Metadata hazırlandı ({len(mongodb_metadata)} alan)")
                
                # MongoDB'ye kaydet
                print("💾 [AŞAMA 3.7] MongoDB'ye kaydediliyor...")
                mongodb_metadata_id = _save_to_mongodb(mongodb_metadata, markdown_content)
                
                if mongodb_metadata_id:
                    print(f"✅ [AŞAMA 3.7] MongoDB kaydı başarılı: metadata_id={mongodb_metadata_id}")
                else:
                    print("❌ [AŞAMA 3.7] MongoDB kaydı başarısız")
                    
            except Exception as e:
                print(f"⚠️ MongoDB/Bunny.net işlemleri sırasında hata: {str(e)}")
                # Hata olsa bile ana işlemi tamamla
        
        # Tüm işlemler başarılı olduktan sonra pdf_output klasörünü temizle
        try:
            print("🧹 pdf_output klasörü temizleniyor...")
            pdf_output_dir = Path("pdf_output")
            if pdf_output_dir.exists():
                # Klasördeki tüm içeriği temizle (klasörleri de dahil)
                for item in pdf_output_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                print("✅ pdf_output klasörü temizlendi")
        except Exception as e:
            print(f"⚠️ pdf_output temizleme hatası: {str(e)}")

        # Response mesajını mode'a göre özelleştir
        mode_messages = {
            "m": "MevzuatGPT'ye yükleme tamamlandı",
            "p": "Portal'a yükleme tamamlandı",
            "t": "Tüm işlemler tamamlandı (MevzuatGPT + Portal)"
        }
        message = mode_messages.get(mode, "İşlem tamamlandı")
        
        return ProcessResponse(
            success=True,
            message=message,
            data=ProcessData(
                category=category,
                institution=institution,
                document_name=document_name,
                output_dir=output_dir,
                sections_count=len(sections) if sections else 0,
                upload_response=upload_resp
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"İşlem sırasında hata oluştu: {str(e)}")


if __name__ == "__main__":
    print("🚀 FastAPI Server başlatılıyor...")
    print("📡 Server: http://0.0.0.0:8000")
    print("📚 API Docs: http://0.0.0.0:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)

