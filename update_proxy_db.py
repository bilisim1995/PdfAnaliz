#!/usr/bin/env python3
"""
MongoDB'ye yeni proxy bilgilerini ekler veya günceller
"""

import os
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Yeni proxy bilgileri
NEW_PROXY = {
    "host": "istanbul8.livaproxy.com",
    "port": "50603",
    "username": "mevzuatgpt",
    "password": "mevzuatgpt1235",
    "is_active": True
}


def update_proxy_in_db():
    """MongoDB'ye yeni proxy bilgilerini ekler veya günceller"""
    try:
        connection_string = os.getenv("MONGODB_CONNECTION_STRING")
        if not connection_string:
            print("❌ MONGODB_CONNECTION_STRING environment variable bulunamadı!")
            return False
        
        client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        
        database_name = os.getenv("MONGODB_DATABASE", "mevzuatgpt")
        db = client[database_name]
        col = db["proxies"]
        
        # Eğer aynı host ve port'a sahip proxy varsa güncelle, yoksa yeni ekle
        existing = col.find_one({
            "host": NEW_PROXY["host"],
            "port": NEW_PROXY["port"]
        })
        
        if existing:
            # Mevcut proxy'yi güncelle
            print(f"📝 Mevcut proxy güncelleniyor (ID: {existing['_id']})...")
            
            # Eğer yeni proxy aktif yapılıyorsa, diğer aktif proxy'leri pasif yap
            if NEW_PROXY["is_active"]:
                col.update_many(
                    {"is_active": True, "_id": {"$ne": existing["_id"]}},
                    {"$set": {"is_active": False, "updated_at": datetime.now().isoformat()}}
                )
            
            update_data = {
                "username": NEW_PROXY["username"],
                "password": NEW_PROXY["password"],
                "is_active": NEW_PROXY["is_active"],
                "updated_at": datetime.now().isoformat()
            }
            
            col.update_one(
                {"_id": existing["_id"]},
                {"$set": update_data}
            )
            print("✅ Proxy başarıyla güncellendi!")
        else:
            # Yeni proxy ekle
            print("➕ Yeni proxy ekleniyor...")
            
            # Eğer yeni proxy aktif yapılıyorsa, diğer aktif proxy'leri pasif yap
            if NEW_PROXY["is_active"]:
                col.update_many(
                    {"is_active": True},
                    {"$set": {"is_active": False, "updated_at": datetime.now().isoformat()}}
                )
            
            doc = {
                "host": NEW_PROXY["host"],
                "port": NEW_PROXY["port"],
                "username": NEW_PROXY["username"],
                "password": NEW_PROXY["password"],
                "is_active": NEW_PROXY["is_active"],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            
            result = col.insert_one(doc)
            print(f"✅ Yeni proxy başarıyla eklendi! (ID: {result.inserted_id})")
        
        # Aktif proxy'yi göster
        active_proxy = col.find_one({"is_active": True})
        if active_proxy:
            print("\n📋 Aktif Proxy Bilgileri:")
            print(f"   Host: {active_proxy['host']}")
            print(f"   Port: {active_proxy['port']}")
            print(f"   Username: {active_proxy['username']}")
            print(f"   Password: {'*' * len(active_proxy.get('password', ''))}")
        
        client.close()
        return True
        
    except ConnectionFailure:
        print("❌ MongoDB bağlantısı kurulamadı!")
        return False
    except Exception as e:
        print(f"❌ Hata: {str(e)}")
        return False


if __name__ == "__main__":
    print("=" * 80)
    print("🔄 MongoDB Proxy Güncelleme")
    print("=" * 80)
    print()
    
    success = update_proxy_in_db()
    
    if success:
        print("\n✅ İşlem tamamlandı!")
    else:
        print("\n❌ İşlem başarısız!")
        exit(1)

