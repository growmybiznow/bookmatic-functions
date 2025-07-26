from flask import Flask, request, jsonify
import os
import re
import json
import traceback
import boto3
import fitz  # PyMuPDF
import openai

# -------------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------------
app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")

R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_KEY = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET_NAME = "bookmatic"

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_KEY,
    aws_secret_access_key=R2_SECRET,
)

# -------------------------------------------------------------------
# FUNCIONES DE UTILIDAD
# -------------------------------------------------------------------

def clean_filename(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]+', '_', text.lower()).strip('_')

def extract_pdf_text_and_cover(local_file):
    """Extraer texto (páginas 2-6) y generar imagen JPG de la portada."""
    doc = fitz.open(local_file)
    cover_image = doc[0].get_pixmap(dpi=150)
    cover_path = local_file.replace(".pdf", "_cover.jpg")
    cover_image.save(cover_path)
    extracted_text = ""
    for i in range(1, min(6, len(doc))):
        extracted_text += doc[i].get_text()
    doc.close()
    return extracted_text, cover_path

def get_book_metadata(extracted_text):
    """Pedir a OpenAI que genere metadatos estructurados en JSON."""
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
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.choices[0].message.content
    try:
        return json.loads(raw_text)
    except Exception:
        return {"raw_text": raw_text}

def files_already_processed(folder):
    """Verifica si cover.jpg y metadata.json ya existen en la carpeta."""
    existing = set()
    resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=f"{folder}/")
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.endswith("cover.jpg"):
            existing.add("cover")
        elif key.endswith("metadata.json"):
            existing.add("metadata")
    return "cover" in existing and "metadata" in existing

# -------------------------------------------------------------------
# RUTAS
# -------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "Bookmatic API is running", 200

@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    try:
        data = request.get_json()
        pdf_key = data.get("pdf_key")
        if not pdf_key:
            return jsonify({"error": "pdf_key is required"}), 400

        # Carpeta y nombres base
        folder, filename = pdf_key.rsplit('/', 1)
        cover_key = f"{folder}/cover.jpg"
        meta_key = f"{folder}/metadata.json"

        # Verificar si ya está procesado
        if files_already_processed(folder):
            return jsonify({
                "file": pdf_key,
                "status": "already_processed",
                "cover_image": cover_key,
                "metadata_json": meta_key
            })

        # Descargar PDF desde R2
        local_pdf = "/tmp/book.pdf"
        s3.download_file(BUCKET_NAME, pdf_key, local_pdf)

        # Procesar PDF
        extracted_text, cover_path = extract_pdf_text_and_cover(local_pdf)
        metadata = get_book_metadata(extracted_text)

        # Subir cover
        with open(cover_path, "rb") as f:
            s3.upload_fileobj(f, BUCKET_NAME, cover_key)

        # Subir metadata
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=meta_key,
            Body=json.dumps(metadata, indent=2),
            ContentType="application/json"
        )

        return jsonify({
            "file": pdf_key,
            "status": "processed",
            "cover_image_uploaded_to": cover_key,
            "metadata_json_uploaded_to": meta_key,
            "metadata": metadata
        })

    except Exception as e:
        print("ERROR:", e)
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

# -------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)