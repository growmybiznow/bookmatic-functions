from flask import Flask, request, jsonify
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Bookmatic API is running", 200

@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    data = request.get_json()
    pdf_key = data.get("pdf_key")
    return jsonify({"status": "ok", "pdf_key": pdf_key})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)