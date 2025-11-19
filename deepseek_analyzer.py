import openai
import json
import re
import time
from typing import Dict, Any

class DeepSeekAnalyzer:
    """DeepSeek AI ile PDF içerik analizi"""
    
    def __init__(self, api_key: str):
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=120.0,  # 2 dakika timeout
            max_retries=3  # Retry sayısı
        )
        self.api_key = api_key
    
    def analyze_section_content(self, text_content: str, max_retries: int = 3) -> Dict[str, Any]:
        """PDF bölüm içeriğini analiz ederek metadata oluşturur"""
        
        if not text_content or len(text_content.strip()) < 10:
            return {
                'title': 'İçerik Tespit Edilemedi',
                'description': 'Bu bölümde yeterli metin içeriği bulunamadı.',
                'keywords': 'içerik_yok'
            }
        
        # Retry mekanizması ile API çağrısı
        last_error = None
        for attempt in range(max_retries):
            try:
                return self._analyze_section_content_internal(text_content)
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                
                # Connection error veya network hatası ise retry yap
                if any(keyword in error_msg for keyword in ['connection', 'timeout', 'network', 'unreachable', 'refused']):
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2  # Exponential backoff: 2s, 4s, 6s
                        print(f"⚠️ DeepSeek API bağlantı hatası (deneme {attempt + 1}/{max_retries}), {wait_time}s sonra tekrar deneniyor...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"❌ DeepSeek API bağlantı hatası: {max_retries} deneme başarısız oldu")
                else:
                    # Diğer hatalar için retry yapma
                    break
        
        # Tüm denemeler başarısız oldu, fallback metadata döndür
        return self._create_fallback_metadata(text_content, last_error)
    
    def _analyze_section_content_internal(self, text_content: str) -> Dict[str, Any]:
        """PDF bölüm içeriğini analiz ederek metadata oluşturur (internal, retry olmadan)"""
        
        try:
            # Metin uzunluğunu sınırla (token limiti için)
            max_chars = 8000
            if len(text_content) > max_chars:
                text_content = text_content[:max_chars] + "..."
            
            prompt = f"""
Aşağıdaki PDF bölümü içeriğini analiz et ve RAG (Retrieval Augmented Generation) sisteminde kullanılmak üzere metadata oluştur.

İÇERİK:
{text_content}

GÖREV:
Bu içerik için aşağıdaki bilgileri oluştur:

1. BAŞLIK: İçeriğin ana konusunu özetleyen kısa ve açıklayıcı başlık (maksimum 100 karakter)
2. AÇIKLAMA: İçeriğin detaylı açıklaması, ne hakkında olduğu, hangi konuları kapsadığı (150-300 kelime)
3. ANAHTAR KELİMELER: RAG sisteminde arama için kullanılacak anahtar kelimeler (virgülle ayrılmış, maksimum 15 kelime)

KURALLAR:
- Türkçe karakter kullan
- Anahtar kelimeleri normal şekilde yaz, boşlukları koru (örn: "prim borcu,sosyal güvenlik")
- Teknik terimler ve mevzuat referansları önemli
- RAG sisteminde bulunabilirlik için optimize et
- Sadece verilen içeriğe dayalı bilgi ver

ÇIKTI FORMATI (sadece JSON döndür):
{{
    "title": "Başlık buraya",
    "description": "Açıklama buraya",
    "keywords": "kelime1,kelime2,kelime3"
}}
"""

            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system", 
                        "content": "Sen bir PDF analiz uzmanısın. Verilen metinleri analiz ederek RAG sistemi için optimal metadata oluşturuyorsun. Sadece JSON formatında yanıt ver."
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            # API yanıtını al
            result_text = response.choices[0].message.content
            if not result_text:
                raise ValueError("API'den boş yanıt alındı")
            result_text = result_text.strip()
            
            # JSON'ı ayıkla
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                result_json = json.loads(json_match.group())
                
                # Sonuçları temizle ve doğrula
                cleaned_result = self._clean_analysis_result(result_json)
                return cleaned_result
            else:
                raise ValueError("API yanıtında JSON bulunamadı")
                
        except Exception as e:
            # Hata durumunda exception'ı yukarı fırlat (retry mekanizması için)
            raise e
    
    def _clean_analysis_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """API sonucunu temizler ve doğrular"""
        cleaned = {}
        
        # Başlığı temizle
        title = result.get('title', '').strip()
        if not title or len(title) < 5:
            title = "PDF Bölümü"
        elif len(title) > 150:
            title = title[:147] + "..."
        cleaned['title'] = title
        
        # Açıklamayı temizle
        description = result.get('description', '').strip()
        if not description or len(description) < 20:
            description = "Bu PDF bölümü önemli bilgiler içermektedir."
        elif len(description) > 1000:
            description = description[:997] + "..."
        cleaned['description'] = description
        
        # Anahtar kelimeleri temizle
        keywords = result.get('keywords', '').strip()
        if not keywords:
            keywords = "pdf bölümü,doküman"
        else:
            # Anahtar kelimeleri işle (boşlukları koru)
            keyword_list = [kw.strip() for kw in keywords.split(',')]
            keyword_list = [kw for kw in keyword_list if kw and len(kw) > 1][:15]  # Maksimum 15 kelime
            keywords = ','.join(keyword_list)
        
        cleaned['keywords'] = keywords
        
        return cleaned
    
    def _create_fallback_metadata(self, text_content: str, error: Exception = None) -> Dict[str, Any]:
        """Hata durumunda basit metadata oluşturur"""
        # İçeriğin ilk birkaç kelimesinden başlık oluştur
        words = text_content.strip().split()[:15]
        title = ' '.join(words) if words else "PDF Bölümü"
        if len(title) > 100:
            title = title[:97] + "..."
        
        # İçerikten önemli kelimeleri çıkar (Türkçe stop words hariç)
        stop_words = {'bir', 'bu', 've', 'ile', 'için', 'olan', 'olarak', 'daha', 'çok', 'en', 'de', 'da', 'ki', 'gibi', 'kadar', 'sonra', 'önce', 'üzerine', 'altında', 'arasında', 'içinde', 'dışında', 'karşı', 'göre', 'doğru', 'madde', 'fıkra', 'bent', 'kanun', 'yönetmelik', 'tebliğ', 'hakkında', 'tarihli', 'sayılı', 'tarih', 'sayı'}
        
        # Anahtar kelimeleri çıkar
        common_words = []
        word_freq = {}
        
        # Metni temizle ve kelimelere ayır
        import re
        clean_text = re.sub(r'[^\w\s]', ' ', text_content.lower())
        words_list = clean_text.split()
        
        for word in words_list:
            word = word.strip()
            # 3 karakterden uzun, stop word değil, sadece harf içeren kelimeler
            if len(word) > 3 and word not in stop_words and word.isalpha():
                word_freq[word] = word_freq.get(word, 0) + 1
        
        # En sık kullanılan kelimeleri al
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        common_words = [word for word, _ in sorted_words[:10]]
        
        keywords = ','.join(common_words) if common_words else "pdf içerik,doküman"
        
        # Açıklama oluştur
        if error:
            error_type = "bağlantı" if "connection" in str(error).lower() or "timeout" in str(error).lower() else "analiz"
            description = f"Bu bölümün AI analizi yapılamadı ({error_type} hatası). İçerik yaklaşık {len(text_content):,} karakter barındırmaktadır. "
        else:
            description = f"Bu bölüm yaklaşık {len(text_content):,} karakter içerik barındırmaktadır. "
        
        # İçeriğin ilk 200 karakterinden açıklama ekle
        preview = text_content[:200].strip()
        if preview:
            description += f"İçerik özeti: {preview}..."
        else:
            description += "İçeriğin detaylı analizi yapılamadı."
        
        # Açıklamayı 500 karakterle sınırla
        if len(description) > 500:
            description = description[:497] + "..."
        
        return {
            'title': title,
            'description': description,
            'keywords': keywords
        }
    
    def suggest_content_based_sections(self, page_texts: list, total_pages: int) -> list:
        """İçerik bazlı optimal bölümleme önerileri oluşturur"""
        try:
            # Her 3 sayfada bir örnek al (çok uzun olmaması için)
            sample_pages = []
            for i in range(0, total_pages, max(1, total_pages // 10)):  # Maksimum 10 örnek
                if i < len(page_texts):
                    sample_pages.append({
                        'page': i + 1,
                        'text': page_texts[i][:500]  # Her sayfadan ilk 500 karakter
                    })
            
            # Örnekleri birleştir
            samples_text = "\n\n".join([f"SAYFA {s['page']}: {s['text']}" for s in sample_pages])
            
            prompt = f"""
Bu bir {total_pages} sayfalık PDF dokümanının içerik örnekleridir. RAG (Retrieval Augmented Generation) sistemi için bu PDF'i optimal bölümlere ayırmalıyım.

İÇERİK ÖRNEKLERİ:
{samples_text}

GÖREV:
Bu PDF'i anlam bütünlüğü olan, RAG için optimal bölümlere ayır. Her bölüm:
- Tek bir ana konuyu veya ilişkili konuları kapsamalı
- Çok küçük (1-2 sayfa) veya çok büyük (30+ sayfa) olmamalı
- Mantıklı bir başlangıç ve bitiş noktası olmalı

ÇIKTI FORMATI (sadece JSON array döndür):
[
  {{"start_page": 1, "end_page": 5, "reason": "Giriş ve genel kavramlar"}},
  {{"start_page": 6, "end_page": 12, "reason": "Ana konu 1"}},
  {{"start_page": 13, "end_page": {total_pages}, "reason": "Ana konu 2 ve sonuç"}}
]

ÖNEMLİ:
- Tüm sayfalar kapsanmalı (1'den {total_pages}'a kadar)
- Bölümler örtüşmemeli
- Sayfa numaraları ardışık olmalı
- Maksimum 15 bölüm oluştur
"""

            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": "Sen bir doküman analiz uzmanısın. PDF içeriklerini analiz ederek RAG sistemleri için optimal bölümleme önerileri sunuyorsun."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            result_text = response.choices[0].message.content
            if not result_text:
                raise ValueError("API'den boş yanıt alındı")
            
            # JSON array'i ayıkla
            json_match = re.search(r'\[.*\]', result_text.strip(), re.DOTALL)
            if json_match:
                sections = json.loads(json_match.group())
                
                # Bölümleri doğrula ve düzelt
                validated_sections = self._validate_sections(sections, total_pages)
                return validated_sections
            else:
                raise ValueError("API yanıtında JSON array bulunamadı")
                
        except Exception as e:
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ['connection', 'timeout', 'network']):
                print(f"⚠️ İçerik bazlı bölümleme hatası (bağlantı): {str(e)}")
            else:
                print(f"⚠️ İçerik bazlı bölümleme hatası: {str(e)}")
            # Fallback: Basit eşit bölümleme
            return self._create_fallback_sections(total_pages)
    
    def suggest_document_name(self, text_content: str) -> str:
        """PDF içeriğinden belge adı önerisi oluşturur"""
        try:
            # İçerik çok uzunsa ilk 3000 karakteri al
            if len(text_content) > 3000:
                text_content = text_content[:3000]
            
            prompt = f"""
Aşağıdaki PDF dokümanının içeriğine bakarak, bu doküman için KISA ve AÇIKLAYICI bir belge adı öner.

İÇERİK:
{text_content}

GÖREV:
Bu PDF için profesyonel bir belge adı öner. Belge adı:
- Kısa ve öz olmalı (maksimum 50 karakter)
- Türkçe karakterler kullanabilir
- İçeriği en iyi özetlemeli
- Dosya sistemi için uygun olmalı (özel karakterler yok)
- Sadece belge adını ver, açıklama yapma

ÖRNEK ÇıKTıLAR:
- TCK_2024
- Otopark_Yonetmeligi_2024
- Sosyal_Guvenlik_Kanunu
- Isci_Sagligi_Rehberi

SADECE BELGE ADI ÇIKTISI VER (JSON veya başka format yok):
"""

            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": "Sen bir belge adlandırma uzmanısın. Verilen içeriğe göre kısa ve profesyonel belge adları öneriyorsun."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                max_tokens=100
            )
            
            suggested_name = response.choices[0].message.content
            if not suggested_name:
                return "Belge Adı"
            
            suggested_name = suggested_name.strip()
            
            # Sadece dosya sistemi için tehlikeli karakterleri temizle
            # Türkçe karakterler ve boşluklar korunsun
            suggested_name = re.sub(r'[<>:"/\\|?*]', '', suggested_name)
            suggested_name = suggested_name.strip()
            
            # Çok uzunsa kısalt
            if len(suggested_name) > 100:
                suggested_name = suggested_name[:100]
            
            return suggested_name if suggested_name else "Belge Adı"
            
        except Exception as e:
            print(f"Belge adı önerisi hatası: {str(e)}")
            return "Belge_Adi"
    
    def _validate_sections(self, sections: list, total_pages: int) -> list:
        """Bölümlerin geçerliliğini kontrol eder ve düzeltir"""
        if not sections:
            return self._create_fallback_sections(total_pages)
        
        validated = []
        expected_start = 1
        
        for section in sections:
            start = section.get('start_page', expected_start)
            end = section.get('end_page', start)
            
            # Sınırları düzelt
            start = max(expected_start, min(start, total_pages))
            end = max(start, min(end, total_pages))
            
            if start <= total_pages:
                validated.append({
                    'start_page': start,
                    'end_page': end,
                    'reason': section.get('reason', '')
                })
                expected_start = end + 1
        
        # Eğer tüm sayfalar kapsanmadıysa, son bölümü genişlet
        if validated and validated[-1]['end_page'] < total_pages:
            validated[-1]['end_page'] = total_pages
        
        # Hiç bölüm yoksa fallback kullan
        if not validated:
            return self._create_fallback_sections(total_pages)
        
        return validated
    
    def _create_fallback_sections(self, total_pages: int) -> list:
        """Basit eşit bölümleme oluşturur"""
        sections = []
        pages_per_section = max(5, total_pages // 5)  # Yaklaşık 5 bölüm
        
        current_page = 1
        while current_page <= total_pages:
            end_page = min(current_page + pages_per_section - 1, total_pages)
            sections.append({
                'start_page': current_page,
                'end_page': end_page,
                'reason': f'Bölüm {len(sections) + 1}'
            })
            current_page = end_page + 1
        
        return sections
    
    def test_connection(self) -> bool:
        """API bağlantısını test eder"""
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": "Merhaba, bağlantı testi."}],
                max_tokens=10,
                temperature=0
            )
            return True
        except Exception as e:
            print(f"DeepSeek bağlantı hatası: {str(e)}")
            return False
