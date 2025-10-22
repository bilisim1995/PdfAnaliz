# PDF RAG Bölümlendirme Aracı

## Overview

This is a Streamlit-based web application that processes PDF documents and segments them into optimized chunks for RAG (Retrieval Augmented Generation) systems. The application analyzes PDF content using DeepSeek AI to generate metadata including titles, descriptions, and keywords for each section. Users can upload PDFs from their computer or download them from URLs, and the system automatically creates intelligent document sections with AI-generated metadata to improve retrieval performance in RAG applications.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture

**Decision**: Streamlit web framework  
**Rationale**: Provides rapid development of data-centric applications with minimal frontend code. Streamlit's session state management handles processing workflows and maintains user context across interactions.

**Key Components**:
- Session state management for tracking processing status, JSON output, and output directories
- File upload interface supporting both local files and URL-based downloads
- Sidebar configuration panel for API keys and processing parameters

### Backend Architecture

**Decision**: Modular Python architecture with separation of concerns  
**Rationale**: Each major functionality is isolated into dedicated modules for maintainability and testability.

**Core Modules**:

1. **PDFProcessor** (`pdf_processor.py`)
   - Handles PDF structure analysis and page counting
   - Creates optimal document sections based on configurable min/max page parameters
   - Uses pypdf library for PDF manipulation
   - Extracts sample text from initial pages for structure analysis

2. **DeepSeekAnalyzer** (`deepseek_analyzer.py`)
   - Integrates with DeepSeek AI API via OpenAI client interface
   - Generates metadata (title, description, keywords) for PDF sections
   - Implements content length limiting (8000 characters) for token management
   - Handles edge cases like insufficient text content
   - Uses Turkish language for metadata generation

3. **Utils** (`utils.py`)
   - PDF download functionality from URLs with validation
   - Content-type verification and PDF magic number checking
   - Temporary file management with unique UUID-based naming
   - HTTP request handling with proper headers and timeout configurations

### Data Processing Flow

**Decision**: Sequential processing pipeline  
**Rationale**: Ensures data integrity and allows for error handling at each stage.

**Pipeline Stages**:
1. PDF acquisition (upload or URL download)
2. Structure analysis (page count, text extraction)
3. Section creation based on page ranges
4. AI-powered metadata generation per section
5. JSON output generation with structured metadata

### AI Integration

**Decision**: DeepSeek API for content analysis  
**Rationale**: Provides cost-effective, high-quality Turkish language support for metadata generation.

**Integration Details**:
- Uses OpenAI-compatible client interface
- Custom base URL pointing to DeepSeek API
- Structured prompt engineering for consistent metadata format
- Token optimization through content truncation
- Graceful degradation for sections with insufficient content

### Error Handling Strategy

**Decision**: Defensive programming with explicit error messages  
**Rationale**: Provides clear feedback for debugging and user guidance.

**Error Handling Patterns**:
- URL validation and content-type checking before processing
- PDF magic number verification for downloaded files
- File size validation (minimum 1KB)
- Try-catch blocks with descriptive error messages
- Fallback metadata for empty or invalid sections

### File Management

**Decision**: Temporary file storage with UUID-based naming  
**Rationale**: Prevents naming conflicts and automatic cleanup via OS temp directory management.

**Implementation**:
- Uses Python's tempfile module for secure temporary storage
- UUID hex strings for unique file identification
- No persistent storage requirement reduces infrastructure complexity

## External Dependencies

### AI Services
- **DeepSeek API**: Primary AI service for content analysis and metadata generation
  - OpenAI-compatible API interface
  - Base URL: https://api.deepseek.com
  - Requires API key authentication (default configured in environment)

### Python Libraries
- **Streamlit**: Web application framework for UI and interaction flow
- **openai**: Client library for DeepSeek API integration
- **pypdf**: PDF parsing and text extraction
- **requests**: HTTP client for URL-based PDF downloads

### External Integrations
- PDF downloads from arbitrary URLs with User-Agent spoofing
- HTTP request handling with 30-second timeout
- Content validation through headers and magic number verification

### Configuration
- Environment variable support for `DEEPSEEK_API_KEY`
- Hardcoded fallback API key for development convenience
- Configurable section parameters (min/max pages per section)