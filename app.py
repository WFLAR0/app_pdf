import io
import os
import re
import base64
import tempfile
import time
import platform
import datetime as dt
from typing import List, Dict, Any, Optional, Tuple

import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError, ImageFilter, ImageGrab
from pypdf import PdfReader, PdfWriter

try:
    from docx import Document
except Exception:
    Document = None

try:
    from docx2pdf import convert as docx2pdf_convert
except Exception:
    docx2pdf_convert = None

try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader as RL_ImageReader
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

try:
    from streamlit_sortable import st_sortable
    HAS_SORTABLE = True
except Exception:
    HAS_SORTABLE = False

SUPPORTED_EXTS = {"pdf", "png", "jpg", "jpeg", "docx"}

# ===== Utilidades =====

def bytesio(data: bytes) -> io.BytesIO:
    bio = io.BytesIO(); bio.write(data); bio.seek(0); return bio

def normalize_ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().strip(".")

def safe_open_image(img_bytes: bytes) -> Optional[Image.Image]:
    try:
        with Image.open(bytesio(img_bytes)) as img:
            img.load(); img = ImageOps.exif_transpose(img)
            return img.convert("RGB")
    except Exception:
        return None

def image_to_pdf_exact_bytes(img: Image.Image) -> Optional[bytes]:
    try:
        img = img.convert("RGB"); w, h = img.size
        if w <= 0 or h <= 0: return None
        if REPORTLAB_OK:
            buf = io.BytesIO(); c = rl_canvas.Canvas(buf, pagesize=(w, h))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            try:
                img.save(tmp.name, format="JPEG", quality=95, subsampling=0, optimize=True)
                c.drawImage(RL_ImageReader(tmp.name), 0, 0, width=w, height=h, preserveAspectRatio=False, mask='auto')
                c.showPage(); c.save(); buf.seek(0)
                return buf.getvalue()
            finally:
                try: os.unlink(tmp.name)
                except: pass
        out = io.BytesIO(); img.save(out, format="PDF"); out.seek(0); return out.getvalue()
    except Exception:
        return None

def docx_to_pdf_bytes(docx_bytes: bytes, title: Optional[str] = None) -> bytes:
    if docx2pdf_convert is not None:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "tmp.docx"); dst = os.path.join(td, "out.pdf")
                with open(src, "wb") as f: f.write(docx_bytes)
                docx2pdf_convert(src, dst)
                with open(dst, "rb") as f: return f.read()
        except Exception: pass
    return b""

def merge_pdfs(pdf_bytes_list: List[bytes]) -> bytes:
    writer = PdfWriter()
    for data in pdf_bytes_list:
        try:
            reader = PdfReader(bytesio(data))
            for page in reader.pages:
                writer.add_page(page)
        except Exception: continue
    out = io.BytesIO(); writer.write(out); out.seek(0); return out.getvalue()

def pdf_iframe(pdf_bytes: bytes, height: int = 640) -> None:
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    html = f"<iframe src=\"data:application/pdf;base64,{b64}\" width=\"100%\" height=\"{height}\"></iframe>"
    st.components.v1.html(html, height=height + 12, scrolling=False)

# ===== App =====

st.set_page_config(page_title="Recortes + Archivos → PDF combinado y por grupos", layout="wide")
st.title("🧩 Recortes + Archivos → PDF único o por grupos")

if "recortes" not in st.session_state:
    st.session_state.recortes = []
if "archivos" not in st.session_state:
    st.session_state.archivos = []

with st.sidebar:
    if st.button("🧹 Limpiar todo", use_container_width=True):
        st.session_state.recortes.clear(); st.session_state.archivos.clear()

# ===== Importar recorte =====
with st.expander("📥 Importar recorte", expanded=True):
    name_input = st.text_input("Nombre del recorte")
    if st.button("📋 Importar ahora"):
        if not name_input.strip():
            st.error("Escribe un nombre para el recorte.")
        else:
            img = ImageGrab.grabclipboard()
            if img:
                buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
                st.session_state.recortes.append({"name": name_input + ".png", "bytes": buf.getvalue(), "ext": "png", "include": True, "order": len(st.session_state.recortes)+1})
                st.success(f"Recorte {name_input}.png agregado")

# ===== Subir archivos =====
with st.expander("📤 Subir archivos"):
    up = st.file_uploader("Archivos (PDF/PNG/JPG/DOCX)", type=["pdf","png","jpg","jpeg","docx"], accept_multiple_files=True)
    if st.button("➕ Agregar"):
        if up:
            for f in up:
                st.session_state.archivos.append({"name": f.name, "bytes": f.read(), "ext": normalize_ext(f.name), "include": True, "order": len(st.session_state.archivos)+1})
            st.success("Archivos agregados")

# ===== Ordenar =====
all_items = [*st.session_state.recortes, *st.session_state.archivos]

# ===== Generar PDF combinado =====
final_name = st.text_input("📄 Nombre del PDF final", value="resultado_combinado.pdf")
if st.button("Generar PDF combinado"):
    pdfs = []
    for it in all_items:
        if not it.get("include", True): continue
        ext = it["ext"]
        if ext == "pdf":
            pdfs.append(it["bytes"])
        elif ext in ("png","jpg","jpeg"):
            img = safe_open_image(it["bytes"])
            if img: pdfs.append(image_to_pdf_exact_bytes(img))
        elif ext == "docx":
            pdfs.append(docx_to_pdf_bytes(it["bytes"]))
    if pdfs:
        combined = merge_pdfs(pdfs)
        pdf_iframe(combined)
        st.download_button("⬇️ Descargar", combined, file_name=final_name, mime="application/pdf")

# ===== Generar 2 PDFs por grupos =====
with st.expander("🗂️ Generar 2 PDFs por grupos"):
    kwA = st.text_input("Palabras clave Grupo A", value="dni dueño,dni cliente,recibo de luz")
    kwB = st.text_input("Palabras clave Grupo B", value="aval,escritura,recibo notarial")
    nameA = st.text_input("Nombre PDF Grupo A", value="Grupo_A.pdf")
    nameB = st.text_input("Nombre PDF Grupo B", value="Grupo_B.pdf")
    if st.button("Generar PDFs por grupos"):
        def _match_any(name, csv):
            toks = [t.strip().lower() for t in csv.split(',') if t.strip()]
            return any(t in name.lower() for t in toks)
        A,B=[],[]
        for it in all_items:
            if not it.get("include", True): continue
            ext = it["ext"]; pdfb=None
            if ext=="pdf": pdfb=it["bytes"]
            elif ext in("png","jpg","jpeg"):
                img=safe_open_image(it["bytes"]); pdfb=image_to_pdf_exact_bytes(img) if img else None
            elif ext=="docx": pdfb=docx_to_pdf_bytes(it["bytes"])
            if not pdfb: continue
            if _match_any(it["name"], kwA): A.append(pdfb)
            elif _match_any(it["name"], kwB): B.append(pdfb)
            else: B.append(pdfb)
        if A:
            pdfA=merge_pdfs(A); st.subheader("Grupo A"); pdf_iframe(pdfA); st.download_button("⬇️ Descargar Grupo A", pdfA, file_name=nameA)
        if B:
            pdfB=merge_pdfs(B); st.subheader("Grupo B"); pdf_iframe(pdfB); st.download_button("⬇️ Descargar Grupo B", pdfB, file_name=nameB)
