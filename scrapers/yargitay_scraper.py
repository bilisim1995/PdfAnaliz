import json
import re
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False

from utils import get_proxy_from_db, html_content_to_pdf


YARGITAY_LIST_URL = "https://karararama.yargitay.gov.tr/aramalist"
YARGITAY_DOC_URL = "https://karararama.yargitay.gov.tr/getDokuman"


def _build_headers() -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
        "Origin": "https://karararama.yargitay.gov.tr",
        "Referer": "https://karararama.yargitay.gov.tr/",
    }


def fetch_yargitay_list(page_number: int) -> List[Dict[str, Any]]:
    proxies = get_proxy_from_db()
    if proxies:
        print("🔐 Yargıtay listesi için proxy kullanılıyor...")
    else:
        print("⚠️ Proxy bulunamadı, direkt bağlantı deneniyor...")

    payload = {
        "data": {
            "aranan": "karar",
            "arananKelime": "karar",
            "pageSize": 100,
            "pageNumber": page_number
        }
    }

    if CURL_CFFI_AVAILABLE:
        response = requests.post(
            YARGITAY_LIST_URL,
            headers=_build_headers(),
            data=json.dumps(payload),
            timeout=120,
            proxies=proxies,
            impersonate="chrome110"
        )
    else:
        response = requests.post(
            YARGITAY_LIST_URL,
            headers=_build_headers(),
            json=payload,
            timeout=120,
            proxies=proxies
        )

    response.raise_for_status()
    data = response.json()
    return data.get("data", {}).get("data", []) or []


def build_yargitay_document_url(doc_id: str) -> str:
    return f"{YARGITAY_DOC_URL}?id={doc_id}"


def fetch_yargitay_document_html(doc_id: str) -> str:
    url = build_yargitay_document_url(doc_id)
    proxies = get_proxy_from_db()
    if proxies:
        print("🔐 Yargıtay dokümanı için proxy kullanılıyor...")
    else:
        print("⚠️ Proxy bulunamadı, direkt bağlantı deneniyor...")

    headers = _build_headers()
    headers["Accept"] = "text/html,application/xml;q=0.9,*/*;q=0.8"

    if CURL_CFFI_AVAILABLE:
        response = requests.get(
            url,
            headers=headers,
            timeout=120,
            proxies=proxies,
            impersonate="chrome110"
        )
    else:
        response = requests.get(
            url,
            headers=headers,
            timeout=120,
            proxies=proxies
        )

    response.raise_for_status()
    return _extract_html_from_xml(response.text) or response.text


def _extract_html_from_xml(xml_text: str) -> Optional[str]:
    if not xml_text:
        return None

    try:
        root = ET.fromstring(xml_text)
        data_node = root.find(".//data")
        if data_node is None:
            return None

        if len(list(data_node)) > 0:
            first_child = list(data_node)[0]
            return ET.tostring(first_child, encoding="unicode", method="html")

        if data_node.text:
            return data_node.text.strip()
    except ET.ParseError:
        pass

    match = re.search(r"<data>(.*)</data>", xml_text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


async def convert_html_to_pdf(html_content: str) -> str:
    return await html_content_to_pdf(html_content, base_url="https://karararama.yargitay.gov.tr")
