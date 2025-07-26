from flask import Flask, request, jsonify
import os
import re
import json
import traceback
import boto3
import fitz  # PyMuPDF
from openai import OpenAI

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
# UTILITIES
# -------------------------------------------------------------------

def clean_filename(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]+', '_', text.lower()).strip('_')

def extract_pdf_text_and_cover(local_file):
    """Extract text (pages 2-6) and generate cover JPG."""
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
    """Use OpenAI to generate rich metadata."""
    prompt = f"""
Analyze the following book content and return a JSON with these fields:

- clean_title: short slug
- full_title
- summary: 3 sentences explaining the book
- category: a precise path like Business/Marketing/LeadGeneration
- target_audience
- level: beginner / intermediate / advanced
- key_insights: 5-7 bullet points
- index: main chapters or sections
- content_ideas:
    guides: 3 titles
    articles: 3 titles
    videos: 3 titles
- reddit_post

Important: 
- Return ONLY a valid JSON. Do not include ```json``` or any explanations.

TEXT:
{extracted_text[:3500]}
"""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.choices[0].message.content
    try:
        return json.loads(raw_text)
    except Exception:
        return {"raw_text": raw_text}

def files_already_processed(folder):
    """Check if cover and metadata already exist."""
    existing = set()
    resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=f"{folder}/")
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.endswith("cover.jpg"):
            existing.add("cover")
        elif key.endswith("metadata.json"):
            existing.add("metadata")
    return "cover" in existing and "metadata" in existing

def copy_and_delete(old_key, new_key):
    """Move object inside R2 (copy+delete)."""
    s3.copy_object(Bucket=BUCKET_NAME, CopySource=f"{BUCKET_NAME}/{old_key}", Key=new_key)
    s3.delete_object(Bucket=BUCKET_NAME, Key=old_key)

# -------------------------------------------------------------------
# ROUTES
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

        # Extract current folder and file
        folder, filename = pdf_key.rsplit('/', 1)
        temp_folder = folder
        temp_filename = filename
        local_pdf = "/tmp/book.pdf"

        # Check duplicates in current folder
        if files_already_processed(temp_folder):
            return jsonify({
                "file": pdf_key,
                "status": "already_processed",
                "cover_image": f"{temp_folder}/cover.jpg",
                "metadata_json": f"{temp_folder}/metadata.json"
            })

        # Download PDF
        s3.download_file(BUCKET_NAME, pdf_key, local_pdf)

        # Extract text and cover
        extracted_text, cover_path = extract_pdf_text_and_cover(local_pdf)
        metadata = get_book_metadata(extracted_text)

        # Use clean title and category for final path
        clean_title = clean_filename(metadata.get("clean_title", filename.replace(".pdf","")))
        category = metadata.get("category", "Uncategorized").replace(" ", "_")
        final_folder = f"{category}/PDF/{clean_title}"

        # Move original PDF to final path
        final_pdf_key = f"{final_folder}/{clean_title}.pdf"
        copy_and_delete(pdf_key, final_pdf_key)

        # Upload cover
        cover_key = f"{final_folder}/cover.jpg"
        with open(cover_path, "rb") as f:
            s3.upload_fileobj(f, BUCKET_NAME, cover_key)

        # Upload metadata
        meta_key = f"{final_folder}/metadata.json"
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=meta_key,
            Body=json.dumps(metadata, indent=2),
            ContentType="application/json"
        )

        return jsonify({
            "status": "processed",
            "original_pdf": pdf_key,
            "final_pdf": final_pdf_key,
            "cover_image": cover_key,
            "metadata_json": meta_key,
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