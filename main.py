from flask import Flask
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Bookmatic Cloud Run is working!", 200

# Punto de entrada
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)