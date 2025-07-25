from flask import Flask, request, jsonify
import os
import re
import json
import boto3
import fitz  # PyMuPDF
import openai

# ---------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------
app = Flask(__name__)

# Claves de entorno
openai.api_key = os.getenv("OPENAI_API_KEY")
R2_ENDPOINT = "https://a5cb606556773f146432977936bbfb21.r2.cloudflarestorage.com"  # <---- CAMBIA ESTO
BUCKET_NAME = "bookmatic"

# Cliente S3 para Cloudflare R2
s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
)

# ---------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------

def clean_filename(text):
    """Limpia el nombre del archivo para nombres seguros"""
    return re.sub(r'[^a-zA-Z0-9]+', '_', text.lower()).strip('_')

def extract_pdf_text_and_cover(local_file):
    """Extrae texto y crea una portada JPG"""
    doc = fitz.open(local_file)

    # Guardar la portada
    cover_image = doc[0].get_pixmap(dpi=150)
    cover_path = local_file.replace(".pdf", "_cover.jpg")
    cover_image.save(cover_path)

    # Extraer texto de páginas 2 a 6
    extracted_text = ""
    for i in range(1, min(6, len(doc))):
        extracted_text += doc[i].get_text()
    doc.close()
    return extracted_text, cover_path

def get_book_metadata(extracted_text):
    """Pide a OpenAI los metadatos del libro"""
    prompt = f"""
Analyze this text and return JSON with:
- clean_title
- full_title
- summary (3 lines)
- category (Business/Marketing/etc.)
- index (main sections)
- reddit_post (short promotional text)

Text:
{extracted_text[:3500]}
"""
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    raw_content = response.choices[0].message["content"]

    # Intentar parsear como JSON
    try:
        return json.loads(raw_content)
    except:
        return {"error": "Could not parse JSON", "raw": raw_content}

# ---------------------------------------------------------
# RUTAS
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "Bookmatic API is running", 200

@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    """
    Recibe:
    {
        "pdf_key": "Business/Marketing/mi_libro.pdf"
    }
    """
    data = request.get_json()
    pdf_key = data.get("pdf_key")

    if not pdf_key:
        return jsonify({"error": "pdf_key is required"}), 400

    # Descargar archivo desde R2
    local_file = "/tmp/book.pdf"
    s3.download_file(BUCKET_NAME, pdf_key, local_file)

    # Extraer contenido
    extracted_text, cover_path = extract_pdf_text_and_cover(local_file)
    metadata = get_book_metadata(extracted_text)

    # Subir la portada al mismo directorio en R2
    cover_key = pdf_key.replace(".pdf", "/cover.jpg")
    with open(cover_path, "rb") as f:
        s3.upload_fileobj(f, BUCKET_NAME, cover_key)

    return jsonify({
        "file": pdf_key,
        "cover_image_uploaded_to": cover_key,
        "metadata": metadata
    })

# ---------------------------------------------------------
# ARRANQUE
# ---------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)