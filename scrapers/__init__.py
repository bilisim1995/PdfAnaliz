"""
Scrapers Module
Tek bir KAYSİS scraper modülü - Tüm kurumlar için
"""
from .kaysis_scraper import (
    scrape_kaysis_mevzuat,
    print_results_to_console,
    get_uploaded_documents,
    check_if_document_exists,
    normalize_text,
    is_title_similar,
    turkish_title,
    turkish_sentence_case
)

__all__ = [
    'scrape_kaysis_mevzuat',
    'print_results_to_console',
    'get_uploaded_documents',
    'check_if_document_exists',
    'normalize_text',
    'is_title_similar',
    'turkish_title',
    'turkish_sentence_case'
]
