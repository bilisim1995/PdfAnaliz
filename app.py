import streamlit as st
import os
import tempfile
from pathlib import Path
import json
import shutil
from pdf_processor import PDFProcessor
from deepseek_analyzer import DeepSeekAnalyzer
from utils import download_pdf_from_url, create_output_directories, create_pdf_filename

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
    if 'sections' not in st.session_state:
        st.session_state.sections = []
    if 'analysis_complete' not in st.session_state:
        st.session_state.analysis_complete = False
    if 'pdf_path_temp' not in st.session_state:
        st.session_state.pdf_path_temp = ""
    if 'pdf_base_name' not in st.session_state:
        st.session_state.pdf_base_name = ""
    if 'metadata_list' not in st.session_state:
        st.session_state.metadata_list = []
    
    # Login session state
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'access_token' not in st.session_state:
        st.session_state.access_token = ""
    if 'refresh_token' not in st.session_state:
        st.session_state.refresh_token = ""
    if 'user_info' not in st.session_state:
        st.session_state.user_info = {}
    if 'api_base_url' not in st.session_state:
        st.session_state.api_base_url = ""
    
    # Document name suggestion
    if 'suggested_doc_name' not in st.session_state:
        st.session_state.suggested_doc_name = ""
    if 'pdf_page_count' not in st.session_state:
        st.session_state.pdf_page_count = 0
    
    # Check login status
    if not st.session_state.logged_in:
        show_login_page()
        return
    
    # Sidebar for logged-in users
    st.sidebar.header("⚙️ Ayarlar")
    
    # Show user info
    if st.session_state.user_info:
        st.sidebar.success(f"👤 Giriş yapıldı: {st.session_state.user_info.get('email', 'Kullanıcı')}")
        st.sidebar.caption(f"Rol: {st.session_state.user_info.get('role', 'N/A')}")
    
    # Logout button
    if st.sidebar.button("🚪 Çıkış Yap", type="secondary", use_container_width=True):
        logout()
        st.rerun()
    
    # DeepSeek API Key
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    
    # API Upload Configuration
    st.sidebar.divider()
    st.sidebar.subheader("📤 Veri Yükleme Ayarları")
    
    category = st.sidebar.text_input(
        "Kategori:",
        value="mevzuat",
        help="Doküman kategorisi (örn: mevzuat, kanun)"
    )
    
    institution = st.sidebar.text_input(
        "Kurum:",
        value="",
        help="Kurum adı (örn: TBB, Adalet Bakanlığı)"
    )
    
    # Initialize belge_adi from session state if exists
    if 'belge_adi_value' not in st.session_state:
        st.session_state.belge_adi_value = ""
    
    belge_adi = st.sidebar.text_input(
        "Belge Adı:",
        value=st.session_state.belge_adi_value,
        help="Belge adı (örn: TCK_2024)",
        key="belge_adi_input"
    )
    
    # Update session state
    st.session_state.belge_adi_value = belge_adi
    
    # Show document name suggestion if available
    if st.session_state.suggested_doc_name:
        st.sidebar.caption(f"💡 Öneri: **{st.session_state.suggested_doc_name}**")
        if st.sidebar.button("✅ Öneriyi Uygula", key="apply_suggestion", use_container_width=True):
            st.session_state.belge_adi_value = st.session_state.suggested_doc_name
            st.rerun()
    
    # PDF source selection
    st.header("1️⃣ PDF Kaynağını Seçin")
    source_option = st.radio(
        "PDF kaynağınızı seçin:",
        ["💻 Bilgisayardan dosya yükle", "🌐 URL'den indir"]
    )
    
    pdf_file = None
    pdf_path = None
    uploaded_file = None
    
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
        
        # Bölümleme stratejisi seçimi
        sectioning_mode = st.radio(
            "Bölümleme Stratejisi:",
            ["🤖 Akıllı Bölümleme (AI bazlı, içeriğe göre)", "📏 Manuel Bölümleme (sabit sayfa aralığı)"],
            help="Akıllı bölümleme: AI, PDF içeriğini analiz ederek en mantıklı bölümleri oluşturur. Manuel bölümleme: Sayfa sayısına göre eşit bölümler oluşturur."
        )
        
        min_pages_per_section = 1
        max_pages_per_section = 30
        
        if sectioning_mode == "📏 Manuel Bölümleme (sabit sayfa aralığı)":
            col1, col2 = st.columns(2)
            with col1:
                min_pages_per_section = st.number_input(
                    "Minimum sayfa/bölüm:",
                    min_value=1,
                    max_value=10,
                    value=3,
                    help="Her bölümde minimum sayfa sayısı"
                )
            
            with col2:
                max_pages_per_section = st.number_input(
                    "Maximum sayfa/bölüm:",
                    min_value=2,
                    max_value=30,
                    value=10,
                    help="Her bölümde maximum sayfa sayısı"
                )
        else:
            st.info("🤖 AI, PDF içeriğini analiz ederek en uygun bölümleme stratejisini belirleyecek. Bu işlem biraz daha uzun sürebilir.")
            
            # API key kontrolü
            if not api_key or api_key == "":
                st.warning("⚠️ Akıllı bölümleme için DeepSeek API anahtarı gereklidir. Lütfen önce API anahtarınızı girin.")
        
        # Process PDF button
        if st.button("🔍 PDF'i Analiz Et (1. Adım)", type="primary"):
            if sectioning_mode == "📏 Manuel Bölümleme (sabit sayfa aralığı)" and min_pages_per_section >= max_pages_per_section:
                st.error("❌ Minimum sayfa sayısı, maximum sayfa sayısından küçük olmalıdır!")
            else:
                # PDF dosya adını kaydet
                if source_option == "💻 Bilgisayardan dosya yükle" and uploaded_file:
                    st.session_state.pdf_base_name = Path(uploaded_file.name).stem
                else:
                    st.session_state.pdf_base_name = "document"
                
                analyze_and_prepare(pdf_path, api_key, sectioning_mode, min_pages_per_section, max_pages_per_section)
    
    # Analysis results section
    if st.session_state.analysis_complete and not st.session_state.processing_complete:
        st.header("✅ Analiz Tamamlandı!")
        st.success("PDF başarıyla analiz edildi. Aşağıda oluşturulacak bölümlerin JSON önizlemesini görebilirsiniz.")
        
        # Display JSON output
        st.subheader("📊 JSON Önizleme - Oluşturulacak Bölümler")
        st.text_area(
            "JSON Çıktısı:",
            value=st.session_state.json_output,
            height=400,
            help="PDF parçalandığında bu yapıda bölümler oluşturulacak",
            key="json_preview"
        )
        
        # Split PDF button - make it more prominent
        st.divider()
        st.info("👇 JSON'u inceledikten sonra, PDF'leri bölmek için aşağıdaki butona tıklayın:")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("✂️ PDF'leri Şimdi Böl (2. Adım)", type="primary", use_container_width=True, help="JSON'daki sayfa aralıklarına göre PDF'leri hızlıca böler ve kaydeder"):
                split_pdf_files()
        
        col4, col5 = st.columns([4, 1])
        with col5:
            if st.button("🔄 İptal", help="Analizi iptal et ve başa dön"):
                reset_and_cleanup()
                st.rerun()
    
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
        
        # Upload to API button
        st.divider()
        st.subheader("📤 Verileri API'ye Yükle")
        
        # Validate API configuration
        upload_ready = all([category, institution, belge_adi])
        
        if not upload_ready:
            st.warning("⚠️ Veri yüklemek için lütfen sol taraftaki tüm API ayarlarını doldurun (Kategori, Kurum, Belge Adı)")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button(
                "📤 Verileri Yükle", 
                type="primary", 
                use_container_width=True,
                disabled=not upload_ready,
                help="Bölümlenmiş PDF'leri ve metadata'yı API'ye yükler"
            ):
                upload_to_api(category, institution, belge_adi)
        
        # Reset button
        st.divider()
        col1, col2 = st.columns([3, 1])
        with col2:
            if st.button("🗑️ Verileri Sıfırla", type="secondary", help="Tüm işlemi sıfırlar, dosyaları siler ve uygulamayı yeniden başlatır"):
                reset_and_cleanup()
                st.rerun()

def analyze_and_prepare(pdf_path, api_key, sectioning_mode, min_pages, max_pages):
    """Analyze PDF and prepare metadata without splitting files"""
    try:
        # Clean up any existing output directory from previous analysis
        # (Users starting a new analysis should download previous results first if needed)
        if st.session_state.output_dir and os.path.exists(st.session_state.output_dir):
            try:
                shutil.rmtree(st.session_state.output_dir)
            except Exception:
                pass  # Ignore cleanup errors
        
        # Reset state for new analysis
        st.session_state.processing_complete = False
        st.session_state.analysis_complete = False
        st.session_state.json_output = ""
        st.session_state.output_dir = ""
        
        # PDF yolunu kaydet
        st.session_state.pdf_path_temp = pdf_path
        # Create progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Step 1: Initialize components
        status_text.text("🔧 Bileşenler başlatılıyor...")
        progress_bar.progress(10)
        
        processor = PDFProcessor()
        analyzer = DeepSeekAnalyzer(api_key)
        
        # Step 2: Analyze PDF structure
        status_text.text("📖 PDF yapısı analiz ediliyor...")
        progress_bar.progress(20)
        
        pdf_info = processor.analyze_pdf_structure(pdf_path)
        total_pages = pdf_info['total_pages']
        
        # Store page count in session state
        st.session_state.pdf_page_count = total_pages
        
        st.info(f"📄 PDF Bilgisi: {total_pages} sayfa tespit edildi")
        
        # Check if PDF has very few pages
        if total_pages <= 5:
            st.warning(f"⚠️ Bu PDF sadece {total_pages} sayfa içeriyor. Parçalama yapmadan doğrudan kullanabilirsiniz.")
            st.info("💡 Yine de devam edebilir veya tüm PDF'i tek dosya olarak yükleyebilirsiniz.")
        
        # Step 2.5: Get document name suggestion
        status_text.text("💡 Belge adı önerisi oluşturuluyor...")
        try:
            # İlk 3 sayfadan örnek metin al
            sample_text = processor.extract_text_from_pages(pdf_path, 1, min(3, total_pages))
            
            # Belge adı önerisi al
            if sample_text.strip():
                suggested_name = analyzer.suggest_document_name(sample_text)
                st.session_state.suggested_doc_name = suggested_name
                st.success(f"💡 Belge adı önerisi: **{suggested_name}**")
        except Exception as e:
            print(f"Belge adı önerisi hatası: {str(e)}")
            st.session_state.suggested_doc_name = ""
        
        progress_bar.progress(25)
        
        # Step 3: Create optimal sections
        if sectioning_mode == "🤖 Akıllı Bölümleme (AI bazlı, içeriğe göre)":
            status_text.text("🤖 AI ile içerik bazlı bölümler oluşturuluyor...")
            progress_bar.progress(30)
            
            try:
                sections = processor.create_intelligent_sections(
                    pdf_path, 
                    pdf_info['total_pages'], 
                    analyzer
                )
                
                # Bölüm nedenlerini göster
                st.success(f"🤖 AI {len(sections)} anlamlı bölüm oluşturdu")
                with st.expander("📋 Bölümleme Detayları"):
                    for i, section in enumerate(sections):
                        st.write(f"**Bölüm {i+1}:** Sayfa {section['start_page']}-{section['end_page']}")
                        if section.get('reason'):
                            st.write(f"   └─ *{section['reason']}*")
            except Exception as e:
                st.warning(f"⚠️ AI bölümleme başarısız oldu: {str(e)}")
                st.info("📏 Otomatik olarak manuel bölümleme moduna geçiliyor...")
                
                # Fallback: Manuel bölümleme
                sections = processor.create_optimal_sections(
                    pdf_path, 
                    pdf_info['total_pages'], 
                    3,  # Default min pages
                    10  # Default max pages
                )
        else:
            status_text.text("✂️ Manuel bölümler oluşturuluyor...")
            
            sections = processor.create_optimal_sections(
                pdf_path, 
                pdf_info['total_pages'], 
                min_pages, 
                max_pages
            )
        
        # Session state'e sections'ı kaydet
        st.session_state.sections = sections
        
        progress_bar.progress(40)
        
        if sectioning_mode != "🤖 Akıllı Bölümleme (AI bazlı, içeriğe göre)":
            st.info(f"📝 {len(sections)} bölüm oluşturuldu")
        
        # Step 4: Analyze content and prepare metadata (WITHOUT creating PDF files)
        status_text.text("🤖 AI ile içerik analiz ediliyor...")
        progress_bar.progress(60)
        
        metadata_list = []
        
        for i, section in enumerate(sections):
            # Extract text for analysis
            section_text = processor.extract_text_from_pages(
                pdf_path, 
                section['start_page'], 
                section['end_page']
            )
            
            # Analyze with DeepSeek
            if section_text.strip():  # Only analyze if there's actual text
                analysis = analyzer.analyze_section_content(section_text)
                
                # API hata kontrolü
                if 'API Analiz Hatası' in analysis.get('title', ''):
                    st.warning(f"⚠️ Bölüm {i + 1} için AI analizi başarısız oldu. Hata: {analysis.get('description', '')}")
                
                title = analysis.get('title', f'Bölüm {i + 1}')
                
                # Dosya adını oluştur (Türkçe karaktersiz)
                output_filename = create_pdf_filename(
                    st.session_state.pdf_base_name,
                    i + 1,
                    section['start_page'],
                    section['end_page'],
                    title
                )
                
                metadata = {
                    "output_filename": output_filename,
                    "start_page": section['start_page'],
                    "end_page": section['end_page'],
                    "title": title,
                    "description": analysis.get('description', 'Bu bölüm için açıklama oluşturulamadı.'),
                    "keywords": analysis.get('keywords', f'bölüm {i + 1}')
                }
            else:
                # Fallback for sections with no extractable text
                output_filename = create_pdf_filename(
                    st.session_state.pdf_base_name,
                    i + 1,
                    section['start_page'],
                    section['end_page'],
                    ""
                )
                
                metadata = {
                    "output_filename": output_filename,
                    "start_page": section['start_page'],
                    "end_page": section['end_page'],
                    "title": f"Bölüm {i + 1}",
                    "description": "Bu bölümde metin içeriği tespit edilemedi. Görsel içerik veya tablo bulunuyor olabilir.",
                    "keywords": f"bölüm {i + 1},görsel içerik"
                }
            
            metadata_list.append(metadata)
            
            # Update progress
            section_progress = 60 + (i + 1) / len(sections) * 25
            progress_bar.progress(int(section_progress))
            status_text.text(f"🤖 Bölüm {i + 1}/{len(sections)} analiz edildi...")
        
        # Save metadata list to session state
        st.session_state.metadata_list = metadata_list
        
        # Step 5: Generate final JSON
        status_text.text("📄 JSON çıktısı oluşturuluyor...")
        progress_bar.progress(90)
        
        final_json = {
            "pdf_sections": metadata_list
        }
        
        json_output = json.dumps(final_json, ensure_ascii=False, indent=2)
        st.session_state.json_output = json_output
        
        # Complete
        progress_bar.progress(100)
        status_text.text("✅ Analiz tamamlandı!")
        st.session_state.analysis_complete = True
        
        st.success(f"🎉 Analiz başarıyla tamamlandı! {len(sections)} bölüm için metadata oluşturuldu.")
        st.info("👇 Aşağıda JSON çıktısını inceleyebilir ve PDF'leri parçalayabilirsiniz.")
        
    except Exception as e:
        st.error(f"❌ İşlem sırasında hata oluştu: {str(e)}")
        st.exception(e)

def split_pdf_files():
    """Split PDF files according to prepared metadata"""
    try:
        # Create progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Get data from session state
        pdf_path = st.session_state.pdf_path_temp
        metadata_list = st.session_state.metadata_list
        sections = st.session_state.sections
        
        # Step 1: Create output directories (only if not already created)
        status_text.text("📁 Çıktı klasörleri hazırlanıyor...")
        progress_bar.progress(10)
        
        if not st.session_state.output_dir or not os.path.exists(st.session_state.output_dir):
            output_dir = create_output_directories()
            st.session_state.output_dir = output_dir
        else:
            output_dir = st.session_state.output_dir
        
        # Step 2: Split PDF files
        status_text.text("✂️ PDF dosyaları parçalanıyor...")
        progress_bar.progress(30)
        
        processor = PDFProcessor()
        
        for i, (section, metadata) in enumerate(zip(sections, metadata_list)):
            # Create section PDF with the specified filename
            output_path = Path(output_dir) / metadata['output_filename']
            
            # Create PDF using processor
            with open(pdf_path, 'rb') as source_file:
                import pypdf
                reader = pypdf.PdfReader(source_file)
                writer = pypdf.PdfWriter()
                
                # Add pages to writer
                for page_num in range(section['start_page'] - 1, section['end_page']):
                    if page_num < len(reader.pages):
                        writer.add_page(reader.pages[page_num])
                
                # Save PDF
                with open(output_path, 'wb') as output_file:
                    writer.write(output_file)
            
            # Update progress
            file_progress = 30 + (i + 1) / len(sections) * 60
            progress_bar.progress(int(file_progress))
            status_text.text(f"✂️ Bölüm {i + 1}/{len(sections)} oluşturuldu...")
        
        # Step 3: Save JSON to file
        status_text.text("💾 JSON dosyası kaydediliyor...")
        progress_bar.progress(95)
        
        json_path = Path(output_dir) / "pdf_sections_metadata.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            f.write(st.session_state.json_output)
        
        # Complete
        progress_bar.progress(100)
        status_text.text("✅ PDF parçalama tamamlandı!")
        st.session_state.processing_complete = True
        st.session_state.analysis_complete = False  # Analiz bölümünü gizle
        
        st.success(f"🎉 {len(sections)} PDF dosyası başarıyla oluşturuldu!")
        st.balloons()
        
    except Exception as e:
        st.error(f"❌ PDF parçalama sırasında hata oluştu: {str(e)}")
        st.exception(e)

def upload_to_api(category, institution, belge_adi):
    """Upload split PDFs and metadata to API endpoint"""
    try:
        import requests
        
        # Get API credentials from session state
        api_base_url = st.session_state.api_base_url
        access_token = st.session_state.access_token
        
        if not api_base_url or not access_token:
            st.error("❌ API bilgileri bulunamadı. Lütfen tekrar giriş yapın.")
            return
        
        with st.spinner("📤 Veriler API'ye yükleniyor..."):
            # Prepare metadata in the required format
            metadata_payload = {
                "pdf_sections": []
            }
            
            for item in st.session_state.metadata_list:
                section_data = {
                    "output_filename": item.get("output_filename", ""),
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "keywords": item.get("keywords", "")
                }
                metadata_payload["pdf_sections"].append(section_data)
            
            # Prepare files
            files_to_upload = []
            output_dir = Path(st.session_state.output_dir)
            
            # Collect all PDF files from output directory
            pdf_files = sorted(output_dir.glob("*.pdf"))
            
            if not pdf_files:
                st.error("❌ Yüklenecek PDF dosyası bulunamadı!")
                return
            
            # Open all PDF files
            file_handles = []
            for pdf_file in pdf_files:
                f = open(pdf_file, 'rb')
                file_handles.append(f)
                files_to_upload.append(('files', (pdf_file.name, f, 'application/pdf')))
            
            # Prepare form data
            form_data = {
                'category': category,
                'institution': institution,
                'belge_adi': belge_adi,
                'metadata': json.dumps(metadata_payload, ensure_ascii=False)
            }
            
            # Prepare headers
            headers = {
                'Authorization': f'Bearer {access_token}'
            }
            
            # Prepare API URL
            upload_url = f"{api_base_url.rstrip('/')}/api/admin/documents/bulk-upload"
            
            # Make API request
            try:
                response = requests.post(
                    upload_url,
                    headers=headers,
                    data=form_data,
                    files=files_to_upload,
                    timeout=300  # 5 minutes timeout
                )
                
                # Close all file handles
                for f in file_handles:
                    f.close()
                
                # Check response
                if response.status_code == 200:
                    result = response.json()
                    st.success("✅ Veriler başarıyla yüklendi!")
                    
                    # Display response
                    st.subheader("📊 API Yanıtı")
                    st.json(result)
                    
                    # Show batch ID if available
                    if 'data' in result and 'batch_id' in result['data']:
                        batch_id = result['data']['batch_id']
                        st.info(f"🆔 Batch ID: `{batch_id}`")
                        st.caption(f"Toplam {result['data'].get('total_files', 0)} dosya yüklendi.")
                    
                    st.balloons()
                    
                else:
                    st.error(f"❌ API Hatası: {response.status_code}")
                    st.code(response.text, language="json")
                    
            except requests.exceptions.Timeout:
                st.error("❌ İstek zaman aşımına uğradı. Lütfen tekrar deneyin.")
            except requests.exceptions.RequestException as e:
                st.error(f"❌ Bağlantı hatası: {str(e)}")
            finally:
                # Make sure all files are closed
                for f in file_handles:
                    try:
                        f.close()
                    except:
                        pass
                        
    except Exception as e:
        st.error(f"❌ Veri yükleme hatası: {str(e)}")
        st.exception(e)

def show_login_page():
    """Display login page"""
    st.header("🔐 Giriş Yap")
    st.markdown("PDF bölümlendirme aracını kullanmak için lütfen giriş yapın.")
    
    # Center the login form
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        with st.form("login_form"):
            api_base_url = st.text_input(
                "API Base URL:",
                value="https://api.example.com",
                help="API'nin temel adresi (örn: https://api.mevzuatgpt.org)",
                placeholder="https://api.mevzuatgpt.org"
            )
            
            email = st.text_input(
                "E-posta:",
                placeholder="admin@mevzuatgpt.org",
                help="Admin kullanıcı e-posta adresi"
            )
            
            password = st.text_input(
                "Şifre:",
                type="password",
                help="Kullanıcı şifresi"
            )
            
            submit = st.form_submit_button("🔓 Giriş Yap", type="primary", use_container_width=True)
            
            if submit:
                if not api_base_url or not email or not password:
                    st.error("❌ Lütfen tüm alanları doldurun!")
                else:
                    login(api_base_url, email, password)

def login(api_base_url, email, password):
    """Login to API and get access token"""
    import requests
    
    with st.spinner("🔄 Giriş yapılıyor..."):
        try:
            # Prepare login endpoint
            login_url = f"{api_base_url.rstrip('/')}/api/auth/login"
            
            # Prepare request body
            login_data = {
                "email": email,
                "password": password
            }
            
            # Make login request
            response = requests.post(
                login_url,
                headers={"Content-Type": "application/json"},
                json=login_data,
                timeout=60  # 1 dakika timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Save to session state
                st.session_state.logged_in = True
                st.session_state.access_token = result.get("access_token", "")
                st.session_state.refresh_token = result.get("refresh_token", "")
                st.session_state.user_info = result.get("user", {})
                st.session_state.api_base_url = api_base_url
                
                st.success("✅ Giriş başarılı!")
                st.balloons()
                st.rerun()
                
            elif response.status_code == 401:
                st.error("❌ Geçersiz e-posta veya şifre!")
            elif response.status_code == 403:
                error_data = response.json()
                st.error(f"❌ {error_data.get('message', 'Yetkiniz bulunmuyor!')}")
            else:
                st.error(f"❌ Giriş hatası: {response.status_code}")
                st.code(response.text, language="json")
                
        except requests.exceptions.RequestException as e:
            st.error(f"❌ Bağlantı hatası: {str(e)}")
        except Exception as e:
            st.error(f"❌ Giriş sırasında hata oluştu: {str(e)}")

def logout():
    """Logout and clear session"""
    st.session_state.logged_in = False
    st.session_state.access_token = ""
    st.session_state.refresh_token = ""
    st.session_state.user_info = {}
    st.session_state.api_base_url = ""
    
    # Also clear processing data
    st.session_state.processing_complete = False
    st.session_state.json_output = ""
    st.session_state.output_dir = ""
    st.session_state.sections = []
    st.session_state.analysis_complete = False
    st.session_state.pdf_path_temp = ""
    st.session_state.pdf_base_name = ""
    st.session_state.metadata_list = []
    st.session_state.suggested_doc_name = ""
    st.session_state.pdf_page_count = 0
    st.session_state.belge_adi_value = ""

def reset_and_cleanup():
    """Reset all session state and clean up files"""
    try:
        # Dosyaları ve klasörü sil
        if st.session_state.output_dir and os.path.exists(st.session_state.output_dir):
            shutil.rmtree(st.session_state.output_dir)
            print(f"Klasör silindi: {st.session_state.output_dir}")
    except Exception as e:
        print(f"Klasör silme hatası: {str(e)}")
    
    # Session state'i temizle
    st.session_state.processing_complete = False
    st.session_state.json_output = ""
    st.session_state.output_dir = ""
    st.session_state.sections = []
    st.session_state.analysis_complete = False
    st.session_state.pdf_path_temp = ""
    st.session_state.pdf_base_name = ""
    st.session_state.metadata_list = []
    st.session_state.suggested_doc_name = ""
    st.session_state.pdf_page_count = 0

if __name__ == "__main__":
    main()
