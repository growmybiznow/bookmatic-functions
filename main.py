import os
import re
import json
import boto3
import fitz  # PyMuPDF
from flask import Flask, request
from openai import OpenAI
from PIL import Image, ImageDraw
import google.generativeai as genai

app = Flask(__name__)

# Configuración APIs
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# Configuración Cloudflare R2
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("R2_ENDPOINT"),
    aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
)
BUCKET_NAME = os.environ.get("R2_BUCKET", "bookmatic")

# Helpers
def clean_filename(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]+', '_', text).strip('_')


def parse_json_from_text(raw_text: str):
    """Limpia la salida (quita ```json) y convierte en JSON."""
    cleaned = raw_text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "")
    try:
        return json.loads(cleaned)
    except Exception:
        return {"raw_text": raw_text}


def generate_metadata(prompt: str):
    """
    Intenta primero con Gemini y si falla usa OpenAI.
    """
    gemini_text = ""
    try:
        gemini_resp = genai.GenerativeModel("gemini-1.5-flash").generate_content(prompt)
        gemini_text = gemini_resp.text
    except Exception as e:
        print(f"[WARN] Gemini no disponible: {e}")

    if not gemini_text:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return completion.choices[0].message.content
    return gemini_text


@app.route("/", methods=["GET"])
def home():
    return "Bookmatic API is running", 200


@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    data = request.get_json()
    key = data.get("pdf_key")
    if not key:
        return {"error": "Missing pdf_key"}, 400

    print(f"[INFO] Procesando archivo: {key}")
    ext = key.split(".")[-1].lower()

    tmp_path = "/tmp/book." + ext
    try:
        s3.download_file(BUCKET_NAME, key, tmp_path)
    except Exception as e:
        return {"error": str(e)}, 500

    title = clean_filename(os.path.splitext(os.path.basename(key))[0])
    base_dir = os.path.dirname(key)
    cover_file = f"{base_dir}/{title}_cover.jpg"
    metadata_file = f"{base_dir}/metadata.json"

    metadata = {}

    if ext == "pdf":
        try:
            doc = fitz.open(tmp_path)

            # Portada
            cover_image = doc[0].get_pixmap(dpi=150)
            cover_path = f"/tmp/{title}_cover.jpg"
            cover_image.save(cover_path)
            s3.upload_file(
                cover_path, BUCKET_NAME, cover_file,
                ExtraArgs={"ContentType": "image/jpeg"},
            )

            # Texto de páginas 2 a 6
            text = "".join([doc[i].get_text() for i in range(1, min(6, len(doc)))])
            doc.close()

            prompt = f"""
Analyze this book and return JSON with:
clean_title, full_title, summary (3 lines), category, index, reddit_post, key_ideas, target_audience
Text:
{text[:3500]}
"""
            raw_text = generate_metadata(prompt)
            metadata = parse_json_from_text(raw_text)

            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=metadata_file,
                Body=json.dumps(metadata, indent=2),
                ContentType="application/json",
            )

        except Exception as e:
            return {"error": str(e)}, 500

    elif ext == "mp3":
        try:
            prompt = f"""
This is an audiobook or poem recording. Generate structured metadata JSON with:
- clean_title
- full_title
- summary (3 lines)
- category (Literature/Poetry/etc.)
- key_ideas
- target_audience
- reddit_post
Audio title: {title}
"""
            raw_text = generate_metadata(prompt)
            metadata = parse_json_from_text(raw_text)

            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=metadata_file,
                Body=json.dumps(metadata, indent=2),
                ContentType="application/json",
            )

            # Portada genérica
            cover_path = f"/tmp/{title}_cover.jpg"
            img = Image.new("RGB", (600, 600), color=(30, 30, 30))
            d = ImageDraw.Draw(img)
            d.text((30, 280), title[:20], fill=(255, 255, 255))
            img.save(cover_path)
            s3.upload_file(
                cover_path, BUCKET_NAME, cover_file,
                ExtraArgs={"ContentType": "image/jpeg"},
            )

        except Exception as e:
            return {"error": str(e)}, 500

    else:
        return {"error": f"Unsupported file type: {ext}"}, 400

    return {
        "pdf_key": key,
        "metadata_json_uploaded_to": metadata_file,
        "cover_image_uploaded_to": cover_file,
    }, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)