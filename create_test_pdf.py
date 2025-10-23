from pypdf import PdfWriter, PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import io

# Create a simple multi-page PDF for testing
def create_test_pdf(filename, num_pages=5):
    # Create a PDF with reportlab
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)
    
    for page_num in range(1, num_pages + 1):
        can.drawString(100, 750, f"Test PDF - Page {page_num}")
        can.drawString(100, 700, f"This is a test document for RAG segmentation.")
        can.drawString(100, 650, f"Content: Lorem ipsum dolor sit amet, consectetur adipiscing elit.")
        can.drawString(100, 600, f"This page contains some sample text to test the application.")
        can.drawString(100, 550, f"Page number: {page_num} of {num_pages}")
        can.showPage()
    
    can.save()
    
    # Save to file
    packet.seek(0)
    with open(filename, 'wb') as f:
        f.write(packet.read())
    
    print(f"Created test PDF: {filename}")

if __name__ == "__main__":
    create_test_pdf("test_sample.pdf", 5)
