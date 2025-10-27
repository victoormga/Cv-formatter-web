import io
import os
import zipfile
import tempfile
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dotenv import load_dotenv
load_dotenv()

# Reutilizamos tu lógica existente
from generador_informe import (
    extract_text_from_pdf,
    gpt_cv_text_to_json,
    parse_cv_text_to_json,
    postprocesar_json,
    generar_docx_softtek,
)

# 👇 IMPORTS REALES que SÍ existen en tu generador_email.py actual
from generador_email import (
    construir_email,
    extract_text_from_pdf as extract_text_from_pdf_email,  # lo dejamos por si luego quieres usar plantilla PDF
)

app = FastAPI(title="CV Formatter Web")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def build_email_from_json(datos: dict, rol_txt: str, plantilla_pdf_bytes: Optional[bytes]) -> str:
    """
    Email 100% derivado del JSON (ya extraído con GPT en generador_informe.py) + rol.
    Tu generador_email actual no usa plantilla PDF externa, así que la ignoramos.
    """
    # Si en el futuro quieres soportar una plantilla PDF, aquí leerías el PDF con
    # extract_text_from_pdf_email(...) y pasarías el texto a una variante de construir_email
    # que acepte plantilla. Por ahora, simple:
    return construir_email(datos, rol_txt)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/process")
async def process(cv_pdf: UploadFile = File(...),
                  rol_txt: UploadFile = File(...),
                  plantilla_pdf: Optional[UploadFile] = File(None)):
    # Cargar bytes
    cv_bytes = await cv_pdf.read()
    rol_bytes = await rol_txt.read()
    # Aunque lo recibimos, por ahora lo ignoramos porque construir_email no usa plantilla externa
    _plantilla_bytes = await plantilla_pdf.read() if plantilla_pdf else None

    # Extraer texto del CV
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(cv_bytes)
        tf.flush()
        cv_text = extract_text_from_pdf(tf.name)

    # JSON con GPT si hay API, si no heurística
    try:
        datos = gpt_cv_text_to_json(cv_text)
    except Exception:
        datos = parse_cv_text_to_json(cv_text)

    datos = postprocesar_json(cv_text, datos)

    # Generar DOCX
    tmp_dir = tempfile.mkdtemp()
    docx_path = os.path.join(tmp_dir, "informe.docx")

    # Logo obligatorio desde /static o LOGO_PATH
    logo_path = os.getenv("LOGO_PATH", os.path.abspath(os.path.join("static", "logo.jpg")))
    if not os.path.isfile(logo_path):
        raise HTTPException(status_code=400, detail="Falta el logo corporativo en /static/logo.jpg o variable LOGO_PATH.")

    generar_docx_softtek(datos, docx_path, logo_path=logo_path)

    # Generar email.txt (reutilizando el JSON)
    email_text = build_email_from_json(datos, rol_bytes.decode("utf-8", errors="ignore"), None)

    # Preparar ZIP (sin JSON)
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(docx_path, arcname="informe.docx")
        zf.writestr("email.txt", email_text)
    mem.seek(0)

    headers = {"Content-Disposition": "attachment; filename=cv_resultados.zip"}
    return StreamingResponse(mem, media_type="application/zip", headers=headers)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
