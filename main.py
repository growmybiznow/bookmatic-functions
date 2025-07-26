import os
import json
import re
import traceback
import boto3
import fitz  # PyMuPDF
from flask import Flask, request, jsonify
from openai import OpenAI

# Flask
app = Flask(__name__)

# Configuración R2
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
)

# Config OpenAI
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Utilidades
def clean_filename(text):
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower())
    return text.strip("_")

def extract_pdf_text_and_cover(local_file):
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
    prompt = f"""
Analyze this text and return a JSON with:
- clean_title
- full_title
- summary (3 lines)
- category (e.g. Business/Marketing)
- index (main sections)
- reddit_post (promotional text)

Text:
{extracted_text[:3500]}
"""
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    content = response.choices[0].message.content
    try:
        cleaned = content.strip("` \n")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        return json.loads(cleaned)
    except Exception:
        return {"raw_text": content}

def copy_and_delete(old_key, new_key):
    s3.copy_object(Bucket=R2_BUCKET, CopySource={"Bucket": R2_BUCKET, "Key": old_key}, Key=new_key)
    s3.delete_object(Bucket=R2_BUCKET, Key=old_key)

# Home page con formulario
@app.route("/", methods=["GET", "POST"])
def upload_and_process():
    if request.method == "GET":
        return """
        <html><body>
            <h2>Upload Book (PDF or MP3)</h2>
            <form enctype="multipart/form-data" method="POST">
                <input type="file" name="file" />
                <button type="submit">Upload & Process</button>
            </form>
        </body></html>
        """

    # POST: subir y procesar
    file = request.files.get("file")
    if not file:
        return "No file uploaded", 400

    filename = clean_filename(file.filename.rsplit("/", 1)[-1])
    local_path = f"/tmp/{filename}"
    file.save(local_path)

    # Detectar tipo de archivo
    ext = filename.split(".")[-1].lower()
    if ext == "pdf":
        base_folder = "Business/PDF"
    elif ext == "mp3":
        base_folder = "Business/MP3"
    else:
        return "Only PDF or MP3 allowed", 400

    # Subir archivo temporal
    temp_key = f"{base_folder}/{filename}"
    with open(local_path, "rb") as f:
        s3.upload_fileobj(f, R2_BUCKET, temp_key)

    # Si es MP3 solo lo subimos
    if ext == "mp3":
        return f"MP3 uploaded to {temp_key}"

    # Extraer contenido y portada
    extracted_text, cover_path = extract_pdf_text_and_cover(local_path)

    # Generar metadata
    try:
        metadata = get_book_metadata(extracted_text)
    except Exception as e:
        print("OpenAI error:", e)
        metadata = {"error": "metadata generation failed"}

    clean_title = clean_filename(metadata.get("clean_title", filename.replace(".pdf","")))
    category = metadata.get("category", "Uncategorized").replace(" ", "_")
    final_folder = f"{category}/PDF/{clean_title}"
    final_pdf_key = f"{final_folder}/{clean_title}.pdf"

    # Mover PDF
    copy_and_delete(temp_key, final_pdf_key)

    # Subir portada
    cover_key = f"{final_folder}/cover.jpg"
    with open(cover_path, "rb") as f:
        s3.upload_fileobj(f, R2_BUCKET, cover_key)

    # Subir metadata.json
    meta_key = f"{final_folder}/metadata.json"
    s3.put_object(
        Bucket=R2_BUCKET,
        Key=meta_key,
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json"
    )

    return jsonify({
        "status": "processed",
        "final_pdf": final_pdf_key,
        "cover_image": cover_key,
        "metadata_json": meta_key,
        "metadata": metadata
    })

# Endpoint solo para API (mantiene compatibilidad)
@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf_api():
    return jsonify({"error": "Use POST / (upload form) instead"}), 400

# Arranque
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)