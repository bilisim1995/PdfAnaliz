import streamlit as st
import os
import tempfile
from pathlib import Path
import json
from pdf_processor import PDFProcessor
from deepseek_analyzer import DeepSeekAnalyzer
from utils import download_pdf_from_url, create_output_directories

def main():
    st.title("📄 PDF RAG Bölümlendirme Aracı")
    st.markdown("PDF dosyalarınızı RAG için optimize edilmiş bölümlere ayırın ve AI ile analiz edin.")
    
    # Initialize session state
    if 'processing_complete' not in st.session_state:
        st.session_state.processing_complete = False
    if 'json_output' not in st.session_state:
        st.session_state.json_output = ""
    if 'output_dir' not in st.session_state:
        st.session_state.output_dir = ""
    
    # Sidebar for configuration
    st.sidebar.header("⚙️ Ayarlar")
    
    # DeepSeek API Key
    api_key = os.getenv("DEEPSEEK_API_KEY", "sk-8c15dc40c6b44cde9880f7a47b4be333")
    
    # PDF source selection
    st.header("1️⃣ PDF Kaynağını Seçin")
    source_option = st.radio(
        "PDF kaynağınızı seçin:",
        ["💻 Bilgisayardan dosya yükle", "🌐 URL'den indir"]
    )
    
    pdf_file = None
    pdf_path = None
    
    if source_option == "💻 Bilgisayardan dosya yükle":
        uploaded_file = st.file_uploader(
            "PDF dosyanızı seçin:",
            type=['pdf'],
            help="RAG için bölümlendirilecek PDF dosyanızı yükleyin"
        )
        if uploaded_file is not None:
            # Save uploaded file to temporary location
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(uploaded_file.getbuffer())
                pdf_path = tmp_file.name
            st.success(f"✅ Dosya yüklendi: {uploaded_file.name}")
    
    elif source_option == "🌐 URL'den indir":
        url_input = st.text_input(
            "PDF URL'sini girin:",
            placeholder="https://example.com/document.pdf",
            help="İndirilecek PDF dosyasının URL'sini girin"
        )
        
        if url_input:
            if st.button("📥 PDF'i İndir"):
                with st.spinner("PDF indiriliyor..."):
                    try:
                        pdf_path = download_pdf_from_url(url_input)
                        st.success("✅ PDF başarıyla indirildi!")
                    except Exception as e:
                        st.error(f"❌ PDF indirme hatası: {str(e)}")
                        pdf_path = None
    
    # Processing section
    if pdf_path:
        st.header("2️⃣ PDF İşleme Ayarları")
        
        col1, col2 = st.columns(2)
        with col1:
            min_pages_per_section = st.number_input(
                "Minimum sayfa/bölüm:",
                min_value=1,
                max_value=10,
                value=1,
                help="Her bölümde minimum sayfa sayısı"
            )
        
        with col2:
            max_pages_per_section = st.number_input(
                "Maximum sayfa/bölüm:",
                min_value=2,
                max_value=30,
                value=5,
                help="Her bölümde maximum sayfa sayısı"
            )
        
        # Process PDF button
        if st.button("🚀 PDF'i İşle ve Analiz Et", type="primary"):
            if min_pages_per_section >= max_pages_per_section:
                st.error("❌ Minimum sayfa sayısı, maximum sayfa sayısından küçük olmalıdır!")
            else:
                process_pdf(pdf_path, api_key, min_pages_per_section, max_pages_per_section)
    
    # Results section
    if st.session_state.processing_complete:
        st.header("3️⃣ İşlem Sonuçları")
        
        # Display JSON output
        st.subheader("📊 Bölüm Metadata (JSON)")
        st.text_area(
            "JSON Çıktısı:",
            value=st.session_state.json_output,
            height=400,
            help="Oluşturulan bölümler ve metadata bilgileri"
        )
        
        # Download JSON button
        if st.session_state.json_output:
            st.download_button(
                label="💾 JSON'u İndir",
                data=st.session_state.json_output,
                file_name="pdf_sections_metadata.json",
                mime="application/json"
            )
        
        # Show output directory
        if st.session_state.output_dir:
            st.info(f"📁 Bölümlenmiş PDF dosyaları şurada kaydedildi: `{st.session_state.output_dir}`")
        
        # Reset button
        if st.button("🔄 Yeni İşlem"):
            reset_session_state()
            st.rerun()

def process_pdf(pdf_path, api_key, min_pages, max_pages):
    """Process PDF file and create sections"""
    try:
        # Create progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Step 1: Initialize components
        status_text.text("🔧 Bileşenler başlatılıyor...")
        progress_bar.progress(10)
        
        processor = PDFProcessor()
        analyzer = DeepSeekAnalyzer(api_key)
        
        # Step 2: Create output directories
        status_text.text("📁 Çıktı klasörleri oluşturuluyor...")
        progress_bar.progress(20)
        
        output_dir = create_output_directories()
        st.session_state.output_dir = output_dir
        
        # Step 3: Analyze PDF structure
        status_text.text("📖 PDF yapısı analiz ediliyor...")
        progress_bar.progress(30)
        
        pdf_info = processor.analyze_pdf_structure(pdf_path)
        st.info(f"📄 PDF Bilgisi: {pdf_info['total_pages']} sayfa tespit edildi")
        
        # Step 4: Create optimal sections
        status_text.text("✂️ Optimal bölümler oluşturuluyor...")
        progress_bar.progress(50)
        
        sections = processor.create_optimal_sections(
            pdf_path, 
            pdf_info['total_pages'], 
            min_pages, 
            max_pages
        )
        
        st.info(f"📝 {len(sections)} bölüm oluşturuldu")
        
        # Step 5: Generate section files and analyze content
        status_text.text("🤖 AI ile içerik analiz ediliyor...")
        progress_bar.progress(70)
        
        metadata_list = []
        
        for i, section in enumerate(sections):
            # Create section PDF
            section_path = processor.create_section_pdf(
                pdf_path, 
                section['start_page'], 
                section['end_page'], 
                output_dir, 
                i + 1
            )
            
            # Extract text for analysis
            section_text = processor.extract_text_from_pages(
                pdf_path, 
                section['start_page'], 
                section['end_page']
            )
            
            # Analyze with DeepSeek
            if section_text.strip():  # Only analyze if there's actual text
                analysis = analyzer.analyze_section_content(section_text)
                
                metadata = {
                    "output_filename": Path(section_path).name,
                    "start_page": section['start_page'],
                    "end_page": section['end_page'],
                    "title": analysis.get('title', f'Bölüm {i + 1}'),
                    "description": analysis.get('description', 'Bu bölüm için açıklama oluşturulamadı.'),
                    "keywords": analysis.get('keywords', f'bölüm_{i + 1}')
                }
            else:
                # Fallback for sections with no extractable text
                metadata = {
                    "output_filename": Path(section_path).name,
                    "start_page": section['start_page'],
                    "end_page": section['end_page'],
                    "title": f"Bölüm {i + 1}",
                    "description": "Bu bölümde metin içeriği tespit edilemedi. Görsel içerik veya tablo bulunuyor olabilir.",
                    "keywords": f"bölüm_{i + 1},görsel_içerik"
                }
            
            metadata_list.append(metadata)
            
            # Update progress
            section_progress = 70 + (i + 1) / len(sections) * 20
            progress_bar.progress(int(section_progress))
            status_text.text(f"🤖 Bölüm {i + 1}/{len(sections)} analiz edildi...")
        
        # Step 6: Generate final JSON
        status_text.text("📄 JSON çıktısı oluşturuluyor...")
        progress_bar.progress(95)
        
        final_json = {
            "pdf_sections": metadata_list
        }
        
        json_output = json.dumps(final_json, ensure_ascii=False, indent=2)
        st.session_state.json_output = json_output
        
        # Save JSON to file
        json_path = Path(output_dir) / "pdf_sections_metadata.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            f.write(json_output)
        
        # Complete
        progress_bar.progress(100)
        status_text.text("✅ İşlem tamamlandı!")
        st.session_state.processing_complete = True
        
        st.success(f"🎉 İşlem başarıyla tamamlandı! {len(sections)} bölüm oluşturuldu.")
        
    except Exception as e:
        st.error(f"❌ İşlem sırasında hata oluştu: {str(e)}")
        st.exception(e)

def reset_session_state():
    """Reset all session state variables"""
    st.session_state.processing_complete = False
    st.session_state.json_output = ""
    st.session_state.output_dir = ""

if __name__ == "__main__":
    main()
