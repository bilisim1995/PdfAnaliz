"""
Telegram Bot Handlers
Telegram mesaj gönderme ve webhook işlemleri için yardımcı fonksiyonlar
"""
import os
from typing import Dict, Any, Callable, Awaitable
from fastapi import HTTPException

# curl_cffi import kontrolü
try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False


def send_telegram_message(message: str) -> bool:
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


async def handle_telegram_webhook(
    update: Dict[str, Any],
    start_handler: Callable[[str], Awaitable[Any]],
    stop_handler: Callable[[str], Awaitable[Any]]
) -> Dict[str, Any]:
    """
    Telegram webhook güncellemelerini işler.
    
    Args:
        update: Telegram webhook update dict
        start_handler: /start komutu için handler fonksiyonu
        stop_handler: /stop komutu için handler fonksiyonu
    
    Returns:
        {"ok": True} veya {"ok": False, "error": "..."}
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

        if cmd == "/start":
            if not args:
                send_telegram_message("⚠️ <b>Kullanım:</b> /start &lt;kurum_id&gt;")
                return {"ok": True}

            kurum_id = args[0]
            try:
                await start_handler(kurum_id)
            except HTTPException as e:
                send_telegram_message(f"❌ Başlatma hatası: {e.detail}")
            except Exception as e:
                send_telegram_message(f"❌ Başlatma hatası: {str(e)}")

        elif cmd == "/stop":
            if not args:
                send_telegram_message("⚠️ <b>Kullanım:</b> /stop &lt;kurum_id&gt;")
                return {"ok": True}

            kurum_id = args[0]
            try:
                await stop_handler(kurum_id)
            except HTTPException as e:
                send_telegram_message(f"❌ Stop hatası: {e.detail}")
            except Exception as e:
                send_telegram_message(f"❌ Stop hatası: {str(e)}")

        else:
            # Bilinmeyen komut için yardım mesajı
            help_text = (
                "🤖 <b>Auto Scraper Komutları</b>\n\n"
                "/start &lt;kurum_id&gt; - Analiz edilmiş kurum için otomatik yüklemeyi başlat\n"
                "/stop &lt;kurum_id&gt; - Devam eden yüklemeyi durdur\n"
            )
            send_telegram_message(help_text)

        return {"ok": True}
    except Exception as e:
        print(f"⚠️ Telegram webhook hatası: {str(e)}")
        return {"ok": True}

