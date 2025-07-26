import os
import re
import json
import boto3
import fitz  # PyMuPDF para PDFs
from flask import Flask, request
from openai import OpenAI
from PIL import Image, ImageDraw
import google.generativeai as genai

app = Flask(__name__)

# Configuración OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Configuración Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Configuración R2
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("R2_ENDPOINT"),
    aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
)
BUCKET_NAME = os.environ.get("R2_BUCKET", "bookmatic")


def clean_filename(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]+', '_', text).strip('_')


def refine_with_gemini(raw_text: str) -> str:
    """
    Usa Gemini para mejorar los metadatos generados por OpenAI:
    - Categoría más precisa
    - target_audience y key_ideas
    - Índice más detallado
    """
    if not GEMINI_API_KEY:
        return raw_text
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
Mejora este JSON de metadatos:
- Categoría más precisa (ej. Business/Marketing, Literature/Poetry)
- Agrega key_ideas (5 puntos)
- Agrega target_audience
- Devuelve solo JSON
{raw_text}
"""
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"[WARN] Gemini no disponible: {e}")
        return raw_text


@app.route("/", methods=["GET"])
def home():
    return "Bookmatic API is running", 200


@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    data = request.get_json()
    key = data.get("pdf_key")
    if not key:
        return {"error": "Missing pdf_key"}, 400

    ext = key.split(".")[-1].lower()
    tmp_path = "/tmp/book." + ext

    # Descargar archivo
    try:
        s3.download_file(BUCKET_NAME, key, tmp_path)
    except Exception as e:
        return {"error": f"Download failed: {str(e)}"}, 500

    metadata = {}

    if ext == "pdf":
        try:
            # Abrir PDF
            doc = fitz.open(tmp_path)
            clean_name = clean_filename(os.path.splitext(os.path.basename(key))[0])

            # Portada
            cover_image = doc[0].get_pixmap(dpi=150)
            cover_path = f"/tmp/{clean_name}_cover.jpg"
            cover_image.save(cover_path)
            s3.upload_file(
                cover_path,
                BUCKET_NAME,
                os.path.dirname(key) + f"/{clean_name}_cover.jpg",
                ExtraArgs={"ContentType": "image/jpeg"},
            )

            # Texto páginas 2 a 6
            text = ""
            for i in range(1, min(6, len(doc))):
                text += doc[i].get_text()
            doc.close()

            # OpenAI: análisis
            prompt = f"""
Analyze this book and return JSON with:
clean_title, full_title, summary (3 lines), category, index, reddit_post
Text:
{text[:3500]}
"""
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            raw_text = completion.choices[0].message.content

            # Refinar con Gemini
            final_metadata = refine_with_gemini(raw_text)
            metadata = {"raw_text": final_metadata}

            # Guardar metadata
            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=os.path.dirname(key) + "/metadata.json",
                Body=json.dumps(metadata),
                ContentType="application/json",
            )

        except Exception as e:
            return {"error": f"PDF processing failed: {str(e)}"}, 500

    elif ext == "mp3":
        try:
            title = clean_filename(os.path.splitext(os.path.basename(key))[0])

            # OpenAI: análisis de audio
            prompt = f"""
This is an audiobook or poem recording. Generate structured metadata JSON with:
- clean_title
- full_title
- summary (3 lines)
- category
- key_ideas
- target_audience
- reddit_post

Audio title: {title}
"""
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            raw_text = completion.choices[0].message.content

            # Refinar con Gemini
            final_metadata = refine_with_gemini(raw_text)
            metadata = {"raw_text": final_metadata}

            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=os.path.dirname(key) + "/metadata.json",
                Body=json.dumps(metadata),
                ContentType="application/json",
            )

            # Portada genérica
            cover_path = f"/tmp/{title}_cover.jpg"
            img = Image.new("RGB", (600, 600), color=(30, 30, 30))
            d = ImageDraw.Draw(img)
            d.text((50, 250), title[:20], fill=(255, 255, 255))
            img.save(cover_path)
            s3.upload_file(
                cover_path,
                BUCKET_NAME,
                os.path.dirname(key) + f"/{title}_cover.jpg",
                ExtraArgs={"ContentType": "image/jpeg"},
            )

        except Exception as e:
            return {"error": f"MP3 processing failed: {str(e)}"}, 500

    else:
        return {"error": f"Unsupported file type: {ext}"}, 400

    return {
        "pdf_key": key,
        "metadata_json_uploaded_to": os.path.dirname(key) + "/metadata.json",
        "cover_image_uploaded_to": os.path.dirname(key) + f"/{clean_filename(os.path.splitext(os.path.basename(key))[0])}_cover.jpg",
    }, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)