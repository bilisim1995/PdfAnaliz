import openai
import json
import re
from typing import Dict, Any

class DeepSeekAnalyzer:
    """DeepSeek AI ile PDF içerik analizi"""
    
    def __init__(self, api_key: str):
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )
    
    def analyze_section_content(self, text_content: str) -> Dict[str, Any]:
        """PDF bölüm içeriğini analiz ederek metadata oluşturur"""
        
        if not text_content or len(text_content.strip()) < 10:
            return {
                'title': 'İçerik Tespit Edilemedi',
                'description': 'Bu bölümde yeterli metin içeriği bulunamadı.',
                'keywords': 'içerik_yok'
            }
        
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
3. ANAHTAR KELİMELER: RAG sisteminde arama için kullanılacak anahtar kelimeler (virgülle ayrılmış, alt çizgi kullan, maksimum 15 kelime)

KURALLAR:
- Türkçe karakter kullan
- Anahtar kelimelerde boşluk yerine alt çizgi kullan (örn: "prim_borcu")
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
            error_msg = f"DeepSeek analiz hatası: {str(e)}"
            print(error_msg)
            # Hata durumunda fallback metadata
            return {
                'title': 'API Analiz Hatası',
                'description': f"Bu bölümün AI analizi yapılamadı. Hata: {str(e)}. İçerik yaklaşık {len(text_content)} karakter barındırmaktadır.",
                'keywords': 'api_hatası,analiz_yapılamadı'
            }
    
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
            keywords = "pdf_bölümü,doküman"
        else:
            # Anahtar kelimeleri işle
            keyword_list = [kw.strip().lower().replace(' ', '_') for kw in keywords.split(',')]
            keyword_list = [kw for kw in keyword_list if kw and len(kw) > 1][:15]  # Maksimum 15 kelime
            keywords = ','.join(keyword_list)
        
        cleaned['keywords'] = keywords
        
        return cleaned
    
    def _create_fallback_metadata(self, text_content: str) -> Dict[str, Any]:
        """Hata durumunda basit metadata oluşturur"""
        # İçeriğin ilk birkaç kelimesinden başlık oluştur
        words = text_content.strip().split()[:10]
        title = ' '.join(words) if words else "PDF Bölümü"
        if len(title) > 100:
            title = title[:97] + "..."
        
        # Basit anahtar kelimeler oluştur
        common_words = []
        for word in text_content.split():
            word = word.strip().lower()
            if len(word) > 3 and word.isalpha():
                word = word.replace(' ', '_')
                if word not in common_words:
                    common_words.append(word)
                if len(common_words) >= 5:
                    break
        
        keywords = ','.join(common_words) if common_words else "pdf_içerik,doküman"
        
        return {
            'title': title,
            'description': f"Bu bölüm yaklaşık {len(text_content)} karakter içerik barındırmaktadır. İçeriğin detaylı analizi yapılamadı.",
            'keywords': keywords
        }
    
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
