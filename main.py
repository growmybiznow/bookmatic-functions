import os
import io
import json
import re
import fitz
import boto3
import openai
import tempfile
from flask import Flask, request, jsonify
from mutagen.id3 import ID3
import google.generativeai as genai

# Inicialización
app = Flask(__name__)

# Configurar clientes
openai.api_key = os.getenv("OPENAI_API_KEY")
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
s3 = boto3.client(
    "s3",
    endpoint_url=os.getenv("R2_ENDPOINT"),
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
)
BUCKET = os.getenv("R2_BUCKET", "bookmatic")

# Utilidades
def clean_filename(text):
    text = re.sub(r'[^a-zA-Z0-9]+', '_', text.lower())
    return text.strip('_')

def generate_metadata_with_gemini(text):
    prompt = f"""
Analyze the following content and return a JSON with:
- clean_title
- full_title
- summary: 3 bullet points (list, not paragraph)
- category (e.g., Business/Marketing)
- key_ideas (list of 3-5)
- target_audience
- index (structured sections or chapters)
- reddit_post (short social post)

Return ONLY valid JSON.

TEXT:
{text[:6000]}
"""
    model = genai.GenerativeModel("gemini-1.5-flash")
    resp = model.generate_content(prompt)
    try:
        raw_text = resp.text.strip()
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        return json.loads(raw_text[start:end+1])
    except Exception:
        return {"error": "Failed to parse Gemini output", "raw_text": resp.text}

def extract_pdf_text_and_cover(local_file):
    doc = fitz.open(local_file)
    cover_path = local_file.replace('.pdf', '_cover.jpg')
    pix = doc[0].get_pixmap(dpi=150)
    pix.save(cover_path)
    text = ""
    for i in range(1, min(6, len(doc))):
        text += doc[i].get_text()
    doc.close()
    return text, cover_path

def extract_mp3_cover(local_file, cover_output_path):
    try:
        tags = ID3(local_file)
        for tag in tags.values():
            if tag.FrameID == "APIC":
                with open(cover_output_path, "wb") as f:
                    f.write(tag.data)
                return True
    except Exception:
        pass
    return False

def transcribe_audio(local_file):
    with open(local_file, "rb") as audio_file:
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    return transcript.text

@app.route("/", methods=["GET"])
def home():
    return "Bookmatic Cloud Run is working!", 200

@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    data = request.get_json()
    pdf_key = data.get("pdf_key")
    if not pdf_key:
        return jsonify({"error": "pdf_key is required"}), 400

    # Verificar si ya existe metadata
    base_key = "/".join(pdf_key.split("/")[:-1])
    metadata_key = f"{base_key}/metadata.json"
    try:
        s3.head_object(Bucket=BUCKET, Key=metadata_key)
        return jsonify({
            "cover_image": f"{base_key}/cover.jpg",
            "file": pdf_key,
            "metadata_json": metadata_key,
            "status": "already_processed"
        })
    except Exception:
        pass

    # Descargar archivo
    with tempfile.TemporaryDirectory() as tmp:
        local_file = os.path.join(tmp, os.path.basename(pdf_key))
        s3.download_file(BUCKET, pdf_key, local_file)

        file_ext = os.path.splitext(pdf_key)[1].lower()
        extracted_text = ""
        cover_path = None

        if file_ext == ".pdf":
            extracted_text, cover_path = extract_pdf_text_and_cover(local_file)

        elif file_ext == ".mp3":
            # Extraer portada
            cover_path = os.path.join(tmp, clean_filename(os.path.basename(pdf_key)) + "_cover.jpg")
            if not extract_mp3_cover(local_file, cover_path):
                # no cover found
                cover_path = None
            # Transcripción
            extracted_text = transcribe_audio(local_file)
        else:
            return jsonify({"error": "Unsupported file type"}), 400

        # Metadata con Gemini
        metadata = generate_metadata_with_gemini(extracted_text)

        # Guardar cover si existe
        cover_key = None
        if cover_path and os.path.exists(cover_path):
            cover_key = f"{base_key}/cover_{metadata.get('clean_title','file')}.jpg"
            s3.upload_file(cover_path, BUCKET, cover_key, ExtraArgs={"ContentType": "image/jpeg"})

        # Subir metadata.json
        metadata_key = f"{base_key}/metadata.json"
        s3.put_object(
            Bucket=BUCKET,
            Key=metadata_key,
            Body=json.dumps(metadata, indent=2),
            ContentType="application/json"
        )

    return jsonify({
        "pdf_key": pdf_key,
        "cover_image_uploaded_to": cover_key,
        "metadata_json_uploaded_to": metadata_key,
        "metadata": metadata
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)