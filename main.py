@app.route("/analyze-pdf", methods=["POST"])
def analyze_pdf():
    try:
        data = request.get_json()
        pdf_key = data.get("pdf_key")
        if not pdf_key:
            return jsonify({"error": "pdf_key is required"}), 400

        print(f"[DEBUG] Starting analysis for: {pdf_key}")

        # Validar que el archivo exista en R2
        try:
            s3.head_object(Bucket=BUCKET_NAME, Key=pdf_key)
        except Exception as nf:
            print(f"[ERROR] File {pdf_key} not found in R2")
            return jsonify({"error": f"File not found in R2: {pdf_key}"}), 404

        folder, filename = pdf_key.rsplit('/', 1)
        temp_folder = folder
        local_pdf = "/tmp/book.pdf"

        # Verificar duplicados
        if files_already_processed(temp_folder):
            print(f"[DEBUG] Already processed: {pdf_key}")
            return jsonify({
                "file": pdf_key,
                "status": "already_processed",
                "cover_image": f"{temp_folder}/cover.jpg",
                "metadata_json": f"{temp_folder}/metadata.json"
            })

        # Descargar archivo
        print("[DEBUG] Downloading PDF...")
        s3.download_file(BUCKET_NAME, pdf_key, local_pdf)

        # Extraer contenido y portada
        print("[DEBUG] Extracting text and cover...")
        extracted_text, cover_path = extract_pdf_text_and_cover(local_pdf)

        # Generar metadatos con OpenAI
        print("[DEBUG] Calling OpenAI...")
        try:
            metadata = get_book_metadata(extracted_text)
            if not isinstance(metadata, dict):
                raise Exception("Metadata returned is not a dict")
        except Exception as ai_err:
            print(f"[ERROR] OpenAI failed: {ai_err}")
            metadata = {
                "error": "metadata generation failed",
                "fallback_title": filename
            }

        # Ruta final
        clean_title = clean_filename(metadata.get("clean_title", filename.replace(".pdf","")))
        category = metadata.get("category", "Uncategorized").replace(" ", "_")
        final_folder = f"{category}/PDF/{clean_title}"
        final_pdf_key = f"{final_folder}/{clean_title}.pdf"

        # Mover PDF
        print(f"[DEBUG] Moving PDF to {final_pdf_key}")
        try:
            copy_and_delete(pdf_key, final_pdf_key)
        except Exception as move_err:
            print(f"[ERROR] Failed to move file: {move_err}")
            return jsonify({"error": f"Failed to move file: {move_err}"}), 500

        # Subir portada
        print(f"[DEBUG] Uploading cover to {final_folder}/cover.jpg")
        with open(cover_path, "rb") as f:
            s3.upload_fileobj(f, BUCKET_NAME, f"{final_folder}/cover.jpg")

        # Subir metadatos
        meta_key = f"{final_folder}/metadata.json"
        print(f"[DEBUG] Uploading metadata.json to {meta_key}")
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=meta_key,
            Body=json.dumps(metadata, indent=2),
            ContentType="application/json"
        )

        print("[DEBUG] Analysis completed successfully.")

        return jsonify({
            "status": "processed",
            "original_pdf": pdf_key,
            "final_pdf": final_pdf_key,
            "cover_image": f"{final_folder}/cover.jpg",
            "metadata_json": meta_key,
            "metadata": metadata
        })

    except Exception as e:
        print("ERROR:", e)
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500