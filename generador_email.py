#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera un email de presentación usando:
  - JSON del candidato (generado por generador_informe.py)
  - Rol/proyecto (txt)
  - (Opcional) Plantilla PDF corporativa, pero sin textos prefabricados
    ni placeholders. Si no hay plantilla PDF o no se reconocen marcadores,
    se usa una plantilla interna minimalista y condicional (omite lo que falte).
"""

import os, re, json, argparse

# --- extracción PDF opcional (solo si pasas una plantilla PDF con texto) ---
def try_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None

PyPDF2 = try_import("PyPDF2")
pdfminer = try_import("pdfminer")
fitz = try_import("fitz")

def extract_text_from_pdf(pdf_path: str) -> str:
    if PyPDF2:
        try:
            parts = []
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for p in reader.pages:
                    parts.append(p.extract_text() or "")
            txt = "\n".join(parts)
            if txt.strip():
                return txt
        except Exception:
            pass
    if pdfminer:
        try:
            from pdfminer.high_level import extract_text as pm_extract
            txt = pm_extract(pdf_path)
            if txt.strip():
                return txt
        except Exception:
            pass
    if fitz:
        try:
            doc = fitz.open(pdf_path)
            parts = []
            for page in doc:
                blocks = page.get_text("blocks")
                blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
                parts.extend(b[4] for b in blocks if isinstance(b[4], str) and b[4].strip())
            txt = "\n".join(parts)
            if txt.strip():
                return txt
        except Exception:
            pass
    raise RuntimeError("No fue posible extraer texto del PDF. Instala PyPDF2 o pdfminer.six o PyMuPDF.")

# --- plantilla interna mínima; NO usa placeholders fijos ---
PLANTILLA_INTERNA = """Asunto: Presentación de {nombre} – {asunto}

Buenos días,
Adjuntamos la candidatura de {nombre}{frase_proyecto}.

{parrafo_perfil}
{parrafo_contexto}
{bloque_contacto}

Quedo a vuestra disposición para ampliar cualquier detalle técnico.

Un saludo y gracias,
Iñigo
""".rstrip("\n")

# -------- utilidades de derivación desde el JSON --------
def _clean(s):
    return re.sub(r"\s+", " ", str(s or "").strip())

def top_tec(datos, limit=10):
    # combina skills + tecnologías de experiencias, deduplicado, orden estable
    seen, res = set(), []
    for t in (datos.get("skills") or []):
        t2 = _clean(t)
        if t2 and t2.lower() not in seen:
            seen.add(t2.lower()); res.append(t2)
    for e in (datos.get("experiencia") or []):
        for t in (e.get("tecnologias") or []):
            t2 = _clean(t)
            if t2 and t2.lower() not in seen:
                seen.add(t2.lower()); res.append(t2)
    return res[:limit]

def empresas_clientes(datos, limit=6):
    seen, res = set(), []
    for e in (datos.get("experiencia") or []):
        emp = _clean(e.get("empresa"))
        if emp and emp.lower() not in seen:
            seen.add(emp.lower()); res.append(emp)
    return res[:limit]

def empresa_actual(datos):
    # intenta la más reciente: desde/hasta -> la que tenga "hasta" vacía/Hoy; si no, la primera del array
    exp = datos.get("experiencia") or []
    if not exp:
        return ""
    def norm_hasta(x):
        x = _clean(x).lower()
        return (x == "" or "hoy" in x or "actual" in x or "presente" in x)
    actuales = [e for e in exp if norm_hasta(e.get("hasta"))]
    base = (actuales[0] if actuales else exp[0])
    return _clean(base.get("empresa"))

def contexto_actual(datos, max_chars=260):
    # toma la descripción del puesto actual y la recorta sensatamente
    exp = datos.get("experiencia") or []
    if not exp:
        return ""
    def norm_hasta(x):
        x = _clean(x).lower()
        return (x == "" or "hoy" in x or "actual" in x or "presente" in x)
    actuales = [e for e in exp if norm_hasta(e.get("hasta"))]
    base = (actuales[0] if actuales else exp[0])
    desc = _clean(base.get("descripcion"))
    if not desc:
        return ""
    if len(desc) <= max_chars:
        return desc
    # corta por frase
    sents = re.split(r"(?<=[.!?…])\s+", desc)
    acc = []
    total = 0
    for s in sents:
        if total + len(s) + 1 > max_chars:
            break
        acc.append(s); total += len(s) + 1
    return " ".join(acc).strip() or desc[:max_chars].rstrip() + "…"

def calidad_from_text(datos):
    # busca pistas de calidad/testing en skills/desc
    bag = " ".join(top_tec(datos, 999)).lower()
    for e in (datos.get("experiencia") or []):
        bag += " " + _clean(e.get("descripcion")).lower()
    hits = []
    for kw in ["junit","testng","tdd","bdd","cucumber","sonarqube","sonar","qa","quality","pytest","jest","cypress","ci/cd","ci cd","pipeline"]:
        if kw in bag:
            hits.append(kw.upper() if kw.isalpha() and len(kw)<=4 else kw.title())
    # muestra 3 como máximo
    return ", ".join(sorted(set(hits)))[:120]

def ingles_from_idiomas(datos):
    # intenta encontrar inglés en idiomas; si no, devuelve ""
    for i in (datos.get("idiomas") or []):
        idioma = _clean(i.get("idioma")).lower()
        nivel = _clean(i.get("nivel"))
        if idioma.startswith(("ingl","engl","english")):
            return nivel or "B2"
    return ""

def ciudad_from_ubicacion(datos):
    u = _clean(datos.get("ubicacion"))
    if not u:
        return ""
    # toma lo que haya antes de coma si existe
    return _clean(u.split(",")[0])

def asunto_from_rol(rol_txt, datos):
    linea = _clean(rol_txt.splitlines()[0] if rol_txt else "")
    if linea.lower().startswith("rol:"):
        linea = _clean(linea[4:])
    if linea:
        return linea
    # fallback al título del candidato
    return _clean(datos.get("titulo") or "Perfil técnico")

# -------- construcción del email (sin campos predefinidos) --------
def construir_email(datos, rol_txt):
    nombre = _clean(datos.get("nombre") or "Candidato/a")
    asunto = f"{asunto_from_rol(rol_txt, datos)}"
    tec = top_tec(datos, limit=10)
    clientes = empresas_clientes(datos, limit=6)
    empresa_act = empresa_actual(datos)
    contexto = contexto_actual(datos)
    calidad = calidad_from_text(datos)
    ciudad = ciudad_from_ubicacion(datos)
    ingles = ingles_from_idiomas(datos)
    email = _clean(datos.get("email"))

    # Frase proyecto (opcional)
    frase_proyecto = f" para el proyecto del asunto" if asunto else ""
    # Párrafo perfil
    piezas = []
    # construye “X es un {titulo} con más de N años…” si tienes datos; si no, algo corto
    titulo = _clean(datos.get("titulo"))
    # años aproximados a partir de fechas (rápido, opcional)
    def approx_years(exp):
        import datetime as _dt
        def parse(d):
            d = _clean(d).lower()
            if not d:
                return None
            m = re.match(r"(\d{4})-(\d{2})", d)
            if m: 
                return _dt.date(int(m.group(1)), int(m.group(2)), 1)
            m = re.search(r"(\d{4})", d)
            if m:
                return _dt.date(int(m.group(1)), 1, 1)
            return None
        spans = []
        for e in exp or []:
            d0 = parse(e.get("desde"))
            d1 = parse(e.get("hasta")) or _dt.date.today()
            if d0 and d1 and d1 >= d0:
                spans.append((d0, d1))
        if not spans:
            return ""
        start = min(a for a,_ in spans); end = max(b for _,b in spans)
        months = (end.year-start.year)*12 + (end.month-start.month)
        if months <= 0: 
            return ""
        years = round(months/12, 1)
        if years.is_integer():
            years = int(years)
        return str(years)

    xp = approx_years(datos.get("experiencia"))
    linea_perfil = []
    if titulo:
        linea_perfil.append(titulo)
    if xp:
        linea_perfil.append(f"con {xp}+ años de experiencia")
    if tec:
        linea_perfil.append(f"especializado en {', '.join(tec)}")
    parrafo_perfil = ""
    if linea_perfil:
        base = f"{nombre} es " + " ".join([linea_perfil[0]] + [(" ".join(linea_perfil[1:]))]).strip()
        # clientes (opcionales)
        if clientes:
            base += f". Ha colaborado en proyectos para {', '.join(clientes)}"
        parrafo_perfil = base + "."

    # Párrafo contexto actual (opcional)
    parrafo_contexto = ""
    detalle = []
    if empresa_act:
        detalle.append(f"En su posición actual en {empresa_act}")
    if contexto:
        detalle.append(f"participa en {contexto}")
    if tec:
        detalle.append(f"aplicando tecnologías como {', '.join(tec[:6])}")
    if calidad:
        detalle.append(f"y con enfoque a calidad/testing ({calidad})")
    if detalle:
        parrafo_contexto = (", ".join(detalle)).rstrip(", ") + "."

    # Bloque contacto (solo líneas con datos)
    contacto_lines = []
    if ciudad: contacto_lines.append(f"Ubicación: {ciudad}")
    if ingles: contacto_lines.append(f"Inglés: {ingles}")
    if email:  contacto_lines.append(f"Email: {email}")
    bloque_contacto = "\n".join(contacto_lines)

    # Ensambla con plantilla interna
    cuerpo = PLANTILLA_INTERNA.format(
        nombre=nombre,
        asunto=asunto or "Presentación de candidatura",
        frase_proyecto=frase_proyecto if asunto else "",
        parrafo_perfil=parrafo_perfil,
        parrafo_contexto=parrafo_contexto,
        bloque_contacto=bloque_contacto
    )

    # Limpieza: elimina líneas vacías dobles
    cuerpo = re.sub(r"\n{3,}", "\n\n", cuerpo).strip() + "\n"
    return cuerpo

# -------- CLI --------
def main():
    ap = argparse.ArgumentParser(description="Genera email de presentación desde un JSON y una descripción de rol.")
    ap.add_argument("--json_datos", required=True, help="Ruta al JSON del candidato")
    ap.add_argument("--rol_txt", required=True, help="Ruta al .txt con el rol/proyecto")
    ap.add_argument("--salida_txt", required=True, help="Ruta de salida del email .txt")
    ap.add_argument("--plantilla_pdf", default=None, help="(Opcional) Plantilla corporativa en PDF (texto)")
    args = ap.parse_args()

    datos = json.load(open(args.json_datos, "r", encoding="utf-8"))
    rol = open(args.rol_txt, "r", encoding="utf-8").read() if os.path.exists(args.rol_txt) else ""

    # Si te empeñas en usar una plantilla PDF, podrías leerla aquí y
    # buscar marcadores personalizados, pero por ahora usamos la interna condicional.
    # (Si quisieras soportar una plantilla PDF con marcadores, dímelo y lo añadimos.)

    email_txt = construir_email(datos, rol)
    os.makedirs(os.path.dirname(args.salida_txt) or ".", exist_ok=True)
    with open(args.salida_txt, "w", encoding="utf-8") as f:
        f.write(email_txt)
    print(email_txt)

if __name__ == "__main__":
    main()
