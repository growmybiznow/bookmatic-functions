import os
import json
import boto3
import fitz  # PyMuPDF
import openai
from flask import Flask, request

app = Flask(__name__)

# Configura las variables de entorno
openai.api_key = os.getenv("OPENAI_API_KEY")
s3 = boto3.client(
    's3',
    endpoint_url="https://<tu-endpoint-R2>",
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY")
)

BUCKET_NAME = "bookmatic"

@app.route("/", methods=["POST"])
def handle_upload():
    data = request.get_json()
    file_path = data.get("file_path")

    # Descargar PDF desde R2
    local_file = "/tmp/book.pdf"
    s3.download_file(BUCKET_NAME, file_path, local_file)

    # Extraer primera página como JPG
    doc = fitz.open(local_file)
    page = doc.load_page(0)
    pix = page.get_pixmap()
    image_path = "/tmp/cover.jpg"
    pix.save(image_path)

    # Subir imagen extraída a misma carpeta
    cover_key = os.path.dirname(file_path) + "/cover.jpg"
    s3.upload_file(image_path, BUCKET_NAME, cover_key, ExtraArgs={'ContentType': 'image/jpeg'})

    # Extraer contenido del PDF (páginas 2-6)
    extracted_text = ""
    for i in range(1, min(6, len(doc))):
        extracted_text += doc.load_page(i).get_text()

    # Obtener metadatos usando OpenAI
    prompt = f"""Given the following text from a business ebook, return:
1. A clean title
2. A 3-line summary
3. A specific category (like Business/Marketing)
4. An index of the book (if available)
5. A Reddit-style promotional post
Respond in JSON.

Text:
{extracted_text[:4000]}"""

    completion = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "