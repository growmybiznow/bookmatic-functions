import os
from flask import Flask

# --------------------------------------------------------
# Bookmatic - API Básica
# --------------------------------------------------------
# Este archivo solo sirve para probar que Cloud Run funcione.
# Luego agregaremos el resto de la lógica (R2, OpenAI, etc).
# --------------------------------------------------------

# Crear la aplicación Flask
app = Flask(__name__)

# Ruta principal
@app.route("/", methods=["GET"])
def index():
    return "Bookmatic API is running", 200


# Punto de entrada principal
if __name__ == "__main__":
    # Cloud Run asigna el puerto usando la variable de entorno PORT.
    port = int(os.environ.get("PORT", 8080))
    # Host 0.0.0.0 hace que la app escuche todas las interfaces (requerido por Cloud Run)
    app.run(host="0.0.0.0", port=port)