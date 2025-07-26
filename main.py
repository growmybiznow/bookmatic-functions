import os
import json
import re
import boto3
import fitz  # PyMuPDF
from flask import Flask, request, jsonify
from openai import OpenAI

# Inicializar Flask
app = Flask(__name__)

# Configuración de Cloudflare R2
R2_BUCKET = os.getenv("R2_BUCKET", "bookmatic")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY")

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

# Cliente OpenAI
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def clean_filename(text: str) -> str:
    """Limpia un texto para usarlo como nombre de archivo/carpeta"""
    text = re.sub(r'[^a-zA-Z0-9]+', '_', text.strip())
    return text.lower().strip('_')


def extract_pdf_text_and_cover(local_path):
    """Extraer texto (páginas 2 a 6) y generar portada JPG"""
    doc = fitz.open(local_path)

    # Guardar imagen portada
    cover_image = doc[0].get_pixmap(dpi=150)
    cover_path = local_path.replace(".pdf", "_cover.jpg")
    cover_image.save(cover_path)

    # Extraer texto de páginas 2 a 6
    extracted_text = ""
    for i in range(1, min(6, len(doc))):
        extracted_text += doc[i].get_text()
    doc.close()

    return extracted_text, cover_path


def analyze_with_openai(text: str) -> dict:
    """Llama a OpenAI y devuelve un JSON limpio"""
    prompt = f"""
Analyze this text and return ONLY a valid JSON object with these keys:
- clean_title
- full_title
- summary (3 descriptive lines)
- category (precise, with subcategory like Business/Entrepreneurship or Business/Marketing)
- key_ideas (5 bullet points with the most important concepts)
- target_audience (who is this book aimed at)
- index (main sections)
- reddit_post (short promotional text)

Text:
{text[:3500]}
"""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    raw_content = response.choices[0].message.content

    # Asegurar que el contenido es JSON válido
    try:
        start = raw_content.find('{')
        end = raw_content.rfind('}') + 1
        json_str = raw_content[start:end]
        data = json.loads(json_str)
    except Exception as e:
        # Si falla, guardar como texto plano
        data = {"raw_text": raw_content, "error": str(e)}

    return data


@app.route("/", methods=["GET"])
def home():
    return "Bookmatic Cloud Run is working!", 200


@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    data = request.get_json()
    pdf_key = data.get("pdf_key")

    if not pdf_key:
        return jsonify({"error": "pdf_key is required"}), 400

    # Paths en R2
    base_path = os.path.dirname(pdf_key)
    clean_base = clean_filename(os.path.splitext(os.path.basename(pdf_key))[0])

    # Cover ahora tiene el título en el nombre
    cover_key = f"{base_path}/{clean_base}/cover_{clean_base}.jpg"
    metadata_key = f"{base_path}/{clean_base}/metadata.json"

    # Si ya existe metadata, no reprocesar
    try:
        s3.head_object(Bucket=R2_BUCKET, Key=metadata_key)
        return jsonify({
            "file": pdf_key,
            "cover_image": cover_key,
            "metadata_json": metadata_key,
            "status": "already_processed"
        })
    except Exception:
        pass

    # Descargar PDF a /tmp
    tmp_pdf = f"/tmp/{os.path.basename(pdf_key)}"
    s3.download_file(R2_BUCKET, pdf_key, tmp_pdf)

    # Extraer texto y portada
    extracted_text, cover_path = extract_pdf_text_and_cover(tmp_pdf)

    # Generar metadatos enriquecidos
    metadata_json = analyze_with_openai(extracted_text)

    # Subir cover
    with open(cover_path, "rb") as f:
        s3.upload_fileobj(f, R2_BUCKET, cover_key)

    # Subir metadata.json limpio
    s3.put_object(
        Bucket=R2_BUCKET,
        Key=metadata_key,
        Body=json.dumps(metadata_json, indent=2),
        ContentType="application/json"
    )

    return jsonify({
        "file": pdf_key,
        "cover_image_uploaded_to": cover_key,
        "metadata_json_uploaded_to": metadata_key,
        "metadata": metadata_json
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)