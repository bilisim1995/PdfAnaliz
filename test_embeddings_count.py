#!/usr/bin/env python3
"""
Elasticsearch Embeddings Count Test Scripti
MevzuatGPT API'sinden toplam chunk (embedding) sayısını çeker
"""

import json
import requests
import sys
from typing import Optional, Dict, Any
from datetime import datetime

def load_config() -> Optional[Dict[str, Any]]:
    """config.json dosyasını yükler"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ config.json dosyası bulunamadı!")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ config.json parse hatası: {str(e)}")
        return None
    except Exception as e:
        print(f"❌ config.json okuma hatası: {str(e)}")
        return None

def login(api_base_url: str, email: str, password: str) -> Optional[str]:
    """API'ye login yapıp access_token döndürür"""
    try:
        login_url = f"{api_base_url.rstrip('/')}/api/auth/login"
        
        print(f"🔐 Login yapılıyor: {login_url}")
        
        response = requests.post(
            login_url,
            headers={"Content-Type": "application/json"},
            json={
                "email": email,
                "password": password
            },
            timeout=60
        )
        
        if response.status_code == 200:
            data = response.json()
            access_token = data.get("access_token")
            if access_token:
                print("✅ Login başarılı!")
                return access_token
            else:
                print("❌ Login yanıtında access_token bulunamadı!")
                return None
        else:
            print(f"❌ Login başarısız: HTTP {response.status_code}")
            try:
                error_data = response.json()
                print(f"   Hata mesajı: {error_data.get('message', 'Bilinmeyen hata')}")
            except:
                print(f"   Response: {response.text[:200]}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Bağlantı hatası: {str(e)}")
        return None
    except Exception as e:
        print(f"❌ Login hatası: {str(e)}")
        return None

def get_embeddings_count(api_base_url: str, access_token: str) -> Optional[Dict[str, Any]]:
    """Embeddings count endpoint'inden toplam chunk sayısını çeker"""
    try:
        url = f"{api_base_url.rstrip('/')}/api/admin/embeddings/count"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        print(f"\n📊 Embeddings count endpoint'ine istek atılıyor...")
        print(f"   URL: {url}")
        
        response = requests.get(
            url,
            headers=headers,
            timeout=60
        )
        
        print(f"\n📥 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get("success"):
                result = data.get("data", {})
                total_embeddings = result.get("total_embeddings", 0)
                document_id = result.get("document_id")
                timestamp = result.get("timestamp")
                
                print("\n" + "="*60)
                print("✅ BAŞARILI!")
                print("="*60)
                print(f"📦 Toplam Embeddings (Chunk) Sayısı: {total_embeddings:,}")
                if document_id:
                    print(f"📄 Document ID: {document_id}")
                if timestamp:
                    print(f"🕐 Timestamp: {timestamp}")
                print("="*60)
                
                return {
                    "success": True,
                    "total_embeddings": total_embeddings,
                    "document_id": document_id,
                    "timestamp": timestamp,
                    "raw_response": data
                }
            else:
                print("❌ Response'da success: false döndü!")
                print(f"   Response: {json.dumps(data, indent=2, ensure_ascii=False)}")
                return None
                
        elif response.status_code == 401:
            print("❌ Yetkilendirme hatası (401 Unauthorized)")
            print("   Token geçersiz veya süresi dolmuş olabilir!")
            return None
        elif response.status_code == 403:
            print("❌ Yetki hatası (403 Forbidden)")
            print("   Bu endpoint için admin yetkisi gerekli olabilir!")
            try:
                error_data = response.json()
                print(f"   Hata mesajı: {error_data.get('message', 'Bilinmeyen hata')}")
            except:
                pass
            return None
        else:
            print(f"❌ Beklenmeyen HTTP status code: {response.status_code}")
            try:
                error_data = response.json()
                print(f"   Response: {json.dumps(error_data, indent=2, ensure_ascii=False)}")
            except:
                print(f"   Response text: {response.text[:500]}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ İstek hatası: {str(e)}")
        return None
    except Exception as e:
        print(f"❌ Embeddings count hatası: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def main():
    """Ana fonksiyon"""
    print("="*60)
    print("🧪 Elasticsearch Embeddings Count Test Scripti")
    print("="*60)
    
    # Config yükle
    config = load_config()
    if not config:
        sys.exit(1)
    
    api_base_url = config.get("api_base_url")
    email = config.get("admin_email")
    password = config.get("admin_password")
    
    if not all([api_base_url, email, password]):
        print("❌ config.json'da eksik bilgiler var!")
        print(f"   api_base_url: {'✅' if api_base_url else '❌'}")
        print(f"   admin_email: {'✅' if email else '❌'}")
        print(f"   admin_password: {'✅' if password else '❌'}")
        sys.exit(1)
    
    print(f"\n📋 Config bilgileri:")
    print(f"   API Base URL: {api_base_url}")
    print(f"   Admin Email: {email}")
    print(f"   Admin Password: {'*' * len(password)}")
    
    # Login
    access_token = login(api_base_url, email, password)
    if not access_token:
        print("\n❌ Login başarısız, işlem sonlandırılıyor!")
        sys.exit(1)
    
    # Embeddings count çek
    result = get_embeddings_count(api_base_url, access_token)
    
    if result:
        print("\n✅ Test başarıyla tamamlandı!")
        sys.exit(0)
    else:
        print("\n❌ Test başarısız!")
        sys.exit(1)

if __name__ == "__main__":
    main()

