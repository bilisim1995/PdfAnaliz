#!/usr/bin/env python3
"""
Scraper Analiz Test Scripti
Belirtilen kurum için scraper'ı çalıştırıp sonuçları JSON dosyasına kaydeder
"""
import asyncio
import sys
from api_server import auto_scraper_analyze, AutoScraperAnalyzeRequest

async def main():
    kurum_id = "68d94b822db1b7f69a79a861"
    
    print("="*80)
    print(f"🧪 Scraper Analiz Test Scripti")
    print("="*80)
    print(f"📋 Kurum ID: {kurum_id}")
    print(f"🔍 Analiz başlatılıyor...")
    print("="*80)
    
    try:
        req = AutoScraperAnalyzeRequest(
            kurum_id=kurum_id,
            detsis=None,
            type="kaysis"
        )
        
        result = await auto_scraper_analyze(req)
        
        print("\n" + "="*80)
        print("✅ Analiz tamamlandı!")
        print("="*80)
        print(f"📊 Sonuç: {result.message}")
        print(f"📈 Data: {result.data}")
        print("="*80)
        
        if result.success:
            print("\n✅ JSON dosyası oluşturuldu (analiz_*.json)")
            sys.exit(0)
        else:
            print("\n❌ Analiz başarısız!")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n❌ Hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

