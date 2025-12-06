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
    get_uploaded_documents
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

# Auto scraper iş yönetimi
# { kurum_id: { "pending_items": List[Dict], "is_running": bool, "stop_requested": bool } }
auto_scraper_jobs: Dict[str, Dict[str, Any]] = {}


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


class AutoScraperAnalyzeRequest(BaseModel):
    kurum_id: str = Field(..., description="Kurum MongoDB ObjectId")
    detsis: Optional[str] = Field(default=None, description="DETSIS numarası (KAYSİS kurum ID'si, opsiyonel)")
    type: str = Field(default="kaysis", description="Scraper tipi (varsayılan: kaysis)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "kurum_id": "68bbf6df8ef4e8023c19641d",
                "detsis": "60521689",
                "type": "kaysis"
            }
        }
    }


class AutoScraperStartRequest(BaseModel):
    kurum_id: str = Field(..., description="Kurum MongoDB ObjectId")

    model_config = {
        "json_schema_extra": {
            "example": {
                "kurum_id": "68bbf6df8ef4e8023c19641d"
            }
        }
    }


class AutoScraperStopRequest(BaseModel):
    kurum_id: str = Field(..., description="Kurum MongoDB ObjectId")

    model_config = {
        "json_schema_extra": {
            "example": {
                "kurum_id": "68bbf6df8ef4e8023c19641d"
            }
        }
    }


@app.get("/", tags=["Health"], summary="API kök")
async def root():
    """API root endpoint"""
    return {
        "message": "SGK Scraper API",
        "version": "1.0.0",
        "endpoints": {
            "POST /api/mevzuatgpt/scrape": "Kurum mevzuatlarını tarar ve konsola yazdırır"
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
                    # Debug: İlk birkaç belge_adi'yi yazdır
                    if uploaded_docs:
                        sample_titles = [doc.get("belge_adi", "") for doc in uploaded_docs[:5]]
                        print(f"🔍 DEBUG - Örnek belge_adi'ler: {sample_titles}")
                except Exception as e:
                    print(f"⚠️ Documents çekme hatası: {str(e)}")

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


def _get_embeddings_count(api_base_url: str, access_token: str) -> Optional[int]:
    """Embeddings count endpoint'inden toplam chunk sayısını çeker"""
    try:
        url = f"{api_base_url.rstrip('/')}/api/admin/embeddings/count"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        response = requests.get(url, headers=headers, timeout=60)
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                result = data.get("data", {})
                return result.get("total_embeddings", 0)
        return None
    except Exception as e:
        print(f"⚠️ Embeddings count hatası: {str(e)}")
        return None


async def _wait_for_chunk_update(
    initial_count: int,
    api_base_url: str,
    access_token: str,
    max_checks: int = 15,
    check_interval: int = 20,
    belge_adi: str = ""
) -> Tuple[bool, int]:
    """
    Chunk sayısının güncellenmesini bekler.
    
    Args:
        initial_count: Başlangıç chunk sayısı
        api_base_url: API base URL
        access_token: Access token
        max_checks: Maksimum kontrol sayısı (default: 15)
        check_interval: Her kontrol arası bekleme süresi saniye (default: 20)
        belge_adi: Belge adı (log için)
    
    Returns:
        (success: bool, final_count: int) - success True ise chunk güncellenmiş, False ise güncellenmemiş
    """
    import asyncio
    
    print(f"\n🔍 Chunk güncellemesi bekleniyor (Başlangıç: {initial_count:,})...")
    print(f"   Belge: {belge_adi}")
    print(f"   Maksimum {max_checks} kontrol, her {check_interval} saniyede bir")
    
    for check_num in range(1, max_checks + 1):
        # Bekle
        if check_num > 1:  # İlk kontrolde bekleme
            print(f"   ⏳ {check_interval} saniye bekleniyor... (Kontrol {check_num}/{max_checks})")
            await asyncio.sleep(check_interval)
        
        # Chunk sayısını çek
        current_count = _get_embeddings_count(api_base_url, access_token)
        
        if current_count is None:
            print(f"   ⚠️ Chunk sayısı çekilemedi (Kontrol {check_num}/{max_checks})")
            continue
        
        print(f"   📊 Kontrol {check_num}/{max_checks}: Mevcut chunk sayısı: {current_count:,}")
        
        # Eğer sayı artmışsa, başarılı
        if current_count > initial_count:
            print(f"   ✅ Chunk sayısı güncellendi! ({initial_count:,} → {current_count:,})")
            return True, current_count
        
        # Eğer sayı aynıysa, bir sonraki kontrole geç
        if current_count == initial_count:
            print(f"   ⏸️ Chunk sayısı henüz değişmedi ({current_count:,})")
    
    # 15 kontrolde de değişmediyse, başarısız
    final_count = _get_embeddings_count(api_base_url, access_token) or initial_count
    print(f"   ❌ {max_checks} kontrolde chunk sayısı değişmedi! ({initial_count:,} → {final_count:,})")
    return False, final_count


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


def _send_telegram_message(message: str) -> bool:
    """Telegram bot API kullanarak mesaj gönderir"""
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not bot_token:
            print("⚠️ TELEGRAM_BOT_TOKEN environment variable bulunamadı")
            return False
        
        if not chat_id:
            print("⚠️ TELEGRAM_CHAT_ID environment variable bulunamadı")
            return False
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"✅ Telegram mesajı gönderildi")
            return True
        else:
            print(f"⚠️ Telegram mesaj gönderme hatası: HTTP {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"⚠️ Telegram mesaj gönderme hatası: {str(e)}")
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
                    print("⚠️ OCR kütüphaneleri kurulu değil veya Poppler/Tesseract eksik")
                    print("⚠️ Kurulum için: 'apt-get install poppler-utils tesseract-ocr tesseract-ocr-tur' (Linux)")
                    print("⚠️ veya: 'brew install poppler tesseract tesseract-lang' (macOS)")
            except Exception as ocr_error:
                error_msg = str(ocr_error)
                print(f"❌ OCR hatası: {error_msg}")
                if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                    print("❌ Poppler kurulu değil! 'apt-get install poppler-utils' komutunu çalıştırın.")
                elif "tesseract" in error_msg.lower():
                    print("❌ Tesseract kurulu değil! 'apt-get install tesseract-ocr tesseract-ocr-tur' komutunu çalıştırın.")
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
                        print("⚠️ OCR kütüphaneleri kurulu değil veya Poppler/Tesseract eksik")
                        print("⚠️ Kurulum için: 'apt-get install poppler-utils tesseract-ocr tesseract-ocr-tur' (Linux)")
                        print("⚠️ veya: 'brew install poppler tesseract tesseract-lang' (macOS)")
                except Exception as ocr_error:
                    error_msg = str(ocr_error)
                    print(f"❌ OCR hatası: {error_msg}")
                    if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                        print("❌ Poppler kurulu değil! 'apt-get install poppler-utils' komutunu çalıştırın.")
                    elif "tesseract" in error_msg.lower():
                        print("❌ Tesseract kurulu değil! 'apt-get install tesseract-ocr tesseract-ocr-tur' komutunu çalıştırın.")
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
                    print("⚠️ OCR kütüphaneleri kurulu değil veya Poppler/Tesseract eksik")
                    print("⚠️ Kurulum için: 'apt-get install poppler-utils tesseract-ocr tesseract-ocr-tur' (Linux)")
                    print("⚠️ veya: 'brew install poppler tesseract tesseract-lang' (macOS)")
            except Exception as ocr_error:
                error_msg = str(ocr_error)
                print(f"❌ OCR hatası: {error_msg}")
                # Poppler veya Tesseract eksikse özel mesaj
                if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
                    print("❌ Poppler kurulu değil! 'apt-get install poppler-utils' komutunu çalıştırın.")
                elif "tesseract" in error_msg.lower():
                    print("❌ Tesseract kurulu değil! 'apt-get install tesseract-ocr tesseract-ocr-tur' komutunu çalıştırın.")
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
                detail="OCR kullanımı isteniyor ancak Tesseract OCR kurulu değil. Lütfen 'apt-get install tesseract-ocr tesseract-ocr-tur' komutunu çalıştırın."
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
        print(f"📸 OCR modu aktif: Tüm sayfalar OCR ile işlenecek")
    
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


@app.post("/api/auto-scraper/analyze", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="Kurum mevzuatlarını analiz et ve yüklü olmayanları tespit et")
async def auto_scraper_analyze(req: AutoScraperAnalyzeRequest):
    """
    Belirtilen kurumun mevzuatlarını tarar, yüklü olmayan mevzuatları tespit eder
    ve sayıları Telegram'a gönderir.
    """
    try:
        print("\n" + "="*80)
        print(f"🚀 Auto Scraper Analyze İsteği Alındı (Kurum ID: {req.kurum_id}, DETSIS: {req.detsis or 'N/A'})")
        print("="*80)
        
        # Type kontrolü
        if req.type.lower() != "kaysis":
            return ScrapeResponse(
                success=False,
                message=f"Desteklenmeyen scraper tipi: {req.type}. Şu an için sadece 'kaysis' desteklenmektedir.",
                data={"error": "UNSUPPORTED_TYPE", "type": req.type}
            )
        
        # MongoDB'den kurum bilgisini çek (kurum_adi ve detsis)
        kurum_adi = None
        kurum_detsis: Optional[str] = None
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
                    kurum_detsis_val = (kurum_doc.get("detsis") or "").strip()
                    kurum_detsis = kurum_detsis_val or None
                client.close()
        except Exception as e:
            print(f"⚠️ MongoDB'den kurum bilgisi alınamadı: {str(e)}")
            kurum_adi = "Bilinmeyen Kurum"
        
        print(f"📋 Kurum: {kurum_adi}")
        # DETSIS bilgisini belirle (öncelik request, yoksa kurum kaydı)
        detsis = (req.detsis or "").strip() if req.detsis else None
        if not detsis:
            detsis = kurum_detsis
        
        if not detsis:
            print("❌ DETSIS bilgisi bulunamadı. Kurum kaydında veya istekte DETSIS alanı yok.")
            raise HTTPException(
                status_code=400,
                detail="DETSIS bilgisi bulunamadı. Lütfen kurum için detsis alanını doldurun veya istekte gönderin."
            )
        
        print(f"🔢 DETSIS: {detsis}")
        
        # Telegram'a analiz başlangıç mesajı gönder
        try:
            start_msg = "🔎 <b>Analiz Başladı</b>\n\n"
            start_msg += f"<b>Kurum:</b> {kurum_adi}\n"
            start_msg += f"<b>DETSIS:</b> {detsis}"
            _send_telegram_message(start_msg)
        except Exception as e:
            print(f"⚠️ Analiz başlangıç mesajı gönderilemedi: {str(e)}")
        
        # MevzuatGPT'de yüklü belgeleri çek
        uploaded_docs = []
        cfg = _load_config()
        if cfg:
            token = _login_with_config(cfg)
            if token:
                api_base_url = cfg.get("api_base_url")
                print(f"📡 API'den yüklü documents çekiliyor...")
                try:
                    uploaded_docs = get_uploaded_documents(api_base_url, token, use_streamlit=False)
                    print(f"✅ {len(uploaded_docs)} document bulundu")
                except Exception as e:
                    print(f"⚠️ Documents çekme hatası: {str(e)}")
        
        # Portal'da (MongoDB metadata) yüklü belgeleri çek
        portal_docs = []
        try:
            client = _get_mongodb_client()
            if client:
                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
                db = client[database_name]
                metadata_collection = db[metadata_collection_name]
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
            all_sections, stats = scrape_kaysis_mevzuat(detsis=detsis)
            print_results_to_console(all_sections, stats)
        
        # Yüklü olmayan mevzuatları tespit et
        not_uploaded_to_mevzuatgpt = []
        not_uploaded_to_portal = []
        # Otomatik yükleme listesi: sadece biri yüklü olanlar (senkronizasyon için)
        sync_items = []
        total_items = 0
        
        for section in all_sections:
            items = section.get('items', [])
            for item in items:
                total_items += 1
                item_baslik = item.get('baslik', '')
                item_normalized = normalize_for_exact_match(item_baslik)
                
                # MevzuatGPT kontrolü
                is_in_mevzuatgpt = False
                for doc in uploaded_docs:
                    belge_adi = doc.get("belge_adi", "")
                    if belge_adi:
                        belge_normalized = normalize_for_exact_match(belge_adi)
                        if item_normalized == belge_normalized:
                            is_in_mevzuatgpt = True
                            break
                
                # Portal kontrolü
                is_in_portal = False
                for doc in portal_docs:
                    pdf_adi = doc.get("pdf_adi", "")
                    if pdf_adi:
                        pdf_normalized = normalize_for_exact_match(pdf_adi)
                        if item_normalized == pdf_normalized:
                            is_in_portal = True
                            break
                
                # Yüklü olmayanları listeye ekle (istatistik için)
                if not is_in_mevzuatgpt:
                    not_uploaded_to_mevzuatgpt.append({
                        "baslik": item_baslik,
                        "link": item.get('link', ''),
                        "section_title": section.get('section_title', '')
                    })
                
                if not is_in_portal:
                    not_uploaded_to_portal.append({
                        "baslik": item_baslik,
                        "link": item.get('link', ''),
                        "section_title": section.get('section_title', '')
                    })

                # Otomatik yükleme listesi:
                # - Eğer hem MevzuatGPT'de hem Portal'da yoksa → listeye ALMA
                # - Eğer her ikisinde de varsa → listeye ALMA
                # - Sadece birinde varsa → listeye AL (tümünü yükle modu eksik tarafı tamamlayacak)
                if (is_in_mevzuatgpt and is_in_portal) or (not is_in_mevzuatgpt and not is_in_portal):
                    continue

                sync_items.append({
                    "baslik": item_baslik,
                    "link": item.get('link', ''),
                    "section_title": section.get('section_title', '')
                })
        
        # Sayıları hesapla
        mevzuatgpt_missing = len(not_uploaded_to_mevzuatgpt)
        portal_missing = len(not_uploaded_to_portal)
        mevzuatgpt_existing = max(total_items - mevzuatgpt_missing, 0)
        portal_existing = max(total_items - portal_missing, 0)
        
        print(f"\n📊 ANALİZ SONUÇLARI:")
        print(f"   Toplam mevzuat: {total_items}")
        print(f"   MevzuatGPT'de yüklü olmayan: {mevzuatgpt_missing}")
        print(f"   Portal'da yüklü olmayan: {portal_missing}")
        
        # Global state'e kaydet
        auto_scraper_jobs[req.kurum_id] = {
            "pending_items": sync_items,  # Sadece biri yüklü olanlar (senkronizasyon için)
            "is_running": False,
            "stop_requested": False,
            "kurum_adi": kurum_adi
        }
        
        # Telegram'a mesaj gönder - detaylı görünüm + progress bar
        def _build_progress_bar(done: int, total: int, bar_len: int = 20) -> str:
            if total <= 0:
                return "⏳ Henüz veri yok"
            ratio = done / total
            filled_len = int(bar_len * ratio)
            filled = "█" * filled_len
            empty = "░" * (bar_len - filled_len)
            percent = round(ratio * 100)
            return f"{filled}{empty} {percent}%"
        
        mevzuatgpt_bar = _build_progress_bar(mevzuatgpt_existing, total_items)
        portal_bar = _build_progress_bar(portal_existing, total_items)
        
        message = f"📊 <b>{kurum_adi}</b> – Mevzuat Analizi\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += f"📚 <b>Toplam Mevzuat:</b> {total_items}\n\n"
        
        message += "🧠 <b>MevzuatGPT</b>\n"
        message += f"   ✅ Yüklü : {mevzuatgpt_existing}\n"
        message += f"   ❌ Eksik : {mevzuatgpt_missing}\n"
        message += f"   📈 Oran : {mevzuatgpt_existing}/{total_items}\n"
        message += f"   {mevzuatgpt_bar}\n\n"
        
        message += "📂 <b>Portal</b>\n"
        message += f"   ✅ Yüklü : {portal_existing}\n"
        message += f"   ❌ Eksik : {portal_missing}\n"
        message += f"   📈 Oran : {portal_existing}/{total_items}\n"
        message += f"   {portal_bar}"
        
        _send_telegram_message(message)
        
        return ScrapeResponse(
            success=True,
            message=f"{kurum_adi} analiz işlemi tamamlandı. Yüklü olmayan mevzuatlar tespit edildi.",
            data={
                "total_items": total_items,
                "not_uploaded_mevzuatgpt": mevzuatgpt_missing,
                "not_uploaded_portal": portal_missing,
                "pending_count": len(sync_items)
            }
        )
        
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Analiz işlemi sırasında hata oluştu: {str(e)}"
        )


@app.post("/api/auto-scraper/start", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="Yüklü olmayan mevzuatları sırayla yükle")
async def auto_scraper_start(req: AutoScraperStartRequest):
    """
    Daha önce analiz edilen kurumun yüklü olmayan mevzuatlarını sırayla yükler.
    Stop komutu gönderilene kadar devam eder.
    """
    try:
        print("\n" + "="*80)
        print(f"🚀 Auto Scraper Start İsteği Alındı (Kurum ID: {req.kurum_id})")
        print("="*80)
        
        # Job kontrolü - analiz yoksa otomatik analiz yap
        if req.kurum_id not in auto_scraper_jobs:
            print("ℹ️ Bu kurum için daha önce analiz yapılmamış. Otomatik analiz başlatılıyor...")
            try:
                analyze_req = AutoScraperAnalyzeRequest(
                    kurum_id=req.kurum_id,
                    detsis=None,
                    type="kaysis"
                )
                await auto_scraper_analyze(analyze_req)
            except HTTPException as e:
                # Telegram'a hata mesajı gönder
                _send_telegram_message(f"❌ Otomatik analiz hatası: {e.detail}")
                raise
            except Exception as e:
                print(f"❌ Otomatik analiz sırasında hata: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Otomatik analiz sırasında hata oluştu: {str(e)}"
                )
        
        # Analiz sonrası job tekrar kontrol et
        if req.kurum_id not in auto_scraper_jobs:
            raise HTTPException(
                status_code=500,
                detail="Analiz sonrasında job oluşturulamadı."
            )
        
        job = auto_scraper_jobs[req.kurum_id]
        pending_items = job.get("pending_items", [])
        
        # Eğer analiz yapılmış ama liste boşsa, tekrar analiz dene
        if not pending_items:
            print("ℹ️ Pending liste boş, analiz tekrarlanıyor...")
            try:
                analyze_req = AutoScraperAnalyzeRequest(
                    kurum_id=req.kurum_id,
                    detsis=None,
                    type="kaysis"
                )
                await auto_scraper_analyze(analyze_req)
                job = auto_scraper_jobs.get(req.kurum_id, job)
                pending_items = job.get("pending_items", [])
            except Exception as e:
                print(f"❌ Tekrar analiz sırasında hata: {str(e)}")
            
            if not pending_items:
                return ScrapeResponse(
                    success=True,
                    message="Yüklenecek mevzuat bulunamadı.",
                    data={"processed_count": 0, "total_count": 0}
                )
        
        if job.get("is_running", False):
            return ScrapeResponse(
                success=False,
                message="Bu kurum için yükleme işlemi zaten devam ediyor.",
                data={"is_running": True}
            )
        
        # Eğer stop isteği önceden verilmişse yeni işlem başlatma
        if job.get("stop_requested", False):
            print("⏹️ Bu kurum için stop isteği mevcut, yeni yükleme başlatılmayacak.")
            return ScrapeResponse(
                success=False,
                message="Bu kurum için stop isteği var. Yeni yükleme başlatılmadı.",
                data={"is_running": False, "stop_requested": True}
            )
        
        # Job'u başlat
        job["is_running"] = True
        job["stop_requested"] = False
        
        kurum_adi = job.get("kurum_adi", "Bilinmeyen Kurum")
        print(f"📋 Kurum: {kurum_adi}")
        print(f"📊 Toplam {len(pending_items)} mevzuat yüklenecek")
        
        # Başlangıç chunk sayısını çek
        initial_chunk_count = None
        cfg = _load_config()
        api_base_url = None
        access_token = None
        if cfg:
            api_base_url = cfg.get("api_base_url")
            access_token = _login_with_config(cfg)
            if api_base_url and access_token:
                print(f"\n🔍 Başlangıç chunk sayısı çekiliyor...")
                initial_chunk_count = _get_embeddings_count(api_base_url, access_token)
                if initial_chunk_count is not None:
                    print(f"✅ Başlangıç chunk sayısı: {initial_chunk_count:,}")
                else:
                    print(f"⚠️ Başlangıç chunk sayısı çekilemedi, chunk kontrolü yapılmayacak")
            else:
                print(f"⚠️ API bilgileri alınamadı, chunk kontrolü yapılmayacak")
        else:
            print(f"⚠️ Config yüklenemedi, chunk kontrolü yapılmayacak")
        
        processed_count = 0
        failed_count = 0
        
        try:
            for i, item in enumerate(pending_items, 1):
                # Stop kontrolü
                if job.get("stop_requested", False):
                    print(f"⏹️ Stop komutu alındı, yükleme durduruluyor...")
                    break
                
                baslik = item.get("baslik", "")
                link = item.get("link", "")
                section_title = item.get("section_title", "")
                
                print(f"\n{'='*80}")
                print(f"📄 [{i}/{len(pending_items)}] Yükleniyor: {baslik}")
                print(f"{'='*80}")
                
                try:
                    # ProcessRequest oluştur (mode="t" - tümünü yükle)
                    process_req = ProcessRequest(
                        kurum_id=req.kurum_id,
                        detsis="",  # Gerekli değil çünkü link zaten var
                        type="kaysis",
                        link=link,
                        mode="t",  # Tümünü yükle - process_item içinde otomatik kontrol yapılıyor
                        category=section_title,
                        document_name=baslik,
                        use_ocr=False
                    )
                    
                    # Mevzuatı yükle
                    result = await process_item(process_req)
                    
                    if result.success:
                        print(f"✅ [{i}/{len(pending_items)}] Yükleme tamamlandı")
                        
                        # Stop isteği geldiyse bu noktadan sonra hiçbir ek işlem yapma
                        if job.get("stop_requested", False):
                            print("⏹️ Stop komutu alındı, analiz ve ek işlemler yapılmadan döngüden çıkılıyor...")
                            break
                        
                        # Stop kontrolü (bekleme öncesi)
                        if job.get("stop_requested", False):
                            print("⏹️ Stop komutu alındı, bekleme ve ek işlemler atlanıyor...")
                            break
                        
                        # 5 saniye bekle
                        import asyncio
                        await asyncio.sleep(5)
                        
                        # Chunk kontrolü yap (eğer başlangıç sayısı alındıysa)
                        chunk_updated = True
                        final_chunk_count = None  # Chunk kontrolü yapılmadıysa None
                        chunk_check_performed = False  # Chunk kontrolü yapıldı mı?
                        if initial_chunk_count is not None and cfg and api_base_url and access_token:
                            # MevzuatGPT'ye yüklendi mi kontrol et
                            msg = (result.message or "").lower()
                            if "mevzuatgpt" in msg or "tüm işlemler tamamlandı" in msg:
                                # Chunk güncellemesini bekle
                                chunk_check_performed = True
                                chunk_updated, final_chunk_count = await _wait_for_chunk_update(
                                    initial_count=initial_chunk_count,
                                    api_base_url=api_base_url,
                                    access_token=access_token,
                                    max_checks=15,
                                    check_interval=20,
                                    belge_adi=baslik
                                )
                                
                                # Chunk güncellenmediyse, döngüyü durdur
                                if not chunk_updated:
                                    print(f"\n❌ Chunk güncellenmedi! Döngü durduruluyor.")
                                    failed_count += 1  # Bu belge başarısız sayılır
                                    
                                    # Telegram'a hata mesajı gönder
                                    error_message = f"❌ <b>Chunk Güncellenmedi</b>\n\n"
                                    error_message += f"<b>Kurum:</b> {kurum_adi}\n"
                                    error_message += f"<b>Belge Adı:</b> {baslik}\n"
                                    error_message += f"<b>İşlem Öncesi Chunk:</b> {initial_chunk_count:,}\n"
                                    error_message += f"<b>İşlem Sonrası Chunk:</b> {final_chunk_count:,}\n"
                                    error_message += f"<b>Durum:</b> Chunk'a eklenmemiş, yükleme durduruldu."
                                    _send_telegram_message(error_message)
                                    
                                    break  # Döngüden çık
                                else:
                                    # Chunk güncellendi, başlangıç sayısını güncelle
                                    initial_chunk_count = final_chunk_count
                                    print(f"✅ Chunk güncellendi, yeni başlangıç sayısı: {initial_chunk_count:,}")
                        
                        # Chunk kontrolü başarılıysa (veya kontrol yapılmadıysa) processed_count artır
                        if chunk_updated:
                            processed_count += 1
                            print(f"✅ [{i}/{len(pending_items)}] Başarıyla yüklendi ve chunk kontrolü tamamlandı")
                        
                        # Stop kontrolü (chunk kontrolünden sonra)
                        if job.get("stop_requested", False):
                            print("⏹️ Stop komutu alındı, ek işlemler atlanıyor...")
                            break
                        
                        # MongoDB'den son yüklenen kaydı al
                        try:
                            client = _get_mongodb_client()
                            if client:
                                database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
                                metadata_collection_name = os.getenv("MONGODB_METADATA_COLLECTION", "metadata")
                                db = client[database_name]
                                metadata_collection = db[metadata_collection_name]
                                
                                # Son yüklenen kaydı bul (kurum_id ve pdf_adi ile)
                                metadata_doc = metadata_collection.find_one(
                                    {"kurum_id": req.kurum_id, "pdf_adi": baslik},
                                    sort=[("yukleme_tarihi", -1)]
                                )
                                
                                if metadata_doc:
                                    pdf_url = metadata_doc.get("pdf_url", "")
                                    belge_turu = metadata_doc.get("belge_turu", section_title)
                                    
                                    # Stop isteği geldiyse Telegram ve diğer işlemleri atla
                                    if job.get("stop_requested", False):
                                        print("⏹️ Stop komutu alındı, Telegram bildirimi atlanıyor...")
                                        break
                                    
                                    # Nereye yüklendi bilgisini belirle
                                    upload_target = "Bilinmiyor"
                                    msg = (result.message or "").lower()
                                    if "mevzuatgpt'ye yükleme tamamlandı" in msg:
                                        upload_target = "Sadece MevzuatGPT"
                                    elif "portal'a yükleme tamamlandı" in msg:
                                        upload_target = "Sadece Portal"
                                    elif "tüm işlemler tamamlandı" in msg or "mevzuatgpt + portal" in msg:
                                        upload_target = "MevzuatGPT + Portal"
                                    
                                    # Telegram'a bilgi gönder (chunk bilgisi ile)
                                    telegram_message = f"✅ <b>Yeni Mevzuat Yüklendi</b>\n\n"
                                    telegram_message += f"<b>Kurum:</b> {kurum_adi}\n"
                                    telegram_message += f"<b>Belge Adı:</b> {baslik}\n"
                                    telegram_message += f"<b>Kategori:</b> {belge_turu}\n"
                                    telegram_message += f"<b>Nereye Yüklendi:</b> {upload_target}\n"
                                    
                                    # Chunk bilgisi ekle (eğer kontrol yapıldıysa)
                                    if chunk_check_performed and initial_chunk_count is not None and final_chunk_count is not None:
                                        telegram_message += f"<b>İşlem Öncesi Chunk:</b> {initial_chunk_count:,}\n"
                                        telegram_message += f"<b>İşlem Sonrası Chunk:</b> {final_chunk_count:,}\n"
                                    
                                    if pdf_url:
                                        telegram_message += f"<b>Public URL:</b> <a href=\"{pdf_url}\">{pdf_url}</a>"
                                    else:
                                        telegram_message += f"<b>Public URL:</b> Henüz yüklenmedi"
                                    
                                    _send_telegram_message(telegram_message)
                                else:
                                    print(f"⚠️ MongoDB'den kayıt bulunamadı: {baslik}")
                                
                                client.close()
                        except Exception as e:
                            print(f"⚠️ MongoDB kayıt okuma hatası: {str(e)}")
                    else:
                        failed_count += 1
                        print(f"❌ [{i}/{len(pending_items)}] Yükleme başarısız: {result.message}")
                
                except Exception as e:
                    failed_count += 1
                    print(f"❌ [{i}/{len(pending_items)}] Hata: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    # Hata olsa bile devam et
                    continue
        
        finally:
            # Job'u bitir
            job["is_running"] = False
            job["stop_requested"] = False
        
        print(f"\n{'='*80}")
        print(f"✅ Yükleme işlemi tamamlandı")
        print(f"   Başarılı: {processed_count}")
        print(f"   Başarısız: {failed_count}")
        print(f"{'='*80}")
        
        # Telegram'a özet gönder
        summary_message = f"📊 <b>Yükleme İşlemi Tamamlandı</b>\n\n"
        summary_message += f"<b>Kurum:</b> {kurum_adi}\n"
        summary_message += f"✅ Başarılı: {processed_count}\n"
        summary_message += f"❌ Başarısız: {failed_count}\n"
        summary_message += f"📄 Toplam: {len(pending_items)}"
        
        _send_telegram_message(summary_message)
        
        return ScrapeResponse(
            success=True,
            message=f"Yükleme işlemi tamamlandı. {processed_count} mevzuat başarıyla yüklendi.",
            data={
                "processed_count": processed_count,
                "failed_count": failed_count,
                "total_count": len(pending_items)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        # Job'u temizle
        if req.kurum_id in auto_scraper_jobs:
            auto_scraper_jobs[req.kurum_id]["is_running"] = False
        raise HTTPException(
            status_code=500,
            detail=f"Yükleme işlemi sırasında hata oluştu: {str(e)}"
        )


@app.post("/api/auto-scraper/stop", response_model=ScrapeResponse, tags=["SGK Scraper"], summary="Yükleme işlemini durdur")
async def auto_scraper_stop(req: AutoScraperStopRequest):
    """
    Devam eden yükleme işlemini durdurur.
    """
    try:
        print("\n" + "="*80)
        print(f"⏹️ Auto Scraper Stop İsteği Alındı (Kurum ID: {req.kurum_id})")
        print("="*80)
        
        if req.kurum_id not in auto_scraper_jobs:
            raise HTTPException(
                status_code=404,
                detail="Bu kurum için aktif bir işlem bulunamadı."
            )
        
        job = auto_scraper_jobs[req.kurum_id]
        
        if not job.get("is_running", False):
            return ScrapeResponse(
                success=True,
                message="Aktif bir yükleme işlemi yok.",
                data={"is_running": False}
            )
        
        # Stop flag'ini set et
        job["stop_requested"] = True
        
        kurum_adi = job.get("kurum_adi", "Bilinmeyen Kurum")
        print(f"⏹️ {kurum_adi} için yükleme işlemi durduruluyor...")
        
        # Telegram'a bilgi gönder
        stop_message = f"⏹️ <b>Yükleme İşlemi Durduruldu</b>\n\n"
        stop_message += f"<b>Kurum:</b> {kurum_adi}"
        
        _send_telegram_message(stop_message)
        
        return ScrapeResponse(
            success=True,
            message="Yükleme işlemi durduruldu. Yeni mevzuatlar başlatılmayacak.",
            data={"stop_requested": True}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Stop işlemi sırasında hata oluştu: {str(e)}"
        )


@app.post("/api/telegram/webhook", tags=["SGK Scraper"], summary="Telegram bot webhook")
async def telegram_webhook(update: Dict[str, Any]):
    """
    Telegram bot webhook endpoint'i.
    Desteklenen komutlar:
      - /analyze <kurum_id>
      - /start <kurum_id>
      - /stop <kurum_id>
    """
    try:
        # Telegram update içinden mesajı al
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not message:
            return {"ok": True}

        text = (message.get("text") or "").strip()
        if not text:
            return {"ok": True}

        # Komut ve argümanları ayıkla
        parts = text.split()
        if not parts:
            return {"ok": True}

        # /command@BotName formatını normalize et
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]

        print(f"📩 Telegram komutu alındı: {cmd} args={args}")

        if cmd == "/analyze":
            if not args:
                _send_telegram_message("⚠️ <b>Kullanım:</b> /analyze &lt;kurum_id&gt;")
                return {"ok": True}

            kurum_id = args[0]
            try:
                req = AutoScraperAnalyzeRequest(kurum_id=kurum_id, detsis=None, type="kaysis")
                await auto_scraper_analyze(req)
            except HTTPException as e:
                _send_telegram_message(f"❌ Analiz hatası: {e.detail}")

        elif cmd == "/start":
            if not args:
                _send_telegram_message("⚠️ <b>Kullanım:</b> /start &lt;kurum_id&gt;")
                return {"ok": True}

            kurum_id = args[0]
            try:
                req = AutoScraperStartRequest(kurum_id=kurum_id)
                await auto_scraper_start(req)
            except HTTPException as e:
                _send_telegram_message(f"❌ Başlatma hatası: {e.detail}")

        elif cmd == "/stop":
            if not args:
                _send_telegram_message("⚠️ <b>Kullanım:</b> /stop &lt;kurum_id&gt;")
                return {"ok": True}

            kurum_id = args[0]
            try:
                req = AutoScraperStopRequest(kurum_id=kurum_id)
                await auto_scraper_stop(req)
            except HTTPException as e:
                _send_telegram_message(f"❌ Stop hatası: {e.detail}")

        else:
            # Bilinmeyen komut için yardım mesajı
            help_text = (
                "🤖 <b>Auto Scraper Komutları</b>\n\n"
                "/analyze &lt;kurum_id&gt; - Kurum mevzuatlarını analiz et\n"
                "/start &lt;kurum_id&gt; - Analiz edilmiş kurum için otomatik yüklemeyi başlat\n"
                "/stop &lt;kurum_id&gt; - Devam eden yüklemeyi durdur\n"
            )
            _send_telegram_message(help_text)

        return {"ok": True}
    except Exception as e:
        print(f"⚠️ Telegram webhook hatası: {str(e)}")
        return {"ok": True}


if __name__ == "__main__":
    print("🚀 FastAPI Server başlatılıyor...")
    print("📡 Server: http://0.0.0.0:8000")
    print("📚 API Docs: http://0.0.0.0:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)

