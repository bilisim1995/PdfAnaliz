from pypdf import PdfWriter

# Create a simple blank PDF for testing
writer = PdfWriter()

# Add a single blank page
from pypdf.generic import PageObject
page = PageObject.create_blank_page(width=612, height=792)  # Letter size

# Add some pages
for i in range(5):
    writer.add_page(page)

# Save the PDF
with open("test_document.pdf", "wb") as output_file:
    writer.write(output_file)

print("Created test PDF: test_document.pdf with 5 pages")
