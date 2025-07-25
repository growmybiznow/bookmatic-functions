import os
import json
import re
import boto3
import fitz  # PyMuPDF
import openai
from flask import Flask, request

# Configurar Flask
app = Flask(__name__)

# Configurar claves y cliente R2
openai.api_key = os.getenv("OPENAI_API_KEY")
s3 = boto3.client(
    's3',
    endpoint_url="https://<TU_ENDPOINT_R2>",  # Cambia por tu endpoint R2
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY")
)
BUCKET_NAME = "bookmatic"

# ---------- UTILIDADES ----------

def clean_filename(text):
    """Limpia el nombre del archivo para que sea seguro"""
    text = re.sub(r'[^a-zA-Z0-9]+', '_', text.lower())
    return text.strip('_')


def extract_pdf_text_and_cover(local_file):
    """Extraer texto y generar portada JPG de un PDF"""
    doc = fitz.open(local_file)

    # Guardar portada
    cover_image = doc[0].get_pixmap(dpi=150)
    cover_path = local_file.replace('.pdf', '_cover.jpg')
    cover_image.save(cover_path)

    # Extraer texto de páginas 2 a 6
    extracted_text = ""
    for i in range(1, min(6, len(doc))):
        extracted_text += doc[i].get_text()
    doc.close()
    return extracted_text, cover_path


def get_book_metadata(extracted_text):
    """Usar OpenAI para generar metadatos"""
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
    return response.choices[0].message["content"]


# ---------- RUTAS ----------

@app.route("/", methods=["GET"])
def index():
    """Ruta principal para verificar que el servicio funciona"""
    return "Bookmatic API is running", 200


# Aquí en el futuro añadiremos rutas POST para subir y procesar PDFs


# ---------- EJECUCIÓN ----------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)