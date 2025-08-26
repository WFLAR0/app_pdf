

import io
import os
import re
import base64
import tempfile
import platform
import uuid
from typing import List, Dict, Any, Optional

import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError, ImageFilter

# --- PDF: pypdf primero; fallback a PyPDF2 si no está disponible ---
try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError:
    from PyPDF2 import PdfReader, PdfWriter

# --- DOCX → PDF: usar docx2pdf sólo en Win/mac; en Linux/Cloud usamos fallback ---
docx2pdf_convert = None
if platform.system().lower() in ("windows", "darwin"):
    try:
        from docx2pdf import convert as docx2pdf_convert
    except Exception:
        docx2pdf_convert = None

# --- Lectura de DOCX (para fallback) ---
try:
    from docx import Document
except Exception:
    Document = None

# --- ReportLab para imagen→PDF exacto (página = tamaño imagen) ---
try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader as RL_ImageReader
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

SUPPORTED_EXTS = {"pdf", "png", "jpg", "jpeg", "docx"}
MIN_CAPTURE_H = 540  # altura mínima "tipo recorte"

# ─────────────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────────────

def bytesio(data: bytes) -> io.BytesIO:
    bio = io.BytesIO(); bio.write(data); bio.seek(0); return bio

def normalize_ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().strip(".")

def safe_open_image(img_bytes: bytes) -> Optional[Image.Image]:
    try:
        with Image.open(bytesio(img_bytes)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            return img.convert("RGB")
    except UnidentifiedImageError:
        return None
    except Exception:
        return None

def ensure_min_height(img: Image.Image, min_h: int = MIN_CAPTURE_H) -> Image.Image:
    if img.height >= min_h:
        return img
    scale = min_h / float(img.height)
    new_w = max(1, int(img.width * scale))
    return img.resize((new_w, min_h), Image.Resampling.LANCZOS)

def safe_read_docx_text(file_bytes: bytes) -> str:
    if Document is None:
        return ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tf:
            tf.write(file_bytes); tf.flush(); path = tf.name
        doc = Document(path)
        text = "\n".join(p.text or "" for p in doc.paragraphs)
        try: os.remove(path)
        except Exception: pass
        return text
    except Exception:
        return ""

def image_to_pdf_exact_bytes(img: Image.Image) -> Optional[bytes]:
    """Convierte una imagen a PDF con página del MISMO tamaño (sin márgenes)."""
    try:
        img = img.convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        if REPORTLAB_OK:
            buf = io.BytesIO()
            c = rl_canvas.Canvas(buf, pagesize=(w, h))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            try:
                img.save(tmp.name, format="JPEG", quality=95, subsampling=0, optimize=True)
                c.drawImage(RL_ImageReader(tmp.name), 0, 0, width=w, height=h,
                            preserveAspectRatio=False, mask='auto')
                c.showPage(); c.save(); buf.seek(0)
                return buf.getvalue()
            finally:
                try: os.unlink(tmp.name)
                except Exception: pass
        out = io.BytesIO(); img.save(out, format="PDF"); out.seek(0); return out.getvalue()
    except Exception:
        return None

def docx_to_pdf_bytes(docx_bytes: bytes, title: Optional[str] = None) -> bytes:
    """DOCX→PDF: usa docx2pdf en Win/mac; fallback simple en otros entornos."""
    if docx2pdf_convert is not None:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "tmp.docx"); dst = os.path.join(td, "out.pdf")
                with open(src, "wb") as f: f.write(docx_bytes)
                docx2pdf_convert(src, dst)
                with open(dst, "rb") as f: return f.read()
        except Exception:
            pass
    # Fallback: texto→imagen→PDF
    from PIL import ImageDraw, ImageFont
    page = Image.new("RGB", (1654, 2339), "white")  # ~A4 @ 200dpi
    try:
        draw = ImageDraw.Draw(page)
        font = ImageFont.load_default()
        x, y, lh, maxw = 40, 40, 16, page.width - 80
        if title:
            draw.text((x, y), title, fill="black", font=font); y += lh*2
        text = safe_read_docx_text(docx_bytes) or "(No se pudo extraer texto del DOCX)"
        for para in text.split("\n"):
            line = ""
            for word in para.split(" "):
                test = (line + " " + word).strip()
                if draw.textlength(test, font=font) > maxw:
                    draw.text((x, y), line, fill="black", font=font); y += lh; line = word
                else:
                    line = test
            draw.text((x, y), line, fill="black", font=font); y += lh
    except Exception:
        pass
    return image_to_pdf_exact_bytes(page) or b""

def merge_pdfs(pdf_bytes_list: List[bytes]) -> bytes:
    writer = PdfWriter()
    for data in pdf_bytes_list:
        try:
            reader = PdfReader(bytesio(data))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue
    out = io.BytesIO()
    # Evitar PDF vacío
    if len(writer.pages) == 0:
        blank = Image.new("RGB", (800, 600), "white")
        b = io.BytesIO(); blank.save(b, format="PDF"); b.seek(0)
        r = PdfReader(b); [writer.add_page(p) for p in r.pages]
    writer.write(out); out.seek(0)
    return out.getvalue()

def pdf_iframe(pdf_bytes: bytes, height: int = 640) -> None:
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    html = f"""
    <iframe src="data:application/pdf;base64,{b64}"
            width="100%" height="{height}"
            style="border:1px solid #ddd;border-radius:10px"></iframe>
    """
    st.components.v1.html(html, height=height+12, scrolling=False)

def safe_download_name(name: str) -> str:
    # Evitar comillas raras en el atributo download
    return name.replace('"', "'")

def next_order() -> int:
    vals = [int(it["order"]) for it in st.session_state.get("archivos", [])] or [0]
    return max(vals) + 1

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Archivos (incl. imágenes) → PDF único / por grupos", layout="wide")
st.title("📑 COMBINAR PDFS")

# Estado
if "archivos" not in st.session_state:
    # cada item: {id, name(ORIGINAL), bytes, ext, include, order, is_image, group}
    # group: 'A' (checkbox marcado) o 'B' (por defecto)
    st.session_state.archivos: List[Dict[str, Any]] = []

with st.sidebar:
    st.markdown("**Estado:**✅")
    st.write(f"Items: **{len(st.session_state.archivos)}**")
    if st.button("🧹 Limpiar lista", use_container_width=True):
        st.session_state.archivos.clear(); st.rerun()

st.markdown(
    """
- Sube **PDF / PNG / JPG / DOCX**.  
    """
)

# ========== Subir archivos (imágenes se procesan como recortes) ==========
with st.expander("📤 Subir archivos (PDF / PNG / JPG / DOCX)", expanded=True):
    up = st.file_uploader("Selecciona archivos", type=["pdf", "png", "jpg", "jpeg", "docx"], accept_multiple_files=True)
    if st.button("➕ Agregar a la lista"):
        if not up:
            st.info("No seleccionaste archivos aún.")
        else:
            added = 0
            for f in up:
                name = f.name                  # ← nombre ORIGINAL
                ext = normalize_ext(name)
                raw = f.read()

                is_image = ext in ("png", "jpg", "jpeg")
                if is_image:
                    # Procesar como "recorte": nitidez + altura mínima
                    img = safe_open_image(raw)
                    if img is None:
                        st.warning(f"Imagen inválida: {name}")
                        continue
                    try:
                        img = ensure_min_height(img, MIN_CAPTURE_H)
                        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
                    except Exception:
                        pass
                    buf = io.BytesIO(); img.save(buf, format="PNG", optimize=True); buf.seek(0)
                    raw = buf.getvalue()
                    # mantenemos el nombre ORIGINAL del usuario

                st.session_state.archivos.append({
                    "id": uuid.uuid4().hex,
                    "name": name,          # ← nombre ORIGINAL
                    "bytes": raw,
                    "ext": ext,
                    "include": True,
                    "order": next_order(),
                    "is_image": is_image,
                    "group": "B",          # por defecto Grupo B; se cambia con el check
                })
                added += 1
            st.success(f"Se agregaron {added} archivo(s).")

# ========== Administrar archivos (orden numérico + incluir + grupo + borrar) ==========
with st.expander("🗂️ Administrar archivos", expanded=True):
    if not st.session_state.archivos:
        st.info("Aún no hay archivos en la lista.")
    else:
        for i, a in enumerate(st.session_state.archivos):
            # columnas: nombre, incluir, orden, grupoA check, borrar
            c1, c2, c3, c4, c5 = st.columns([5, 1, 1, 1, 1])

            # Mostrar nombre ORIGINAL (no editable)
            c1.markdown(f"**Archivo:** `{a['name']}`")

            # Incluir
            a["include"] = c2.checkbox("Incluir", value=a["include"], key=f"arc_inc_{a['id']}")

            # Orden
            a["order"] = c3.number_input("Orden", value=int(a["order"]), step=1, key=f"arc_ord_{a['id']}")

            # Grupo A (check) / Grupo B (sin check)
            chkA = (a.get("group", "B") == "A")
            chkA_new = c4.checkbox("Grupo A", value=chkA, key=f"arc_grpA_{a['id']}")
            a["group"] = "A" if chkA_new else "B"

            # Borrar
            if c5.button("🗑️", key=f"arc_del_{a['id']}"):
                st.session_state.archivos.pop(i); st.rerun()

            # Vista previa si es imagen
            if a.get("is_image") and a.get("bytes"):
                try:
                    st.image(a["bytes"], caption=f"{a['name']}  ·  Grupo: {a['group']}", use_container_width=True)
                except Exception:
                    st.caption("(No se pudo previsualizar la imagen)")

# ========== Generar PDF combinado ==========
st.divider()
final_name = st.text_input("📄 Nombre del PDF final (combinado)", value="resultado_combinado.pdf")
if st.button("🛠️ Generar PDF combinado", type="primary"):
    try:
        items_sorted = sorted(st.session_state.archivos, key=lambda x: int(x["order"]))
        pdfs: List[bytes] = []
        for it in items_sorted:
            if not it.get("include", True):
                continue
            ext = it["ext"]; pdfb = None
            if ext == "pdf":
                pdfb = it["bytes"]
            elif ext in ("png", "jpg", "jpeg"):
                img = safe_open_image(it["bytes"])
                pdfb = image_to_pdf_exact_bytes(img) if img else None
            elif ext == "docx":
                pdfb = docx_to_pdf_bytes(it["bytes"], title=it["name"])
            if pdfb:
                pdfs.append(pdfb)

        if not pdfs:
            st.warning("No hay elementos incluidos para combinar.")
        else:
            combined = merge_pdfs(pdfs)
            st.success(f"PDF combinado generado: {final_name or 'resultado_combinado.pdf'}")
            st.markdown("**Previsualización**")
            pdf_iframe(combined, height=640)
            st.download_button("⬇️ Descargar PDF combinado", data=combined,
                               file_name=(final_name or "resultado_combinado.pdf"),
                               mime="application/pdf", use_container_width=True)
    except Exception as e:
        st.exception(e)
        st.error("Ocurrió un error al generar el PDF combinado.")

# ========== Generar 2 PDFs por grupos (por check) ==========
st.divider()
with st.expander("🧭 Generar 2 PDFs por grupos", expanded=True):
    st.caption("Selecciona en el **Administrador de archivos** qué elementos van al **Grupo A** (check). Los demás irán al **Grupo B**.")
    nameA = st.text_input("Nombre PDF Grupo A", value="Grupo_A.pdf")
    nameB = st.text_input("Nombre PDF Grupo B", value="Grupo_B.pdf")

    if st.button("📑 Generar PDFs por grupos", use_container_width=True):
        try:
            items_sorted = sorted(st.session_state.archivos, key=lambda x: int(x["order"]))
            A_list: List[bytes] = []; B_list: List[bytes] = []
            for it in items_sorted:
                if not it.get("include", True):
                    continue
                ext = it["ext"]; pdfb = None
                if ext == "pdf":
                    pdfb = it["bytes"]
                elif ext in ("png", "jpg", "jpeg"):
                    img = safe_open_image(it["bytes"]); pdfb = image_to_pdf_exact_bytes(img) if img else None
                elif ext == "docx":
                    pdfb = docx_to_pdf_bytes(it["bytes"], title=it["name"])
                if not pdfb:
                    continue

                if it.get("group", "B") == "A":
                    A_list.append(pdfb)
                else:
                    B_list.append(pdfb)

            pdfA = merge_pdfs(A_list) if A_list else None
            pdfB = merge_pdfs(B_list) if B_list else None

            tabA, tabB = st.tabs(["📁 Grupo A", "📁 Grupo B"])
            with tabA:
                if pdfA:
                    st.success("PDF Grupo A generado")
                    pdf_iframe(pdfA, height=520)
                    st.download_button("⬇️ Descargar Grupo A", data=pdfA, file_name=(nameA or "Grupo_A.pdf"),
                                       mime="application/pdf", use_container_width=True)
                else:
                    st.info("Ningún elemento fue asignado al Grupo A.")
            with tabB:
                if pdfB:
                    st.success("PDF Grupo B generado")
                    pdf_iframe(pdfB, height=520)
                    st.download_button("⬇️ Descargar Grupo B", data=pdfB, file_name=(nameB or "Grupo_B.pdf"),
                                       mime="application/pdf", use_container_width=True)
                else:
                    st.info("Ningún elemento resultó para el Grupo B.")

            # Descargar ambos PDFs a la vez (SIN ZIP) mediante HTML+JS
            if pdfA and pdfB:
                nameA_safe = safe_download_name(nameA or "Grupo_A.pdf")
                nameB_safe = safe_download_name(nameB or "Grupo_B.pdf")
                b64A = base64.b64encode(pdfA).decode("utf-8")
                b64B = base64.b64encode(pdfB).decode("utf-8")
                html = f"""
                <div style="margin-top:8px;">
                  <button onclick="(function() {{
                    (function(){{
                      const a=document.createElement('a');
                      a.href='data:application/pdf;base64,{b64A}';
                      a.download='{nameA_safe}';
                      document.body.appendChild(a); a.click(); document.body.removeChild(a);
                    }})();
                    (function(){{
                      const a=document.createElement('a');
                      a.href='data:application/pdf;base64,{b64B}';
                      a.download='{nameB_safe}';
                      document.body.appendChild(a); a.click(); document.body.removeChild(a);
                    }})();
                  }})()" style="padding:8px 12px;border:1px solid #ccc;border-radius:8px;cursor:pointer;">
                    ⬇️ Descargar ambos PDFs
                  </button>
                </div>
                """
                st.components.v1.html(html, height=60)
            elif (pdfA and not pdfB) or (pdfB and not pdfA):
                st.info("Sólo se generó uno de los grupos; usa el botón de descarga individual.")
            else:
                st.info("No hay PDFs de grupos para descargar.")
        except Exception as e:
            st.exception(e)
            st.error("Ocurrió un error al generar los PDFs por grupos.")

