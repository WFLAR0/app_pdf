# app.py
# ─────────────────────────────────────────────────────────────────────────────
# 🧩 Recortes + Archivos → PDF único o por grupos (con orden numérico y drag&drop)
# Requisitos sugeridos (requirements.txt):
# streamlit, pypdf, pillow, python-docx, reportlab, streamlit-sortable
# ─────────────────────────────────────────────────────────────────────────────

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
from PIL import Image, ImageOps, UnidentifiedImageError, ImageFilter

# --- PDF: usar pypdf; si no está, fallback a PyPDF2 ---
try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError:
    from PyPDF2 import PdfReader, PdfWriter

# --- DOCX → PDF: sólo intentarlo en Windows/macOS (en Linux/Cloud suele fallar) ---
docx2pdf_convert = None
if platform.system().lower() in ("windows", "darwin"):
    try:
        from docx2pdf import convert as docx2pdf_convert
    except Exception:
        docx2pdf_convert = None

# --- Portapapeles (recortes): sólo Windows/macOS ---
ImageGrab = None
if platform.system().lower() in ("windows", "darwin"):
    try:
        from PIL import ImageGrab  # type: ignore
    except Exception:
        ImageGrab = None

# --- DOCX lectura de texto (para fallback) ---
try:
    from docx import Document
except Exception:
    Document = None

# --- ReportLab para imagen→PDF exacto (mejor calidad) ---
try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader as RL_ImageReader
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# --- Drag & drop ordering ---
try:
    from streamlit_sortable import st_sortable  # pip install streamlit-sortable
    HAS_SORTABLE = True
except Exception:
    HAS_SORTABLE = False

SUPPORTED_EXTS = {"pdf", "png", "jpg", "jpeg", "docx"}

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
    """
    Convierte una imagen a PDF con página del MISMO tamaño de la imagen (sin márgenes).
    Usa ReportLab si está disponible; si no, fallback de PIL.
    """
    try:
        img = img.convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        # ReportLab (recomendado)
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
        # Fallback PIL
        out = io.BytesIO(); img.save(out, format="PDF"); out.seek(0); return out.getvalue()
    except Exception:
        return None

def docx_to_pdf_bytes(docx_bytes: bytes, title: Optional[str] = None) -> bytes:
    """
    Convierte DOCX a PDF. Si hay docx2pdf (Windows/macOS), lo usa.
    Fallback: extrae texto y lo renderiza en una página imagen→PDF.
    """
    # Opción 1: docx2pdf (sólo Win/mac)
    if docx2pdf_convert is not None:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "tmp.docx"); dst = os.path.join(td, "out.pdf")
                with open(src, "wb") as f: f.write(docx_bytes)
                docx2pdf_convert(src, dst)
                with open(dst, "rb") as f: return f.read()
        except Exception:
            pass
    # Opción 2: Fallback simple (texto a imagen, luego imagen→PDF)
    text = safe_read_docx_text(docx_bytes) or "(No se pudo extraer texto del DOCX)"
    # Imagen A4 aproximada @ ~200dpi (1654x2339)
    from PIL import ImageDraw, ImageFont
    page = Image.new("RGB", (1654, 2339), "white")
    try:
        draw = ImageDraw.Draw(page)
        font = ImageFont.load_default()
        x, y, lh, maxw = 40, 40, 16, page.width - 80
        if title:
            draw.text((x, y), title, fill="black", font=font); y += lh*2
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

# Helpers de orden
def _ensure_item_defaults(item: Dict[str, Any], kind: str) -> None:
    # kind: 'recorte' | 'archivo'
    if 'id' not in item:
        item['id'] = f"{kind}_{int(time.time()*1000)}_{os.path.basename(item.get('name','item'))}"
    if 'include' not in item:
        item['include'] = True
    if 'order' not in item:
        item['order'] = 1

def next_global_order() -> int:
    vals = []
    for it in st.session_state.get('recortes', []):
        _ensure_item_defaults(it, 'recorte'); vals.append(int(it['order']))
    for it in st.session_state.get('archivos', []):
        _ensure_item_defaults(it, 'archivo'); vals.append(int(it['order']))
    return (max(vals) if vals else 0) + 1

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Recortes + Archivos → PDF combinado y por grupos", layout="wide")
st.title("🧩 Recortes + Archivos → PDF único o por grupos")

# Estado
if "recortes" not in st.session_state:
    # {id,name,bytes,ext,include,order}
    st.session_state.recortes: List[Dict[str, Any]] = []
if "archivos" not in st.session_state:
    st.session_state.archivos: List[Dict[str, Any]] = []

# Normaliza existentes
for it in st.session_state.recortes:
    _ensure_item_defaults(it, 'recorte')
for it in st.session_state.archivos:
    _ensure_item_defaults(it, 'archivo')

# Sidebar
with st.sidebar:
    st.markdown("**Estado:** App cargada ✅")
    st.write(f"Recortes: **{len(st.session_state.recortes)}**  ·  Archivos: **{len(st.session_state.archivos)}**")
    if st.button("🧹 Limpiar todo", use_container_width=True):
        st.session_state.recortes.clear(); st.session_state.archivos.clear(); st.rerun()
    if not HAS_SORTABLE:
        st.info("Para ordenar con el mouse instala: `pip install streamlit-sortable`.")

st.markdown(
    """
- **Importa recortes** (Win+Shift+S en Windows o tu herramienta de recorte en macOS), pega desde el portapapeles y pon **nombre**.
- **Sube archivos** (PDF/PNG/JPG/DOCX).
- Ordena con **números** o con el **mouse** (si tienes `streamlit-sortable`).
- Genera **un único PDF** o **2 PDFs por grupos** (clasificación por **nombre**).
    """
)

# Importar recorte
with st.expander("📥 Importar recorte desde portapapeles", expanded=True):
    if ImageGrab is None:
        st.info("Esta función sólo está disponible en Windows/macOS. En la nube, sube imágenes PNG/JPG desde el panel de archivos.")
    name_input = st.text_input("Nombre del recorte (sin extensión)")
    if st.button("📋 Importar ahora"):
        if ImageGrab is None:
            st.error("No hay acceso al portapapeles en este entorno. Sube la imagen como archivo.")
        elif not name_input.strip():
            st.error("Escribe un nombre para el recorte.")
        else:
            img = None
            try:
                data = ImageGrab.grabclipboard()  # puede devolver Image o lista de rutas
                if isinstance(data, Image.Image):
                    img = data.convert("RGB")
                elif isinstance(data, list) and data:
                    try:
                        with open(data[0], "rb") as f:
                            img = safe_open_image(f.read())
                    except Exception:
                        img = None
            except Exception:
                img = None

            if img is None:
                st.warning("No encontré una imagen en el portapapeles. Haz el recorte e intenta otra vez.")
            else:
                try:
                    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
                except Exception:
                    pass
                buf = io.BytesIO(); img.save(buf, format="PNG", optimize=True); buf.seek(0)
                st.session_state.recortes.append({
                    "id": f"recorte_{int(time.time()*1000)}",
                    "name": re.sub(r"[^A-Za-z0-9_\-]", "_", name_input.strip()) + ".png",
                    "bytes": buf.getvalue(),
                    "ext": "png",
                    "include": True,
                    "order": next_global_order()
                })
                st.success(f"Recorte {name_input}.png agregado")

# Subir archivos
with st.expander("📤 Subir archivos (PDF/PNG/JPG/DOCX)", expanded=True):
    up = st.file_uploader("Selecciona archivos", type=["pdf", "png", "jpg", "jpeg", "docx"], accept_multiple_files=True)
    if st.button("➕ Agregar a la lista"):
        if up:
            for f in up:
                st.session_state.archivos.append({
                    "id": f"archivo_{int(time.time()*1000)}_{f.name}",
                    "name": f.name,
                    "bytes": f.read(),
                    "ext": normalize_ext(f.name),
                    "include": True,
                    "order": next_global_order()
                })
            st.success(f"Se agregaron {len(up)} archivo(s) a la lista.")
        else:
            st.info("No seleccionaste archivos aún.")

# Administrar recortes (orden numérico)
with st.expander("🧾 Tus recortes (orden por números)", expanded=True):
    if not st.session_state.recortes:
        st.info("Aún no hay recortes.")
    else:
        for i, r in enumerate(st.session_state.recortes):
            c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
            new_name = c1.text_input("Nombre", value=os.path.splitext(r["name"])[0], key=f"rec_name_{r['id']}")
            r["include"] = c2.checkbox("Incluir", value=r["include"], key=f"rec_inc_{r['id']}")
            r["order"] = c3.number_input("Orden", value=int(r["order"]), step=1, key=f"rec_ord_{r['id']}")
            if c4.button("🗑️", key=f"rec_del_{r['id']}"):
                st.session_state.recortes.pop(i); st.rerun()
            r["name"] = re.sub(r"[^A-Za-z0-9_\-]", "_", (new_name.strip() or "recorte")) + ".png"
            st.image(r["bytes"], caption=r["name"], use_container_width=True)

# Administrar archivos (orden numérico)
with st.expander("📚 Tus archivos (orden por números)", expanded=True):
    if not st.session_state.archivos:
        st.info("Aún no hay archivos.")
    else:
        for i, a in enumerate(st.session_state.archivos):
            c1, c2, c3, c4 = st.columns([6, 1, 1, 1])
            c1.write(a["name"])
            a["include"] = c2.checkbox("Incluir", value=a["include"], key=f"arc_inc_{a['id']}")
            a["order"] = c3.number_input("Orden", value=int(a["order"]), step=1, key=f"arc_ord_{a['id']}")
            if c4.button("🗑️", key=f"arc_del_{a['id']}"):
                st.session_state.archivos.pop(i); st.rerun()

# Ordenar con el mouse (drag & drop)
with st.expander("↕️ Ordenar con el mouse (drag & drop)", expanded=True):
    included_items = [
        {"id": r['id'], "kind": "recorte", "label": f"🖼️ {r['name']}", "order": int(r['order'])}
        for r in st.session_state.recortes if r["include"]
    ] + [
        {"id": a['id'], "kind": "archivo", "label": f"📄 {a['name']}", "order": int(a['order'])}
        for a in st.session_state.archivos if a["include"]
    ]
    included_items = sorted(included_items, key=lambda x: x["order"])

    if HAS_SORTABLE and included_items:
        data_for_sort = [{"header": it["label"], "body": f"orden: {it['order']}", "id": it["id"], "kind": it["kind"]} for it in included_items]
        new_items = st_sortable(data_for_sort, key="order_list", direction="vertical") or data_for_sort
        for idx, it in enumerate(new_items, start=1):
            if it.get("kind") == "recorte":
                for r in st.session_state.recortes:
                    if r["id"] == it["id"]:
                        r["order"] = idx
            else:
                for a in st.session_state.archivos:
                    if a["id"] == it["id"]:
                        a["order"] = idx
        st.success("Orden actualizado con el mouse.")
    elif not HAS_SORTABLE:
        st.info("Instala `streamlit-sortable` para ordenar con el mouse. Puedes seguir usando el orden numérico.")
    else:
        st.info("No hay elementos incluidos para ordenar.")

# Generar PDF combinado (respeta orden actual)
st.divider()
final_name = st.text_input("📄 Nombre del PDF final (combinado)", value="resultado_combinado.pdf")
if st.button("🛠️ Generar PDF combinado", type="primary"):
    try:
        items_sorted = sorted([*st.session_state.recortes, *st.session_state.archivos], key=lambda x: int(x["order"]))
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

# Generar 2 PDFs por grupos (por nombre)
st.divider()
with st.expander("🗂️ Generar 2 PDFs por grupos (por nombre)", expanded=False):
    st.caption("Define palabras clave para clasificar por **nombre de archivo** (recortes y archivos). Si un ítem no coincide con A, va a B.")
    colA, colB = st.columns(2)
    kwA = colA.text_input("Palabras clave Grupo A (separadas por coma)", value="dni dueño,dni cliente,recibo de luz")
    kwB = colB.text_input("Palabras clave Grupo B (separadas por coma)", value="aval,escritura,recibo notarial")
    ignore_case = st.checkbox("Ignorar mayúsculas/minúsculas", value=True)
    nameA = st.text_input("Nombre PDF Grupo A", value="Grupo_A.pdf")
    nameB = st.text_input("Nombre PDF Grupo B", value="Grupo_B.pdf")

    def _match_any(name: str, csv: str, ignore: bool) -> bool:
        if not csv.strip():
            return False
        toks = [t.strip() for t in csv.split(",") if t.strip()]
        base = name.lower() if ignore else name
        toks = [t.lower() if ignore else t for t in toks]
        return any(t in base for t in toks)

    if st.button("📑 Generar PDFs por grupos", use_container_width=True):
        try:
            items_sorted = sorted([*st.session_state.recortes, *st.session_state.archivos], key=lambda x: int(x["order"]))
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

                if _match_any(it["name"], kwA, ignore_case):
                    A_list.append(pdfb)
                elif _match_any(it["name"], kwB, ignore_case):
                    B_list.append(pdfb)
                else:
                    B_list.append(pdfb)  # por defecto a B si no coincide con A

            pdfA = merge_pdfs(A_list) if A_list else None
            pdfB = merge_pdfs(B_list) if B_list else None

            tabA, tabB = st.tabs(["📁 Grupo A", "📁 Grupo B"])
            with tabA:
                if pdfA:
                    st.success("PDF Grupo A generado")
                    pdf_iframe(pdfA, height=600)
                    st.download_button("⬇️ Descargar Grupo A", data=pdfA, file_name=(nameA or "Grupo_A.pdf"),
                                       mime="application/pdf", use_container_width=True)
                else:
                    st.info("Ningún elemento coincidió con el Grupo A.")
            with tabB:
                if pdfB:
                    st.success("PDF Grupo B generado")
                    pdf_iframe(pdfB, height=600)
                    st.download_button("⬇️ Descargar Grupo B", data=pdfB, file_name=(nameB or "Grupo_B.pdf"),
                                       mime="application/pdf", use_container_width=True)
                else:
                    st.info("Ningún elemento resultó para el Grupo B.")
        except Exception as e:
            st.exception(e)
            st.error("Ocurrió un error al generar los PDFs por grupos.")

st.caption("Hecho con ❤️ en Streamlit · Orden numérico + drag&drop · Recortes con nombre propio · PDF único o 2 PDFs por grupos · Soporta PDF/PNG/JPG/DOCX")
