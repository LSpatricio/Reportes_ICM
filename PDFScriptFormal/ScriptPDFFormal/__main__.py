import csv
from datetime import datetime
import glob
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import base64
import json
import concurrent.futures
import shutil
import logging
import subprocess

LIB_PATH = "/root/Script/Library"
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from dotenv import load_dotenv



#===================================== NUBE ICM / RUTAS =====================================

# Detecta desde qué carpeta está corriendo realmente el script.
# Esto permite soportar ejecución local y rutas resueltas desde un entorno empacado.
def get_runtime_base_dir():
    entry_path = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else os.path.realpath(__file__)
    if os.path.isfile(entry_path):
        return os.path.dirname(entry_path)
    return entry_path


# Rutas base del script, de root/scripts y del proyecto completo.
scriptDir = os.path.dirname(os.path.realpath(__file__))
scriptsRoot = os.path.dirname(scriptDir)
projectRoot = os.path.dirname(scriptsRoot)
runtimeBaseDir = get_runtime_base_dir()
runtimeScriptsRoot = os.path.dirname(runtimeBaseDir)
runtimeProjectRoot = os.path.dirname(runtimeScriptsRoot)
rootDir = os.path.dirname(projectRoot)


# Resuelve la carpeta Data más adecuada según el contexto de ejecución.
def get_runtime_data_dir():
    preferred_paths = [
        os.path.join(runtimeBaseDir, "Data"),
        os.path.join(runtimeScriptsRoot, "Data"),
        os.path.join(runtimeProjectRoot, "Data"),
        os.path.join(projectRoot, "Data"),
    ]
    for candidate in preferred_paths:
        if os.path.isdir(candidate):
            return candidate
    return preferred_paths[1]


def migrate_legacy_directory(base_dir, old_name, new_name):
    old_path = os.path.join(base_dir, old_name)
    new_path = os.path.join(base_dir, new_name)
    if os.path.isdir(old_path) and not os.path.exists(new_path):
        os.rename(old_path, new_path)
    return new_path


def migrate_legacy_settings_dir():
    for base_dir in (runtimeBaseDir, runtimeScriptsRoot, scriptDir, scriptsRoot):
        migrate_legacy_directory(base_dir, "PDFFiniquito", "Settings")


# Directorios operativos principales del flujo.
DATA_DIR = get_runtime_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
PDFS_FINIQUITOS_DIR = migrate_legacy_directory(DATA_DIR, "PDFs Finiquitos", "PDFs Formales")
os.makedirs(PDFS_FINIQUITOS_DIR, exist_ok=True)
migrate_legacy_settings_dir()
LOGS_DIR = os.path.join(scriptsRoot, "Logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Devuelve la primera ruta existente de una lista de candidatos.
def resolve_runtime_dir(preferred_paths):
    for candidate in preferred_paths:
        if os.path.isdir(candidate):
            return candidate
    return preferred_paths[0]


# Carpetas base para datos, templates, proceso y configuración.
dataDir = resolve_runtime_dir([
    os.path.join(runtimeBaseDir, "Data"),
    os.path.join(scriptDir, "Data"),
    os.path.join(runtimeProjectRoot, "Data"),
    os.path.join(projectRoot, "Data"),
    os.path.join(rootDir, "Data"),
])
templateDir = resolve_runtime_dir([
    os.path.join(runtimeBaseDir, "TemplatePDF"),
    os.path.join(runtimeScriptsRoot, "TemplatePDF"),
    os.path.join(runtimeProjectRoot, "TemplatePDF"),
    os.path.join(scriptDir, "TemplatePDF"),
    os.path.join(scriptsRoot, "TemplatePDF"),
    os.path.join(projectRoot, "TemplatePDF"),
])
processDir = resolve_runtime_dir([
    os.path.join(runtimeBaseDir, "Settings"),
    os.path.join(runtimeScriptsRoot, "Settings"),
    os.path.join(scriptDir, "Settings"),
    os.path.join(scriptsRoot, "Settings"),
    os.path.join(projectRoot, "Settings"),

])
configDir = resolve_runtime_dir([
    os.path.join(runtimeBaseDir, "Settings"),
    os.path.join(runtimeScriptsRoot, "Settings"),
    os.path.join(scriptDir, "Settings"),
    os.path.join(scriptsRoot, "Settings"),
    os.path.join(projectRoot, "Settings"),
])


# Identificadores y archivos principales del proceso.
SCRIPT_NAME="PDFScriptFormal"

apiConfigFile    = os.path.join(configDir, "api.json")           #ICM API configuration
scriptConfigFile = os.path.join(configDir, "ConfigPDFScriptFormal.json")  #script specific config
logFile          = os.path.join(LOGS_DIR, SCRIPT_NAME + ".log")   # central logs folder for all script files.
VERSION = "v1.0.0"


# Tipografia y tolerancias base del overlay formal.
HEADER_FONT_SIZE = 8.8
BODY_FONT_SIZE = 7.0
BODY_SMALL_FONT_SIZE = 5.9
TOTAL_FONT_SIZE = 6.8
FOOTER_FONT_SIZE = 6.0
ZERO_EPSILON = 0.004

# Variables de entorno compartidas del proyecto.
# dotenv_path = os.path.join(scriptsRoot, ".env")
if not os.path.exists(os.path.join(scriptsRoot, ".env")):
    dotenv_path = os.path.join(projectRoot, ".env")
else:
    dotenv_path = os.path.join(scriptsRoot, ".env")
load_dotenv(dotenv_path)
load_dotenv()


class batchPDF(Exception):
    pass


# Layout mínimo sobre el que luego se fusionan el JSON y el metadata del RDL.
DEFAULT_TEMPLATE_LAYOUT = {
    "page_index": 0,
    "font_name": "Helvetica",
    "font_size": 10,
    "rdl": {},
}


def sanitize_filename(value, fallback="documento"):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", str(value or "")).strip(" ._")
    return cleaned or fallback


def format_month_year_token(row):
    month_text = str(row.get("mes", "") or "").strip()
    if month_text:
        match = re.search(r"(\d{4})[-/](\d{2})", month_text)
        if match:
            year = match.group(1)
            month_number = int(match.group(2))
            month_abbr = [
                "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
                "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"
            ][month_number - 1]
            return f"{month_abbr}{year}"

        month_word_match = re.search(
            r"\b(ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)[A-Z]*\b",
            month_text.upper()
        )
        year_match = re.search(r"(20\d{2})", month_text)
        if month_word_match and year_match:
            return f"{month_word_match.group(1)}{year_match.group(1)}"

    start_date = str(row.get("FECHAINICIAL", "") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date):
        date_value = datetime.strptime(start_date, "%Y-%m-%d")
        month_abbr = [
            "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
            "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"
        ][date_value.month - 1]
        return f"{month_abbr}{date_value.year}"

    return "MESANIO"


def get_config_value(section, key, default=None):
    if section is None:
        return default

    if hasattr(section, "get"):
        try:
            return section.get(key, default)
        except TypeError:
            pass

    try:
        return section[key]
    except Exception:
        return default


def get_configuration_section(script_config):
    configuration = script_config.get("CONFIGURATION")
    if not isinstance(configuration, dict):
        raise batchPDF("El archivo de configuracion debe incluir un objeto 'CONFIGURATION'.")
    return configuration


# Carga requests solo cuando realmente se necesita el modo remoto.
def get_requests_module():
    try:
        import requests
        return requests
    except ModuleNotFoundError as exc:
        raise batchPDF(
            "El modo remoto requiere la libreria 'requests'. "
            "En ejecucion LOCAL_TEMPLATE no deberia cargarse."
        ) from exc


def parse_bool(value, default=False):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in {"1", "true", "yes", "si", "on"}


def parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_reportlab_font_size(value, default):
    if value in (None, ""):
        return default

    text = str(value).strip().lower().replace("pt", "")
    return parse_float(text, default)


def parse_optional_int(value, default=None):
    if value in (None, ""):
        return default


def decode_env_base64(name):
    value = os.getenv(name)
    if not value:
        return None
    try:
        return base64.b64decode(value).decode("utf-8")
    except Exception:
        return value

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def get_first_row_value(row, keys, default=""):
    for key in keys:
        value = get_row_value(row, key, None)
        if value not in (None, ""):
            return value
    return default

def sum_row_fields(row, field_names):
    total = 0.0
    for field_name in field_names:
        total += safe_float(get_row_value(row, field_name), 0.0)
    return total


def format_currency_value(value):
    number = safe_float(value, default=0.0)
    if number < 0:
        return f"(${abs(number):,.2f})"
    return f"${number:,.2f}"


def format_date_value(value):
    if value in (None, ""):
        return ""
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def parse_row_date(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def get_spanish_month_name_from_date(value):
    date_value = parse_row_date(value)
    if date_value is None:
        return ""
    return {
        1: "Enero",
        2: "Febrero",
        3: "Marzo",
        4: "Abril",
        5: "Mayo",
        6: "Junio",
        7: "Julio",
        8: "Agosto",
        9: "Septiembre",
        10: "Octubre",
        11: "Noviembre",
        12: "Diciembre",
    }[date_value.month]

def obtener_anio_periodo(periodo):
    return periodo.split("-")[0]

def format_spanish_datetime(value=None):
    date_value = value if isinstance(value, datetime) else datetime.now()
    weekday_names = {
        0: "lunes",
        1: "martes",
        2: "miercoles",
        3: "jueves",
        4: "viernes",
        5: "sabado",
        6: "domingo",
    }
    month_names = {
        1: "enero",
        2: "febrero",
        3: "marzo",
        4: "abril",
        5: "mayo",
        6: "junio",
        7: "julio",
        8: "agosto",
        9: "septiembre",
        10: "octubre",
        11: "noviembre",
        12: "diciembre",
    }
    return (
        f"{weekday_names[date_value.weekday()]}, {date_value.day:02d} de "
        f"{month_names[date_value.month]} de {date_value.year}, {date_value:%H:%M}"
    )


def format_spanish_date(value=None):
    date_value = value if isinstance(value, datetime) else datetime.now()
    weekday_names = {
        0: "lunes",
        1: "martes",
        2: "miercoles",
        3: "jueves",
        4: "viernes",
        5: "sabado",
        6: "domingo",
    }
    month_names = {
        1: "enero",
        2: "febrero",
        3: "marzo",
        4: "abril",
        5: "mayo",
        6: "junio",
        7: "julio",
        8: "agosto",
        9: "septiembre",
        10: "octubre",
        11: "noviembre",
        12: "diciembre",
    }
    return (
        f"{weekday_names[date_value.weekday()]} {date_value.day:02d} de "
        f"{month_names[date_value.month]} de {date_value.year}"
    )


def fit_font_size(text, font_name, max_width, initial_size, min_size=6):
    text = "" if text is None else str(text)
    size = initial_size
    while size > min_size and pdfmetrics.stringWidth(text, font_name, size) > max_width:
        size -= 0.5
    return size


def trim_text_to_width(text, font_name, font_size, max_width, suffix="..."):
    text = "" if text is None else str(text)
    if not max_width or pdfmetrics.stringWidth(text, font_name, font_size) <= max_width:
        return text

    trimmed = text
    while trimmed:
        candidate = trimmed.rstrip() + suffix
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            return candidate
        trimmed = trimmed[:-1]
    return suffix


def normalize_single_line_text(value):
    if value is None:
        return ""
    return re.sub(r"\s*\r?\n\s*", " ", str(value)).strip()


def normalize_identifier_text(value):
    if value is None:
        return ""

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return re.sub(r"\.0+$", "", str(value))

    text = normalize_single_line_text(value)
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def draw_text(canvas_obj, x, y, text, font_name="Helvetica", font_size=9, max_width=None, min_size=6, trim_overflow=False, shrink_to_fit=True):
    text = "" if text is None else str(text)
    if max_width and shrink_to_fit:
        font_size = fit_font_size(text, font_name, max_width, font_size, min_size=min_size)
    if max_width and trim_overflow:
        text = trim_text_to_width(text, font_name, font_size, max_width)
    canvas_obj.setFont(font_name, font_size)
    canvas_obj.drawString(x, y, text)


def draw_right_text(canvas_obj, right_x, y, text, font_name="Helvetica", font_size=9, max_width=None, min_size=6):
    text = "" if text is None else str(text)
    if max_width:
        font_size = fit_font_size(text, font_name, max_width, font_size, min_size=min_size)
    canvas_obj.setFont(font_name, font_size)
    canvas_obj.drawRightString(right_x, y, text)


def draw_center_text(canvas_obj, center_x, y, text, font_name="Helvetica", font_size=9, max_width=None, min_size=6, trim_overflow=False, shrink_to_fit=True):
    text = "" if text is None else str(text)
    if max_width and shrink_to_fit:
        font_size = fit_font_size(text, font_name, max_width, font_size, min_size=min_size)
    if max_width and trim_overflow:
        text = trim_text_to_width(text, font_name, font_size, max_width)
    canvas_obj.setFont(font_name, font_size)
    text_width = pdfmetrics.stringWidth(text, font_name, font_size)
    canvas_obj.drawString(center_x - (text_width / 2), y, text)


def clear_overlay_area(canvas_obj, x, y, width, height):
    canvas_obj.saveState()
    canvas_obj.setFillColorRGB(1, 1, 1)
    canvas_obj.setStrokeColorRGB(1, 1, 1)
    canvas_obj.rect(x, y, width, height, stroke=0, fill=1)
    canvas_obj.restoreState()


def draw_right_currency(
    canvas_obj,
    right_x,
    y,
    raw_value,
    font_name="Helvetica-Bold",
    font_size=6,
    max_width=80,
    text_rgb=None,
    allow_negative_red=True,
    display_absolute_value=False,
):
    number = safe_float(raw_value, default=0.0)
    if display_absolute_value:
        number = abs(number)

    if text_rgb is not None:
        canvas_obj.saveState()
        canvas_obj.setFillColorRGB(*text_rgb)
        draw_right_text(
            canvas_obj,
            right_x,
            y,
            format_currency_value(number),
            font_name=font_name,
            font_size=font_size,
            max_width=max_width
        )
        canvas_obj.restoreState()
        return

    if allow_negative_red and number < 0:
        canvas_obj.saveState()
        canvas_obj.setFillColorRGB(1, 0, 0)
        draw_right_text(
            canvas_obj,
            right_x,
            y,
            format_currency_value(number),
            font_name=font_name,
            font_size=font_size,
            max_width=max_width
        )
        canvas_obj.restoreState()
        return

    draw_right_text(
        canvas_obj,
        right_x,
        y,
        format_currency_value(number),
        font_name=font_name,
        font_size=font_size,
        max_width=max_width
    )


def calculate_total_a_pagar(row):
    # Total final mostrado en la esquina inferior derecha del finiquito.
    return (
        safe_float(get_row_value(row, "totalcomision"), 0.0)
        - (
            safe_float(get_row_value(row, "totalanticipos_recup"), 0.0)
            + safe_float(get_row_value(row, "dev_aguinaldo_recup"), 0.0)
        )
        - safe_float(get_row_value(row, "mermatotal_facturad"), 0.0)
        - safe_float(get_row_value(row, "mermatotal_nofac"), 0.0)
    )


def calculate_flujo_mensual(row):
    # Flujo mensual usado en el resumen final inferior.
    return (
        calculate_total_a_pagar(row)
        + safe_float(get_row_value(row, "aportcajaahorro_mensual"), 0.0)
        + safe_float(get_row_value(row, "antiliderescom_mensual"), 0.0)
    )


def get_row_value(row, key, default=""):
    for candidate in (key, str(key).upper(), str(key).lower()):
        if candidate in row:
            value = row.get(candidate)
            return default if value is None else value
    return default


def row_has_field(row, key):
    for candidate in (key, str(key).upper(), str(key).lower()):
        if candidate in row:
            return True
    return False


def draw_paragraph_text(canvas_obj, x, y, text, max_width, font_name="Helvetica", font_size=8, line_gap=9):
    text = "" if text is None else str(text).strip()
    if not text:
        return

    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    for index, line in enumerate(lines):
        draw_text(canvas_obj, x, y - (index * line_gap), line, font_name=font_name, font_size=font_size, max_width=max_width)


def draw_center_paragraph_text(canvas_obj, center_x, y, text, max_width, font_name="Helvetica", font_size=8, line_gap=9):
    text = "" if text is None else str(text).strip()
    if not text:
        return

    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    for index, line in enumerate(lines):
        draw_center_text(
            canvas_obj,
            center_x,
            y - (index * line_gap),
            line,
            font_name=font_name,
            font_size=font_size,
            max_width=max_width,
            trim_overflow=True,
            shrink_to_fit=False,
        )


def is_layout_block_enabled(block_cfg, default=True):
    if not isinstance(block_cfg, dict):
        return default
    return parse_bool(block_cfg.get("enabled"), default=default)


def build_second_signature_text(row):
    total_comision = format_currency_value(get_row_value(row, "TOTALCOMISION"))
    period_text = str(get_row_value(row, "MES") or "").strip()
    if period_text:
        periodo_fragment = f"correspondiente al periodo {period_text}"
    else:
        fecha_final = parse_row_date(get_row_value(row, "FECHAFINAL"))
        mes = get_spanish_month_name_from_date(get_row_value(row, "FECHAFINAL"))
        anio = str(fecha_final.year) if fecha_final else ""
        periodo_fragment = f"correspondiente al Mes de {mes} del Ano {anio}".strip()

    return (
        "Recibi de Cadena Comercial OXXO SA de CV, la cantidad de "
        f"{total_comision} por concepto de Pago Total de Comision Mercantil "
        f"{periodo_fragment} acreditando con la "
        "presente que estoy recibiendo de conformidad el Pago Total de la Factura "
        "emitida por este concepto, por lo cual deslindo a Cadena Comercial Oxxo, "
        "S.A. de C.V., de cualquier responsabilidad surgida con motivo de la "
        "Facturacion correspondiente a este mes."
    )


def build_signature_body_text(row):
    period_text = str(get_row_value(row, "MES") or "").strip()
    if period_text:
        periodo_fragment = f"correspondiente al periodo {period_text}"
    else:
        periodo_fragment = "correspondiente al periodo acreditado"

    return (
        "Total de Comision Mercantil "
        f"{periodo_fragment} con la presente que estoy recibiendo de "
        "conformidad de Pago Total de la Factura emitida por este concepto, "
        "por lo cual deslindo a Cadena Comercial OXXO,S.A. de C.V.,de "
        "Cualquier responsabilidad surgida con motivo de la Facturacion "
        "correspondiente a este mes."
    )


def build_signature_receipt_line(row, prefix_text, suffix_text):
    amount_text = get_signature_total_amount(row)
    prefix_text = str(prefix_text or "").strip()
    suffix_text = str(suffix_text or "").strip()
    return f"{prefix_text} {amount_text} {suffix_text}".strip()


def build_header_comisionista_text(row):
    commission_id = normalize_identifier_text(get_row_value(row, "COMISIONID"))
    commission_name = normalize_single_line_text(get_row_value(row, "NOMBRECOMISIONISTA"))
    if commission_id and commission_name:
        return f"({commission_id}) {commission_name}"
    return commission_name or commission_id


def get_signature_total_amount(row):
    amount_text = format_currency_value(get_row_value(row, "TOTALCOMISION"))
    if amount_text.startswith("($") and amount_text.endswith(")"):
        return f"({amount_text[2:-1]})"
    if amount_text.startswith("$"):
        return amount_text[1:]
    return amount_text



def draw_text_with_parenthesis_below(
    canvas_obj,
    x,
    y,
    text,
    font_name="Helvetica",
    font_size=9,
    max_width=None,
    min_size=6,
    trim_overflow=False,
    line_gap=9,
    shrink_to_fit=True,
):
    text = "" if text is None else str(text).strip()
    if not text:
        return

    match = re.match(r"^(.*?)\s*(\(.+\))$", text)
    if not match:
        draw_text(
            canvas_obj,
            x,
            y,
            text,
            font_name=font_name,
            font_size=font_size,
            max_width=max_width,
            min_size=min_size,
            trim_overflow=trim_overflow,
            shrink_to_fit=shrink_to_fit
        )
        return

    main_text = match.group(1).strip()
    bottom_text = match.group(2).strip()

    if main_text:
        draw_text(
            canvas_obj,
            x,
            y,
            main_text,
            font_name=font_name,
            font_size=font_size,
            max_width=max_width,
            min_size=min_size,
            trim_overflow=trim_overflow,
            shrink_to_fit=shrink_to_fit
        )

    draw_text(
        canvas_obj,
        x,
        y - line_gap,
        bottom_text,
        font_name=font_name,
        font_size=font_size,
        max_width=max_width,
        min_size=min_size,
        trim_overflow=trim_overflow,
        shrink_to_fit=shrink_to_fit
    )


def resolve_path(path_value, base_dirs):
    # Resuelve rutas absolutas o relativas del config.
    # Tolera variantes como root/..., Script/... y scripts/... para que el mismo
    # JSON funcione en local y en ICM sin tener que duplicar configuraciones.
    if not path_value:
        return None

    if isinstance(base_dirs, (list, tuple, set)):
        normalized_base_dirs = [str(base_dir) for base_dir in base_dirs if base_dir]
    else:
        normalized_base_dirs = [str(base_dirs)] if base_dirs else []

    normalized = os.path.expandvars(os.path.expanduser(str(path_value).strip()))
    if os.path.isabs(normalized):
        return normalized

    normalized = normalized.replace("/", os.sep).replace("\\", os.sep)

    relative_candidates = []
    for candidate in (
        normalized,
        normalized.removeprefix(f"root{os.sep}"),
        normalized.removeprefix(f"Script{os.sep}"),
        normalized.removeprefix(f"scripts{os.sep}"),
    ):
        if candidate and candidate not in relative_candidates:
            relative_candidates.append(candidate)

    for base_dir in normalized_base_dirs:
        for relative_path in relative_candidates:
            candidate_path = os.path.normpath(os.path.join(base_dir, relative_path))
            if os.path.exists(candidate_path):
                return candidate_path

    if normalized_base_dirs:
        return os.path.normpath(os.path.join(normalized_base_dirs[0], relative_candidates[0]))
    return os.path.normpath(normalized)


def resolve_existing_path(path_value, base_dirs):
    resolved_path = resolve_path(path_value, base_dirs)
    if resolved_path and os.path.exists(resolved_path):
        return resolved_path
    return ""


def ensure_directory(path_value):
    os.makedirs(path_value, exist_ok=True)
    return path_value


def write_rows_to_csv(csv_path, rows, headers=None):
    ensure_directory(os.path.dirname(csv_path) or ".")
    normalized_rows = []
    computed_headers = list(headers or [])

    for row in rows:
        if isinstance(row, dict):
            normalized_row = {str(key): row.get(key) for key in row.keys()}
            normalized_rows.append(normalized_row)
            for key in normalized_row.keys():
                if key not in computed_headers:
                    computed_headers.append(key)
        else:
            values = list(row) if isinstance(row, (list, tuple)) else [row]
            if not computed_headers:
                computed_headers = [f"col_{index}" for index in range(len(values))]
            normalized_rows.append(
                {
                    computed_headers[index] if index < len(computed_headers) else f"col_{index}": values[index]
                    for index in range(len(values))
                }
            )

    if not computed_headers and normalized_rows:
        computed_headers = list(normalized_rows[0].keys())

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=computed_headers, delimiter=";")
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow({header: row.get(header, "") for header in computed_headers})

    return csv_path


def export_customtable_view_to_csv(api_url, headers, table_name, output_csv):
    # Exporta una vista auxiliar desde ICM a un CSV local para el modo LOCAL_TEMPLATE.
    requests = get_requests_module()
    table_url = api_url + f"api/v1/customtables/{table_name}/inputforms/0/data?limit=100000"
    response = requests.request("GET", table_url, headers=headers, timeout=120)
    response.raise_for_status()

    payload = response.json()
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise batchPDF(f"La tabla {table_name} no devolvió una lista válida en 'data'.")

    header_candidates = None
    for key in ("columns", "headers", "fieldNames"):
        candidate = payload.get(key)
        if isinstance(candidate, list) and candidate:
            header_candidates = [str(value) for value in candidate]
            break

    csv_path = write_rows_to_csv(output_csv, rows, headers=header_candidates)
    print(f"[REMOTE] CSV de consulta generado para {table_name}: {csv_path}")
    return csv_path


def get_runtime_api_config():
    # Recupera token/modelo desde variables de entorno y normaliza la URL base de la API.
    api_url = os.getenv("API_URL")
    if not api_url:
        api_url = "https://api.cloud.varicent.com/"
    if not api_url.endswith("/"):
        api_url += "/"

    model = decode_env_base64("model")
    api_user_key = decode_env_base64("API_KEY")

    if not model or not api_user_key:
        raise batchPDF("No fue posible resolver credenciales API desde el entorno actual.")

    return {
        "API_URL": api_url,
        "MODEL": model,
        "API_USER_KEY": api_user_key,
    }


def send_local_template_email(pdf_path, recipient):
    # Envía por correo el primer PDF generado cuando el flujo local así lo requiere.
    requests = get_requests_module()
    model = decode_env_base64("model")
    auth_token = decode_env_base64("API_KEY")
    if not model or not auth_token:
        return False, "faltan credenciales de correo"

    with open(pdf_path, "rb") as pdf_file:
        pdf_content = pdf_file.read()

    if not pdf_content:
        return False, f"el PDF está vacío: {pdf_path}"

    pdf_name = os.path.basename(pdf_path)

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "model": model,
    }
    payload = {
        "to": [recipient],
        "cc": [],
        "subject": f"PDF Formal - Finiquito generado - {pdf_name}",
        "body": (
            "<html><body>"
            f"<p>Se generó el PDF <strong>{pdf_name}</strong>.</p>"
            "<p>Se adjunta el archivo PDF generado.</p>"
            "</body></html>"
        ),
        "useHtml": True,
        "bcc": [],
        "attachments": [
            {
                "fileName": pdf_name,
                "content": base64.b64encode(pdf_content).decode("utf-8"),
            }
        ],
    }

    response = requests.post(
        "https://api.cloud.varicent.com/api/v1/admin/tsapi/sendMail",
        headers=headers,
        json=payload,
        timeout=120
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        detail = ""
        try:
            detail = response.text.strip()
        except Exception:
            detail = ""
        if detail:
            raise RuntimeError(f"{exc}. Response: {detail}") from exc
        raise
    return True, "ok"
    return True, "ok"
def load_json_config(path_value):
    if path_value and os.path.exists(path_value):
        with open(path_value, "r", encoding="utf-8") as config_file:
            return json.load(config_file)
    return {"CONFIGURATION": {}}


def get_log_level(level_name):
    if not level_name:
        return logging.INFO
    return getattr(logging, str(level_name).upper(), logging.INFO)


def setup_file_logger(log_file, log_level):
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


class ElapsedTime:
    def __init__(self):
        self.start = time.time()

    def elapsed(self):
        return time.time() - self.start


def get_start_line(elapsed_time, script_name, version):
    return f"START {script_name} {version}"


def get_end_line(elapsed_time):
    return f"END elapsed={elapsed_time.elapsed():.2f}s"


def get_frame_line(message):
    return message


def exit_handler(logger, message, successful=False):
    if logger:
        if successful:
            logger.info(message)
        else:
            logger.error(message)
    else:
        print(message)

    if not successful:
        raise SystemExit(1)


def remove_files_x_days_old(folder, retention_days):
    # Limpieza preventiva de archivos viejos para evitar crecimiento indefinido de logs y salidas.
    if not os.path.isdir(folder):
        return

    max_age_seconds = float(retention_days) * 86400
    now = time.time()
    for file_name in os.listdir(folder):
        file_path = os.path.join(folder, file_name)
        if not os.path.isfile(file_path):
            continue
        if now - os.path.getmtime(file_path) > max_age_seconds:
            os.remove(file_path)


def resolve_input_csv_path(config_section):
    # PDFScript consume exclusivamente el CSV desacoplado generado para este flujo.
    # No debe depender de rutas configuradas como csvformal.csv u otras variantes.
    candidates = [
        os.path.join(dataDir, "CSVparaPDFFormal"),
        os.path.join(dataDir, "CSVparaPDFFormal.csv"),
        os.path.join(DATA_DIR, "CSVparaPDFFormal"),
        os.path.join(DATA_DIR, "CSVparaPDFFormal.csv"),
    ]

    deduped_candidates = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized_candidate = os.path.normpath(candidate)
        if normalized_candidate in seen:
            continue
        seen.add(normalized_candidate)
        deduped_candidates.append(normalized_candidate)

    for candidate in deduped_candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    return deduped_candidates[-1] if deduped_candidates else os.path.join(DATA_DIR, "CSVparaPDFFormal.csv")


def find_default_template_path():
    # En el flujo formal solo se aceptan templates PDF explícitamente identificados como formales.
    # Si no existe uno válido, el script debe fallar claro en vez de reutilizar el template normal.
    if not os.path.isdir(templateDir):
        return ""

    pdf_files = sorted(
        file_name for file_name in os.listdir(templateDir)
        if file_name.lower().endswith(".pdf")
    )
    if not pdf_files:
        return ""

    preferred_formal_names = [
        "template nuevo finiquito formal sin datos.pdf",
        "template nuevo finiquito formal con datos.pdf",
        "template finiquito formal sin datos.pdf",
        "template finiquito formal con datos.pdf",
        "template pdf finiquito formal sin datos.pdf",
        "pdf finiquito formal con datos.pdf",
        "template pdf finiquito sin datos.pdf",
        "pdf finiquito con datos.pdf",
    ]
    lowered_to_original = {file_name.lower(): file_name for file_name in pdf_files}
    for preferred_name in preferred_formal_names:
        matched = lowered_to_original.get(preferred_name)
        if matched:
            return os.path.join(templateDir, matched)

    return ""


def find_default_layout_path():
    # El layout formal por defecto es layout.json.
    # Se excluye deliberadamente el layout normal para evitar mezclar ambos formatos.
    preferred_layouts = [
        os.path.join(templateDir, "layout.json"),
        os.path.join(scriptDir, "layout.json"),
    ]
    for candidate in preferred_layouts:
        if candidate and os.path.exists(candidate):
            return candidate

    if not os.path.isdir(templateDir):
        return ""

    json_files = sorted(
        file_name for file_name in os.listdir(templateDir)
        if file_name.lower().endswith(".json")
        and file_name.lower() != "layout.finiquito.normal.json"
    )
    if not json_files:
        return ""

    return os.path.join(templateDir, json_files[0])



def find_default_rdl_path():
    # El RDL formal preferido es Finiquito_Formal 1.rdl.
    if not os.path.isdir(templateDir):
        return ""

    preferred_formal_rdl = os.path.join(templateDir, "Finiquito_Formal 1.rdl")
    if os.path.exists(preferred_formal_rdl):
        return preferred_formal_rdl

    rdl_files = sorted(
        file_name for file_name in os.listdir(templateDir)
        if file_name.lower().endswith(".rdl")
    )
    if not rdl_files:
        return ""

    return os.path.join(templateDir, rdl_files[0])


def mm_to_points(value):
    text = str(value or "").strip().lower()
    match = re.match(r"(-?\d+(?:\.\d+)?)(mm|cm|in|pt)?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "pt"
    if unit == "mm":
        return number * 72.0 / 25.4
    if unit == "cm":
        return number * 72.0 / 2.54
    if unit == "in":
        return number * 72.0
    return number


RDL_LEFT_MARGIN_PT = mm_to_points("2.5cm") or 70.87
RDL_VERTICAL_OFFSET_PT = 45.83
INTEGRATION_X_OFFSET_PT = 1.5
INTEGRATION_Y_OFFSET_PT = -6.0
INVENTORY_Y_OFFSET_PT = 18.0
INVENTORY_WRAPPED_ROW_EXTRA_OFFSET_PT = -(mm_to_points("3.325mm") or 9.42)
INVENTORY_TOTAL_Y_OFFSET_PT = 18.0
DISCOUNTS_Y_OFFSET_PT = 20.0
TOTALS_Y_OFFSET_PT = 34.0
DISCOUNTS_FIRST_COLUMN_EXTRA_Y_OFFSET_PT = -4.0

# Ajuste fino por fila de la integracion de comision.
# Positivo sube la fila; negativo la baja.
# Puedes editar usando las llaves internas o los alias amigables definidos abajo.
INTEGRATION_ROW_OFFSETS_PT = {
    "comisionfija": 16.0,
    "comisionporestructura": 16.0,
    "comisionvariable": 16.0,
    "incentivovtacat": 16.0,
    "subtotalcomison": 16.0,
    "ivasubtotalcomision": 16.0,
    "retenciondosterciosiva": 16.0,
    "retencionisr": 16.0,
    "impcedular": 16.0,
    "totalcomision": 16.0,
}

INTEGRATION_ROW_OFFSET_ALIASES = {
    "COMISIONFIJA": "comisionfija",
    "COMISIONESTRUCTURA": "comisionporestructura",
    "COMISIONPORESTRUCTURA": "comisionporestructura",
    "COMISIONES": "comisionvariable",
    "COMISIONVARIABLE": "comisionvariable",
    "INCENTIVOVTACAT": "incentivovtacat",
    "SUBTOTALCOMISON": "subtotalcomison",
    "IVACOMISION": "ivasubtotalcomision",
    "IVASUBTOTALCOMISION": "ivasubtotalcomision",
    "RETIVA": "retenciondosterciosiva",
    "RETENCIONDOSTERCIOSIVA": "retenciondosterciosiva",
    "RETISR125": "retencionisr",
    "RETENCIONISR": "retencionisr",
    "RETCEDULAR2": "impcedular",
    "IMPCEDULAR": "impcedular",
    "TOTALCOMISION": "totalcomision",
}

# Ajuste fino por fila. Valores positivos suben la fila; negativos la bajan.
INVENTORY_ROW_OFFSETS_PT = {
    "MERMAMCIACERO": 0.0,
    "MERMAMCIAEXENTA": 0.0,
    "MERMAMCIACONSIG": 0.0,
    "MERMAMCIAGRAVAD": 0.0,
    "MERMASUBTOTAL": 0.0,
    "MERMAIVA": 0.0,
    "MERMAMIMPESTATAL": 0.0,
    "MERMAIMPESTATAL": 0.0,
    "MERMAIEPS": 0.0,
    "TOTAL": 0.0,
}

DISCOUNT_ROW_OFFSETS_PT = {
    0: 0.0,
    1: 0.0,
    2: 0.0,
    3: 0.0,
    "TOTAL": 0.0
}

TOTALS_ROW_OFFSETS_PT = {
    "TOTAL_A_PAGAR": 0.0,
    "FLUJO_MENSUAL": 0.0,
}

HEADER_FIELD_OFFSETS_PT = {
    "MES": {"x": 1.8, "y": 1.8},
    "ULTIMO_CALCULO": {"x": 0.0, "y": 0.0},
    "FECHA_REPORTE": {"x": 0.0, "y": 0.0},
    "PLAZA": {"x": 8.0, "y": 8.0},
    "CRDISTRITO": {"x": 8.0, "y": 10.0},
    "TIENDA": {"x": 10.0, "y": 12.0},
    "NOMBRECOMISIONISTA": {"x": 10.0, "y": 10.0},
}


def get_integration_row_offset(field_key):
    normalized_key = str(field_key or "").strip()
    if not normalized_key:
        return 0.0
    canonical_key = INTEGRATION_ROW_OFFSET_ALIASES.get(normalized_key.upper(), normalized_key.lower())
    return INTEGRATION_ROW_OFFSETS_PT.get(canonical_key, 0.0)


def get_header_field_offset(field_key, axis):
    field_offsets = HEADER_FIELD_OFFSETS_PT.get(str(field_key or "").strip().upper(), {})
    try:
        return float(field_offsets.get(axis, 0.0))
    except (TypeError, ValueError):
        return 0.0


def parse_rdl_metadata(rdl_path):
    # Lee el RDL y construye un índice de textbox/expresiones para reutilizar sus posiciones en el overlay.
    if not rdl_path or not os.path.exists(rdl_path):
        return {}

    try:
        tree = ET.parse(rdl_path)
    except ET.ParseError:
        return {}

    root = tree.getroot()
    namespace = {"rdl": "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"}
    textboxes = {}
    field_expressions = {}
    field_expression_matches = {}

    for textbox in root.findall(".//rdl:Textbox", namespace):
        name = textbox.attrib.get("Name", "").strip()
        if not name:
            continue

        value_node = textbox.find(".//rdl:Value", namespace)
        font_size_node = textbox.find(".//rdl:FontSize", namespace)
        font_weight_node = textbox.find(".//rdl:FontWeight", namespace)
        format_node = textbox.find(".//rdl:Format", namespace)
        left_node = textbox.find("./rdl:Left", namespace)
        top_node = textbox.find("./rdl:Top", namespace)
        width_node = textbox.find("./rdl:Width", namespace)
        height_node = textbox.find("./rdl:Height", namespace)

        value_text = value_node.text.strip() if value_node is not None and value_node.text else ""
        textbox_info = {
            "value": value_text,
            "font_size": font_size_node.text.strip() if font_size_node is not None and font_size_node.text else "",
            "font_weight": font_weight_node.text.strip() if font_weight_node is not None and font_weight_node.text else "",
            "format": format_node.text.strip() if format_node is not None and format_node.text else "",
            "left_pt": mm_to_points(left_node.text) if left_node is not None and left_node.text else None,
            "top_pt": mm_to_points(top_node.text) if top_node is not None and top_node.text else None,
            "width_pt": mm_to_points(width_node.text) if width_node is not None and width_node.text else None,
            "height_pt": mm_to_points(height_node.text) if height_node is not None and height_node.text else None,
        }
        textboxes[name.upper()] = textbox_info

        field_match = re.fullmatch(r"=Fields!([A-Za-z0-9_]+)\.Value", value_text)
        if field_match:
            field_name = field_match.group(1).upper()
            field_expressions.setdefault(field_name, textbox_info)
            field_expression_matches.setdefault(field_name, []).append(textbox_info)

    return {
        "path": rdl_path,
        "textboxes": textboxes,
        "field_expressions": field_expressions,
        "field_expression_matches": field_expression_matches,
    }


def get_rdl_textbox(layout, name):
    return ((layout.get("rdl") or {}).get("textboxes") or {}).get(str(name or "").upper(), {})


def get_rdl_field_expression(layout, field_name):
    return ((layout.get("rdl") or {}).get("field_expressions") or {}).get(str(field_name or "").upper(), {})


def get_rdl_field_matches(layout, field_name):
    return ((layout.get("rdl") or {}).get("field_expression_matches") or {}).get(str(field_name or "").upper(), [])


def get_rdl_canvas_position(page_height, textbox_info, align="left"):
    if not textbox_info:
        return None

    left_pt = textbox_info.get("left_pt")
    top_pt = textbox_info.get("top_pt")
    width_pt = textbox_info.get("width_pt")
    if left_pt is None or top_pt is None:
        return None

    x = RDL_LEFT_MARGIN_PT + left_pt
    if align == "right" and width_pt is not None:
        x += width_pt

    y = page_height - top_pt - RDL_VERTICAL_OFFSET_PT
    return {
        "x": x,
        "y": y,
        "width": width_pt,
        "height": textbox_info.get("height_pt"),
    }


def get_rdl_field_canvas_position(layout, page_height, field_name, align="left"):
    return get_rdl_canvas_position(page_height, get_rdl_field_expression(layout, field_name), align=align)


def get_rdl_field_canvas_position_in_range(layout, page_height, field_name, min_top=None, max_top=None, align="left"):
    matches = get_rdl_field_matches(layout, field_name)
    filtered = []
    for match in matches:
        top_pt = match.get("top_pt")
        if top_pt is None:
            continue
        if min_top is not None and top_pt < min_top:
            continue
        if max_top is not None and top_pt > max_top:
            continue
        filtered.append(match)

    if not filtered:
        if min_top is not None or max_top is not None:
            return None
        return get_rdl_field_canvas_position(layout, page_height, field_name, align=align)

    filtered.sort(key=lambda item: item.get("top_pt") or 0)
    return get_rdl_canvas_position(page_height, filtered[0], align=align)


def canvas_y_from_mm(page_height, top_mm_value, base_offset_pt=0.0, row_offset_pt=0.0):
    top_pt = mm_to_points(top_mm_value)
    if top_pt is None:
        raise ValueError(f"No se pudo convertir a puntos el valor top_mm={top_mm_value!r}")
    return page_height - top_pt - RDL_VERTICAL_OFFSET_PT + base_offset_pt + row_offset_pt


def evaluate_rdl_expression(expression, row):
    if not expression:
        return None

    matches = re.findall(r"([+-]?)\s*Fields!([A-Za-z0-9_]+)\.Value", expression, flags=re.IGNORECASE)
    if not matches:
        return None

    total = 0.0
    for sign, field_name in matches:
        value = safe_float(get_row_value(row, field_name), 0.0)
        total += -value if sign == "-" else value
    return total




def get_numeric_field_or_rdl(layout, row, field_name):
    raw_value = get_row_value(row, field_name)
    if raw_value not in (None, ""):
        return safe_float(raw_value, 0.0)

    field_info = get_rdl_field_expression(layout, field_name)
    expression = (field_info.get("value") or "").strip() if isinstance(field_info, dict) else ""
    numeric_value = evaluate_rdl_expression(expression, row)
    if numeric_value is not None:
        return numeric_value
    return 0.0

def resolve_rdl_textbox_render_value(textbox_name, textbox_info, row):
    # Interpreta el contenido del textbox del RDL y devuelve el valor listo para renderizar.
    # Soporta campos directos y expresiones numéricas simples usadas por las tablas del reporte.
    expression = (textbox_info.get("value") or "").strip()
    if not expression.startswith("="):
        return None, None

    direct_field_match = re.fullmatch(r"=Fields!([A-Za-z0-9_]+)\.Value", expression, flags=re.IGNORECASE)
    if direct_field_match:
        field_name = direct_field_match.group(1)
        raw_value = get_row_value(row, field_name)
        text_fields = {"MES", "PLAZA", "CRDISTRITO", "TIENDA", "NOMBRECOMISIONISTA", "RFC"}
        date_fields = {"FECHAINICIAL", "FECHAFINAL", "FECHAINICIO", "FECHAFIN", "FechaInicio", "FechaFin"}
        if field_name.upper() in text_fields:
            return raw_value, "text"
        if field_name.upper() in {item.upper() for item in date_fields}:
            return format_date_value(raw_value), "text"
        if raw_value in (None, ""):
            raw_value = 0.0
        return raw_value, "currency"

    numeric_value = evaluate_rdl_expression(expression, row)
    if numeric_value is not None:
        return numeric_value, "currency"

    return None, None


def draw_rdl_textboxes_in_range(pdf_canvas, page_height, row, layout, min_top_mm, max_top_mm):
    textboxes = ((layout.get("rdl") or {}).get("textboxes") or {})
    min_top_pt = mm_to_points(min_top_mm)
    max_top_pt = mm_to_points(max_top_mm)

    ordered_items = sorted(
        textboxes.items(),
        key=lambda item: (
            item[1].get("top_pt") if item[1].get("top_pt") is not None else float("inf"),
            item[1].get("left_pt") if item[1].get("left_pt") is not None else float("inf"),
        ),
    )

    for textbox_name, textbox_info in ordered_items:
        top_pt = textbox_info.get("top_pt")
        if top_pt is None:
            continue
        if min_top_pt is not None and top_pt < min_top_pt:
            continue
        if max_top_pt is not None and top_pt > max_top_pt:
            continue

        render_value, render_mode = resolve_rdl_textbox_render_value(textbox_name, textbox_info, row)
        if render_mode is None or render_value in (None, ""):
            continue

        font_name = "Helvetica-Bold" if str(textbox_info.get("font_weight") or "").strip().lower() == "bold" else "Helvetica"
        font_size = parse_reportlab_font_size(textbox_info.get("font_size"), 8)
        max_width = textbox_info.get("width_pt") or 90

        if render_mode == "currency":
            position = get_rdl_canvas_position(page_height, textbox_info, align="right")
            if not position:
                continue
            draw_right_currency(
                pdf_canvas,
                position["x"],
                position["y"],
                render_value,
                font_name=font_name,
                font_size=font_size,
                max_width=max_width
            )
            continue

        position = get_rdl_canvas_position(page_height, textbox_info, align="left")
        if not position:
            continue
        draw_text(
            pdf_canvas,
            position["x"],
            position["y"],
            render_value,
            font_name=font_name,
            font_size=font_size,
            max_width=max_width,
            min_size=6,
            trim_overflow=True
        )


def draw_rdl_named_textboxes(pdf_canvas, page_height, row, layout, textbox_names):
    textboxes = ((layout.get("rdl") or {}).get("textboxes") or {})

    for textbox_name in textbox_names:
        textbox_info = textboxes.get(str(textbox_name or "").upper())
        if not textbox_info:
            continue

        render_value, render_mode = resolve_rdl_textbox_render_value(textbox_name, textbox_info, row)
        if render_mode is None or render_value in (None, ""):
            continue

        font_name = "Helvetica-Bold" if str(textbox_info.get("font_weight") or "").strip().lower() == "bold" else "Helvetica"
        font_size = parse_reportlab_font_size(textbox_info.get("font_size"), 8)
        max_width = textbox_info.get("width_pt") or 90

        if render_mode == "currency":
            position = get_rdl_canvas_position(page_height, textbox_info, align="right")
            if not position:
                continue
            draw_right_currency(
                pdf_canvas,
                position["x"],
                position["y"],
                render_value,
                font_name=font_name,
                font_size=font_size,
                max_width=max_width
            )
            continue

        position = get_rdl_canvas_position(page_height, textbox_info, align="left")
        if not position:
            continue
        draw_text(
            pdf_canvas,
            position["x"],
            position["y"],
            render_value,
            font_name=font_name,
            font_size=font_size,
            max_width=max_width,
            min_size=6,
            trim_overflow=True
        )


def load_csv_rows(csv_path):
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise batchPDF(f"CSV sin encabezados o vacio: {csv_path}")

        rows = [row for row in reader]

    if not rows:
        raise batchPDF(f"CSV sin registros para generar PDFs: {csv_path}")

    return rows


def load_template_layout(script_config):
    # Combina tres fuentes:
    # 1. DEFAULT_TEMPLATE_LAYOUT
    # 2. metadata del RDL formal
    # 3. layout.json del proceso
    # El objetivo es que el JSON no sustituya el RDL, sino que lo complemente.
    config_section = get_configuration_section(script_config)
    layout = dict(DEFAULT_TEMPLATE_LAYOUT)
    rdl_default_path = find_default_rdl_path()
    rdl_config_value = get_config_value(config_section, "LOCAL_TEMPLATE_RDL_PATH", None)
    rdl_path = resolve_existing_path(
        rdl_config_value,
        [runtimeScriptsRoot, scriptDir, runtimeProjectRoot, projectRoot]
    )
    if not rdl_path:
        rdl_path = resolve_existing_path(
            rdl_default_path,
            [projectRoot, runtimeProjectRoot, scriptDir, runtimeScriptsRoot]
        )
    layout["rdl"] = parse_rdl_metadata(rdl_path)
    layout_default_path = find_default_layout_path()
    layout_config_value = get_config_value(config_section, "LOCAL_TEMPLATE_LAYOUT_PATH", None)
    layout_path = resolve_existing_path(
        layout_config_value,
        [runtimeScriptsRoot, scriptDir, runtimeProjectRoot, projectRoot]
    )
    if not layout_path:
        layout_path = resolve_existing_path(
            layout_default_path,
            [projectRoot, runtimeProjectRoot, scriptDir, runtimeScriptsRoot]
        )
    if layout_path and os.path.exists(layout_path):
        try:
            with open(layout_path, "r", encoding="utf-8") as layout_file:
                file_layout = json.load(layout_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise batchPDF(f"No se pudo leer LOCAL_TEMPLATE_LAYOUT_PATH: {exc}") from exc

        if not isinstance(file_layout, dict):
            raise batchPDF("El layout del template debe ser un objeto JSON.")

        layout.update(file_layout)

    layout["page_index"] = parse_int(layout.get("page_index"), 0)
    layout["font_size"] = parse_float(layout.get("font_size"), 10)
    layout["font_name"] = str(layout.get("font_name") or "Helvetica")

    return layout




def get_layout_section(layout, section_name):
    section = layout.get(section_name) if isinstance(layout, dict) else None
    return section if isinstance(section, dict) else {}


def get_layout_field(layout_section, field_name):
    fields = layout_section.get("fields") if isinstance(layout_section, dict) else None
    if isinstance(fields, dict):
        field = fields.get(field_name)
        if isinstance(field, dict):
            return field
    return {}


def get_layout_reference_value(layout, key, default):
    document = layout.get("document") if isinstance(layout, dict) else None
    reference = document.get("reference") if isinstance(document, dict) else None
    try:
        return float(reference.get(key, default))
    except (TypeError, ValueError, AttributeError):
        return float(default)


def layout_left_margin_pt(layout):
    return get_layout_reference_value(layout, "left_margin_pt", RDL_LEFT_MARGIN_PT)


def layout_vertical_offset_pt(layout):
    return get_layout_reference_value(layout, "vertical_offset_pt", RDL_VERTICAL_OFFSET_PT)


def layout_right_x_from_mm(layout, right_x_mm, default_right_x_pt=None):
    if right_x_mm in (None, ""):
        return default_right_x_pt
    return layout_left_margin_pt(layout) + mm_to_points(f"{right_x_mm}mm")


def canvas_y_from_layout_mm(page_height, layout, top_mm, extra_offset_pt=0.0):
    return page_height - mm_to_points(f"{top_mm}mm") - layout_vertical_offset_pt(layout) + float(extra_offset_pt or 0.0)

def draw_finiquito_overlay(page_width, page_height, row, layout):
    # Version conservadora: mantiene la logica original funcional y solo toma
    # posiciones del layout cuando existen. Si falta algo en el JSON, usa las
    # coordenadas originales para no romper la generacion.
    packet = io.BytesIO()
    pdf_canvas = canvas.Canvas(packet, pagesize=(page_width, page_height), pageCompression=1)
    rdl_totalcomision = get_rdl_textbox(layout, "TOTALCOMISION")
    rdl_comisionfija = get_rdl_textbox(layout, "COMISIONFIJA")
    amount_font_size = parse_float(rdl_comisionfija.get("font_size", "6"), 6)
    total_font_size = parse_float(rdl_totalcomision.get("font_size", "6"), 6)

    header_layout = get_layout_section(layout, "header")
    integration_layout = get_layout_section(layout, "integration")
    inventory_layout = get_layout_section(layout, "inventory")
    discounts_layout = get_layout_section(layout, "discounts")
    totals_layout = get_layout_section(layout, "totals")
    signature_layout = get_layout_section(layout, "signature")

    current_run = datetime.now()

    # Clear areas desactivado.
    # Los rectangulos blancos estaban tapando el template y generando bloques visuales.

    def header_field_position(field_name, fallback_x, fallback_y, fallback_width):
        field_cfg = get_layout_field(header_layout, field_name)
        if field_cfg:
            return {
                "x": safe_float(field_cfg.get("x_pt"), fallback_x),
                "y": safe_float(field_cfg.get("y_pt"), fallback_y),
                "width": safe_float(field_cfg.get("max_width_pt"), fallback_width),
            }
        return {"x": fallback_x, "y": fallback_y, "width": fallback_width}
    """
    mes_pos = header_field_position("MES", 1,790, 150)
    plaza_pos = header_field_position("PLAZA", 60, 749.5, 210)
    distrito_pos = header_field_position("CRDISTRITO", 55, 739.5, 230)
    tienda_pos = header_field_position("TIENDA", 78, 727, 250)
    comisionista_pos = header_field_position("NOMBRECOMISIONISTA", 240, 739.8, 270)
    ultimo_calculo_pos = header_field_position("ULTIMO_CALCULO", 404.95, 786.01, 145)
    fecha_reporte_pos = header_field_position("FECHA_REPORTE", 404.95, 778.31, 145)
    """

    tiendaDetalle= re.match(r"^(.*?)\((.*?)\)$",get_row_value(row, "tienda"))
    
    mes_anio = f"{get_spanish_month_name_from_date( get_row_value(row, "FECHAINICIAL"))} del año {obtener_anio_periodo(get_row_value(row, "mes"))}"
        # 123.41 y 763.05 (y-33(menos es izquierda), x+10.75(mas es subir))
    # Arreglado
    mes_pos = header_field_position("MES", 74, 762, 110)

    # Arreglado
    plaza_pos = header_field_position("PLAZA", 74, 749.5, 210)
    distrito_pos = header_field_position("CRDISTRITO", 74, 740, 230)
    tienda_pos = header_field_position("TIENDA", 74,  727.7, 250)
    nombreTienda_pos = header_field_position("NOMBRETIENDA", 74,  717.7, 250)

    # Arreglado
    comisionista_pos = header_field_position("NOMBRECOMISIONISTA", 238, 740.7, 270)
    
    ultimo_calculo_pos = header_field_position("ULTIMO_CALCULO", 404.95, 785.90, 145)

    fecha_reporte_pos = header_field_position("FECHA_REPORTE", 404.95, 778.20, 145)

    # Arreglado
    rfc_pos = header_field_position("RFC",210, 727.7, 270)

    fecha_inicio_pos = header_field_position("FECHAINICIAL", 243, 765.8, 270)
    fecha_fin_pos = header_field_position("FECHAFINAL", 290, 765.8, 270)



    header_font_size = parse_float(header_layout.get("font_size"), 6.2) if header_layout else 6.2

    draw_text(pdf_canvas, mes_pos["x"], mes_pos["y"], get_row_value(row, "mes"), font_size=header_font_size, max_width=mes_pos["width"], trim_overflow=True, shrink_to_fit=False)
   #draw_text(pdf_canvas, ultimo_calculo_pos["x"], ultimo_calculo_pos["y"], format_spanish_datetime(current_run), font_size=header_font_size, max_width=ultimo_calculo_pos["width"], trim_overflow=True, shrink_to_fit=False)
    #draw_text(pdf_canvas, fecha_reporte_pos["x"], fecha_reporte_pos["y"], format_spanish_date(current_run), font_size=header_font_size, max_width=fecha_reporte_pos["width"], trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, plaza_pos["x"], plaza_pos["y"], get_row_value(row, "plaza"), font_size=header_font_size, max_width=plaza_pos["width"], trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, distrito_pos["x"], distrito_pos["y"], get_row_value(row, "CRDISTRITO"), font_size=header_font_size, max_width=distrito_pos["width"], trim_overflow=True, shrink_to_fit=False)
    #draw_text(pdf_canvas, tienda_pos["x"], tienda_pos["y"], normalize_single_line_text(get_row_value(row, "tienda")), font_size=header_font_size, max_width=tienda_pos["width"], min_size=7, trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, tienda_pos["x"], tienda_pos["y"], tiendaDetalle.group(1), font_size=header_font_size, max_width=tienda_pos["width"], min_size=7, trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, nombreTienda_pos["x"], nombreTienda_pos["y"], tiendaDetalle.group(2), font_size=header_font_size, max_width=nombreTienda_pos["width"], min_size=7, trim_overflow=True, shrink_to_fit=False)

    #draw_text(pdf_canvas, tienda_pos["x"], tienda_pos["y"], normalize_single_line_text(get_row_value(row, "tienda")), font_size=header_font_size, max_width=tienda_pos["width"], min_size=7, trim_overflow=True, shrink_to_fit=False)
    #draw_text(pdf_canvas, comisionista_pos["x"], comisionista_pos["y"], build_header_comisionista_text(row), font_size=header_font_size, max_width=comisionista_pos["width"], trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, comisionista_pos["x"], comisionista_pos["y"], get_row_value(row,"NOMBRECOMISIONISTA"), font_size=header_font_size, max_width=comisionista_pos["width"], trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, rfc_pos["x"], rfc_pos["y"], get_row_value(row, "RFC"), font_size=header_font_size, max_width=rfc_pos["width"], trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, fecha_inicio_pos["x"], fecha_inicio_pos["y"],  format_date_value(get_first_row_value(row, ["FechaInicio", "FECHAINICIO", "FECHAINICIAL"])), font_size=header_font_size, max_width=fecha_inicio_pos["width"], trim_overflow=True, shrink_to_fit=False)
    draw_text(pdf_canvas, fecha_fin_pos["x"], fecha_fin_pos["y"], format_date_value(get_first_row_value(row, ["FechaFin", "FECHAFIN", "FECHAFINAL"])), font_size=header_font_size, max_width=fecha_fin_pos["width"], trim_overflow=True, shrink_to_fit=False)



    integration_right_x = safe_float(integration_layout.get("right_x_pt"), 557.03) if integration_layout else 557.03
    integration_default_max_width = safe_float(integration_layout.get("default_max_width_pt"), 80) if integration_layout else 80
    integration_total_max_width = safe_float(integration_layout.get("total_max_width_pt"), 85) if integration_layout else 85
    integration_default_font_size = parse_float(integration_layout.get("default_font_size"), amount_font_size) if integration_layout else amount_font_size
    integration_total_font_size = parse_float(integration_layout.get("total_font_size"), total_font_size) if integration_layout else total_font_size
    integration_default_font_name = str(integration_layout.get("default_font_name") or "Helvetica") if integration_layout else "Helvetica"
    integration_total_font_name = str(integration_layout.get("total_font_name") or "Helvetica") if integration_layout else "Helvetica"
    integration_row_y_pt = {
        "comisionfija":             663.56,
        "comisionporestructura":    653.06,
        "comisionvariable":         640.50,
        "incentivovtacat":          629.00,
        "subtotalcomison":          617.00,
        "ivasubtotalcomision":      605.00,
        "retenciondosterciosiva":   593.00,
        "retencionisr":             582.00,
        "impcedular":               571.00,
        "totalcomision":            558.00,
    }
    integration_layout_rows = integration_layout.get("rows") if integration_layout else {}
    for source_key, fallback_y_pt in integration_row_y_pt.items():
        if not row_has_field(row, source_key):
            continue
        row_cfg = integration_layout_rows.get(source_key, {}) if isinstance(integration_layout_rows, dict) else {}
        row_y = safe_float(row_cfg.get("y_pt"), fallback_y_pt)
        font_size = integration_total_font_size if source_key == "totalcomision" else integration_default_font_size
        max_width = integration_total_max_width if source_key == "totalcomision" else integration_default_max_width
        draw_right_currency(
            pdf_canvas,
            integration_right_x,
            row_y,
            get_row_value(row, source_key),
            font_name=integration_total_font_name if source_key == "totalcomision" else integration_default_font_name,
            font_size=font_size,
            max_width=max_width
        )

    inventory_suffixes = ["ANTERIOR", "MENSUAL", "ACUMULAD", "FACTURAD", "NOFAC", "SALDO"]
    inventory_row_prefixes = ["MERMAMCIACERO", "MERMAMCIAEXENTA", "MERMAMCIACONSIG", "MERMAMCIAGRAVAD", "MERMASUBTOTAL", "MERMAIVA", "MERMAIMPESTATAL", "MERMAIEPS"]
    inventory_row_y_pt = {
        "MERMAMCIACERO":    499.00,
        "MERMAMCIAEXENTA":  488.00,
        "MERMAMCIACONSIG":  476.00,
        "MERMAMCIAGRAVAD":  464.00,
        "MERMASUBTOTAL":    452.00,
        "MERMAIVA":         440.00,
        "MERMAIMPESTATAL":  429.00,
        "MERMAIEPS":        417.00,
    }
    inventory_field_suffix_lookup = {
        "ANTERIOR": "ANTERIOR",
        "MENSUAL": "MENSUAL",
        "ACUMULAD": "ACUMULAD",
        "FACTURAD": "FACTURAD",
        "NOFAC": "NOFAC",
        "SALDO": "SALDO",
    }
    inventory_layout_columns = inventory_layout.get("columns") if inventory_layout else {}
    inventory_column_right_x = {
        "ANTERIOR": safe_float(((inventory_layout_columns.get("ANTERIOR") or {}).get("right_x_pt") if isinstance(inventory_layout_columns, dict) else None), 256.00),
        "MENSUAL": safe_float(((inventory_layout_columns.get("MENSUAL") or {}).get("right_x_pt") if isinstance(inventory_layout_columns, dict) else None), 316.00),
        "ACUMULAD": safe_float(((inventory_layout_columns.get("ACUMULAD") or {}).get("right_x_pt") if isinstance(inventory_layout_columns, dict) else None), 376.00),
        "FACTURAD": safe_float(((inventory_layout_columns.get("FACTURAD") or {}).get("right_x_pt") if isinstance(inventory_layout_columns, dict) else None), 437.00),
        "NOFAC": safe_float(((inventory_layout_columns.get("NOFAC") or {}).get("right_x_pt") if isinstance(inventory_layout_columns, dict) else None), 497.00),
        "SALDO": safe_float(((inventory_layout_columns.get("SALDO") or {}).get("right_x_pt") if isinstance(inventory_layout_columns, dict) else None), 557.03),
    }
    inventory_rows_layout = inventory_layout.get("rows") if inventory_layout else {}
    inventory_font_size = parse_float(inventory_layout.get("font_size"), BODY_SMALL_FONT_SIZE) if inventory_layout else BODY_SMALL_FONT_SIZE
    inventory_total_font_size = parse_float(inventory_layout.get("total_font_size"), 5.8) if inventory_layout else 5.8
    inventory_max_width = safe_float(inventory_layout.get("max_width_pt"), 54) if inventory_layout else 54
    inventory_source_prefix_override = {
        "MERMAIVA": "MERMAIMPESTATAL",
        "MERMAIMPESTATAL": "MERMAIEPS",
        "MERMAIEPS": "MERMAIVA",
    }
    inventory_totals = {suffix: 0.0 for suffix in inventory_suffixes}
    for prefix in inventory_row_prefixes:
        row_cfg = inventory_rows_layout.get(prefix, {}) if isinstance(inventory_rows_layout, dict) else {}
        row_y = safe_float(row_cfg.get("y_pt"), inventory_row_y_pt[prefix]) if row_cfg else inventory_row_y_pt[prefix]
        source_prefix = inventory_source_prefix_override.get(prefix, prefix)
        for suffix in inventory_suffixes:
            field_suffix = inventory_field_suffix_lookup[suffix]
            field_name = f"{source_prefix}_{field_suffix}"
            if source_prefix == "MERMAIEPS" and suffix == "ACUMULAD":
                field_name = "MERMAIEPS_ACUMULADA"
            value = get_numeric_field_or_rdl(layout, row, field_name)
            inventory_totals[suffix] += value
            draw_right_currency(pdf_canvas, inventory_column_right_x[suffix], row_y, value, font_name="Helvetica", font_size=inventory_font_size, max_width=inventory_max_width)

    inventory_total_field_lookup = {
        "ANTERIOR": "MERMATOTAL_ANTERIOR",
        "MENSUAL": "MERMATOTAL_MENSUAL",
        "ACUMULAD": "MERMATOTAL_ACUMULAD",
        "FACTURAD": "MERMATOTAL_FACTURAD",
        "NOFAC": "MERMATOTAL_NOFAC",
        "SALDO": "MERMATOTAL_SALDO",
    }
    total_row_cfg = inventory_layout.get("total_row") if inventory_layout else {}
    if isinstance(total_row_cfg, dict) and total_row_cfg:
        inventory_total_y = safe_float(total_row_cfg.get("y_pt"), 403.00)
        inventory_total_white = bool(total_row_cfg.get("white_text", True))
    else:
        inventory_total_y = 403.00
        inventory_total_white = True
    for suffix in inventory_suffixes:
        total_field_name = inventory_total_field_lookup.get(suffix)
        total_value = get_numeric_field_or_rdl(layout, row, total_field_name) if total_field_name else 0.0
        if total_value == 0.0 and inventory_totals[suffix] != 0.0:
            total_value = inventory_totals[suffix]
        draw_right_currency(pdf_canvas, inventory_column_right_x[suffix], inventory_total_y, total_value, font_name="Helvetica-Bold", font_size=inventory_total_font_size, max_width=inventory_max_width, text_rgb=(1,1,1) if inventory_total_white else None)

    discounts_columns_layout = discounts_layout.get("columns") if discounts_layout else {}
    discounts_column_right_x = {
        "ANTERIOR": safe_float(((discounts_columns_layout.get("ANTERIOR") or {}).get("right_x_pt") if isinstance(discounts_columns_layout, dict) else None), 314.95),
        "MENSUAL": safe_float(((discounts_columns_layout.get("MENSUAL") or {}).get("right_x_pt") if isinstance(discounts_columns_layout, dict) else None), 375.47),
        "ACUM": safe_float(((discounts_columns_layout.get("ACUM") or {}).get("right_x_pt") if isinstance(discounts_columns_layout, dict) else None), 435.99),
        "RECUP": safe_float(((discounts_columns_layout.get("RECUP") or {}).get("right_x_pt") if isinstance(discounts_columns_layout, dict) else None), 496.51),
        "SALDO": safe_float(((discounts_columns_layout.get("SALDO") or {}).get("right_x_pt") if isinstance(discounts_columns_layout, dict) else None), 557.03),
    }
    discounts_rows = [
        ("ANTICIPO_COMISION_FIJA_MES_CORRIENTE", {
            "ANTERIOR": get_numeric_field_or_rdl(layout, row, "ANTICIPOLIDERCO_ANTERIOR"),
            "MENSUAL": get_numeric_field_or_rdl(layout, row, "ANTILIDERESCOM_MENSUAL"),
            "ACUM": get_numeric_field_or_rdl(layout, row, "ANTILIDERESCOM_ACUM"),
            "RECUP": get_numeric_field_or_rdl(layout, row, "ANTILIDERESCOM_RECUP"),
            "SALDO": get_numeric_field_or_rdl(layout, row, "ANTILIDERESCOM_SALDO"),
        }),
        ("ANTICIPO_COMISION_PAGO_A_TERCEROS", {
            "ANTERIOR": sum_row_fields(row, ["ANTICIPOCONT_ANTERIOR", "ANTICIPOIMPRFAC_ANTERIOR", "ANTICIPOPAGOSAL_ANTERIOR", "APORTCAJAAHORRO_ANTERIOR", "DEV_AGUINALDO_ANTERIOR", "DEVRETDIFIVA_ANTERIOR", "PRESTAMOCAJAAHO_ANTERIOR", "RETENUNTERCIO_ANTERIOR", "RETENIMPUESTOS_ANTERIOR", "RETENIMPNOMEMP_ANTERIOR", "ANTICIPOPAMULTA_ANTERIOR", "RESERAGUINALRED_ANTERIOR"]),
            "MENSUAL": sum_row_fields(row, ["ANTIDESPCONT_MENSUAL", "ANTIIMPRFACT_MENSUAL", "DEVRETDIFIVA_MENSUAL", "RETENUNTERCIOIVA_MENSUAL", "RETENIMPUESTOS_MENSUAL", "RETENIMPNOMEMP_MENSUAL", "ANTIPAGOSALARIOS_MENSUAL", "APORTCAJAAHORRO_MENSUAL", "PRESTCAJAAHORRO_MENSUAL", "RESERAGUINREDPAT_MENSUAL", "DEV_AGUINALDO_MENSUAL", "ANTIPAGOMULTAS_SALDO"]),
            "ACUM": sum_row_fields(row, ["ANTIDESPCONT_ACUM", "ANTIIMPRFACT_ACUM", "ANTIPAGOMULTAS_ACUM", "ANTIPAGOSALARIOS_ACUM", "APORTCAJAAHORROS_ACUM", "DEVRETENDIFIVA_ACUM", "RETENCIONUNTERCIO_ACUM", "RETENCIONIMP_ACUM", "RETENCIONIMPNOMEMP_ACUM", "RESERAGUINRED_ACUM", "DEV_AGUINALDO_ACUMULADA", "PRESTAMOCAJAAHORRO_ACUM"]),
            "RECUP": sum_row_fields(row, ["ANTIDESPCONT_RECUP", "ANTIIMPRFACT_RECUP", "DEVRETENDIFIVA_RECUP", "RETENUNTERCIOIVA_RECUP", "RETENIMPUESTOS_RECUP", "RETENIMPNOMEMP_RECUP", "APORTCAJAAHORROS_RECUP", "PRESTAMOCAJAAHORRO_RECUP", "RESERAGUINRED_RECUP", "ANTIPAGOSALARIOS_RECUP", "DEV_AGUINALDO_RECUP", "ANTIPAGOMULTAS_RECUP"]),
            "SALDO": sum_row_fields(row, ["ANTIDESPCONTABLE_SALDO", "ANTIIMPFACT_SALDO", "ANTIPAGOMULTAS_SALDO", "ANTIPAGOSALARIOS_SALDO", "APORTCAJAAHORRO_SALDO", "DEVRETENDIFIVA_SALDO", "PRESTOTALCAJAHORRO_SALDO", "RESERAGUINRED_SALDO", "RETENUNTERCIOIVA_SALDO", "RETENIMPNOMEMP_SALDO", "RETENIMPUESTOS_SALDO", "DEV_AGUINALDO_SALDO"]),
        }),
        ("DIFERENCIA_DE_DEPOSITOS", {
            "ANTERIOR": get_numeric_field_or_rdl(layout, row, "DIFERENCIADEPOS_ANTERIOR"),
            "MENSUAL": get_numeric_field_or_rdl(layout, row, "DIFERENCIADEPOS_MENSUAL"),
            "ACUM": get_numeric_field_or_rdl(layout, row, "DIFERENCIADEPO_ACUM"),
            "RECUP": get_numeric_field_or_rdl(layout, row, "DIFERENCIADEPO_RECUP"),
            "SALDO": get_numeric_field_or_rdl(layout, row, "DIFDESPOSITOS_SALDO"),
        }),
        ("OTROS_ANTICIPOS", {
            "ANTERIOR": sum_row_fields(row, ["ANTICIPOSPECIAL_ANTERIOR", "ANTICIPOPAGOTEL_ANTERIOR", "ANTICIPOSOTROS_ANTERIOR", "DESCUENTOSEGVOL_ANTERIOR", "DESCUENTOFASTFO_ANTERIOR" ,"ANTICIPESPTOTAL_ANTERIOR"]),
            "MENSUAL": sum_row_fields(row, ["ANTIPAGOTEL_MENSUAL", "ANTIESPECIAL_MENSUAL", "DESCFASTFOOD_MENSUAL", "DESCSEGVOLUN_MENSUAL", "ANTIOTROS_MENSUAL" ,"PAGOTOTANTIESPE_MENSUAL"]),
            "ACUM":  sum_row_fields(row, ["ANTIPAGOTEL_ACUM", "ANTIESPECIALES_ACUM", "DESCFASTFOOD_ACUM", "DESCSEGVOLUN_ACUM", "ANTICIPOSOTROS_ACUM" ,"PAGOTOTANTIESPE_ACUM"]),
            "RECUP": sum_row_fields(row, ["ANTIPAGOTEL_RECUP", "ANTIESPECIALES_RECUP", "DESCUENTOFASTFOOD_RECUP", "DESCSEGVOLUN_RECUP", "ANTICIPOSOTROS_RECUP" ,"PAGOTOTANTIESPE_RECUP"]),
            "SALDO": sum_row_fields(row, ["PAGOTOTANTIESPECIA_SALDO", "ANTIESPECIALES_SALDO", "DESCFASTFOOD_SALDO", "ANTICIPOSOTROS_SALDO", "DESCSEGDIFIVA_SALDO" ,"ANTIPAGOTELEFONO_SALDO"]),
        }),
    ]

    totalDiscounts_rows = [
        ("TOTAL_ANTICIPOS", {
            "ANTERIOR": sum_row_fields(row, ["TOTALANTICIPOS_ANTERIOR", "RESERVACARED_ANTERIOR", "ANTICIPOCAPACIT_ANTERIOR", "RESERCRECIPATRI_ANTERIOR", "DEV_AGUINALDO_ANTERIOR" ]),
            "MENSUAL": sum_row_fields(row, ["TOTALANTICIPOS_MENSUAL", "ANTICAPACITACION_MENSUAL", "RESERVACARED_MENSUAL", "RESERCRECIPATRI_MENSUAL", "DEV_AGUINALDO_MENSUAL" ]),
            "ACUM":  sum_row_fields(row, ["TOTALANTICIPOS_ACUM", "ANTICAPACITACION_ACUM", "RESERCRECIPAT_ACUM", "RESERVACARED_ACUM", "DEV_AGUINALDO_ACUMULADA" ]),
            "RECUP": sum_row_fields(row, ["TOTALANTICIPOS_RECUP", "ANTICAPACITACION_RECUP", "RESERCRECIPAT_RECUP", "RESERVACARED_RECUP", "DEV_AGUINALDO_RECUP" ]),
            "SALDO": sum_row_fields(row, ["TOTALANTICIPOS_SALDO", "ANTICAPACITACION_SALDO", "RESERCRECIPAT_SALDO", "RESERVACARED_SALDO", "DEV_AGUINALDO_SALDO" ]),
        }),]

    discounts_rows_layout = discounts_layout.get("rows") if discounts_layout else {}
    discounts_font_size = parse_float(discounts_layout.get("font_size"), 5.8) if discounts_layout else 5.8
    discounts_total_font_size = parse_float(discounts_layout.get("total_font_size"), 5.8) if discounts_layout else 5.8
    discounts_max_width = safe_float(discounts_layout.get("max_width_pt"), 54) if discounts_layout else 54
    fallback_discount_y = {0: 473.61, 1: 465.13, 2: 456.40, 3: 447.66}
    #discounts_totals = {suffix: 0.0 for suffix in ("ANTERIOR", "MENSUAL", "ACUM", "RECUP", "SALDO")}
    for row_index, (row_key, values_by_suffix) in enumerate(discounts_rows):
        row_cfg = discounts_rows_layout.get(row_key, {}) if isinstance(discounts_rows_layout, dict) else {}
        row_y = safe_float(row_cfg.get("y_pt"), fallback_discount_y[row_index]) if row_cfg else fallback_discount_y[row_index]
        for suffix, value in values_by_suffix.items():
            #discounts_totals[suffix] += value
            draw_right_currency(pdf_canvas, discounts_column_right_x[suffix], row_y, value, font_name="Helvetica", font_size=discounts_font_size, max_width=discounts_max_width)
    discounts_total_row_cfg = discounts_layout.get("total_row") if discounts_layout else {}
    if isinstance(discounts_total_row_cfg, dict) and discounts_total_row_cfg:
        discounts_total_y = safe_float(discounts_total_row_cfg.get("y_pt"), 311.00)
        discounts_total_white = bool(discounts_total_row_cfg.get("white_text", True))
    else:
        discounts_total_y = 311.00
        discounts_total_white = True
    for row_index, (row_key, values_by_suffix) in enumerate(totalDiscounts_rows):
        row_cfg = discounts_rows_layout.get(row_key, {}) if isinstance(discounts_rows_layout, dict) else {}
        row_y = safe_float(row_cfg.get("y_pt"), fallback_discount_y[row_index]) if row_cfg else fallback_discount_y[row_index]
        for suffix, value in values_by_suffix.items():
            draw_right_currency(pdf_canvas, discounts_column_right_x[suffix], discounts_total_y, value, font_name="Helvetica-Bold", font_size=discounts_total_font_size, max_width=discounts_max_width, text_rgb=(1,1,1) if discounts_total_white else None)
    
    
    
    #for suffix, value in discounts_totals.items():
        #draw_right_currency(pdf_canvas, discounts_column_right_x[suffix], discounts_total_y, value, font_name="Helvetica-Bold", font_size=discounts_total_font_size, max_width=discounts_max_width, text_rgb=(1,1,1) if discounts_total_white else None)

    totals_right_x = safe_float(totals_layout.get("right_x_pt"), 557.03) if totals_layout else 557.03
    totals_max_width = safe_float(totals_layout.get("max_width_pt"), mm_to_points("24.00000mm")) if totals_layout else mm_to_points("24.00000mm")
    totals_font_size = parse_float(totals_layout.get("font_size"), total_font_size) if totals_layout else total_font_size
    totals_rows_layout = totals_layout.get("rows") if totals_layout else {}
    total_a_pagar_cfg = totals_rows_layout.get("TOTAL_A_PAGAR", {}) if isinstance(totals_rows_layout, dict) else {}
    flujo_cfg = totals_rows_layout.get("FLUJO_MENSUAL", {}) if isinstance(totals_rows_layout, dict) else {}
    total_a_pagar_y = safe_float(total_a_pagar_cfg.get("y_pt"), 428.59) if total_a_pagar_cfg else 428.59
    flujo_mensual_y = safe_float(flujo_cfg.get("y_pt"), 417.85) if flujo_cfg else 417.85
    draw_right_currency(
        pdf_canvas,
        totals_right_x,
        total_a_pagar_y,
        calculate_total_a_pagar(row),
        font_name="Helvetica-Bold",
        font_size=totals_font_size,
        max_width=totals_max_width,
        allow_negative_red=False,
        display_absolute_value=True,
    )
    draw_right_currency(
        pdf_canvas,
        totals_right_x,
        flujo_mensual_y,
        calculate_flujo_mensual(row),
        font_name="Helvetica-Bold",
        font_size=totals_font_size,
        max_width=totals_max_width,
        allow_negative_red=False,
        display_absolute_value=True,
    )

    # Firma formal:
    # El template PDF formal sin datos ya contiene la estructura completa
    # de la firma, lineas, textos legales y parrafos.
    # Por eso aqui NO se dibujan bloques adicionales como clear_area, image,
    # line, title, receipt_line o body. Solo se sobreponen los dos datos
    # variables requeridos: nombre completo y monto.

    signature_name_cfg = (signature_layout.get("image_name") or {}) if signature_layout else {}
    if is_layout_block_enabled(signature_name_cfg, default=True):
        draw_center_text(
            pdf_canvas,
            safe_float(signature_name_cfg.get("center_x_pt"), 295.0),
            safe_float(signature_name_cfg.get("y_pt"),214),
            normalize_single_line_text(get_row_value(row, "NOMBRECOMISIONISTA")),
            font_name=str(signature_name_cfg.get("font_name") or "Helvetica"),
            font_size=parse_float(signature_name_cfg.get("font_size"), 8.4),
            max_width=safe_float(signature_name_cfg.get("max_width_pt"), 260),
            trim_overflow=True,
            shrink_to_fit=True
        )

    signature_amount_cfg = (signature_layout.get("image_amount") or {}) if signature_layout else {}
    if is_layout_block_enabled(signature_amount_cfg, default=True):
        draw_text(
            pdf_canvas,
            safe_float(signature_amount_cfg.get("x_pt"), 201.0),
            safe_float(signature_amount_cfg.get("y_pt"), 166.6),
            get_signature_total_amount(row),
            font_name=str(signature_amount_cfg.get("font_name") or "Helvetica"),
            font_size=parse_float(signature_amount_cfg.get("font_size"), 5.6),
            max_width=safe_float(signature_amount_cfg.get("max_width_pt"), 80),
            trim_overflow=True,
            shrink_to_fit=True
        )

    mes_anio_cfg = (signature_layout.get("image_name") or {}) if signature_layout else {}
    if is_layout_block_enabled(mes_anio_cfg, default=True):
        draw_center_text(
            pdf_canvas,
            safe_float(mes_anio_cfg.get("center_x_pt"), 457.5),
            safe_float(mes_anio_cfg.get("y_pt"),167.5),
            mes_anio,
            font_name=str(mes_anio_cfg.get("font_name") or "Helvetica"),
            font_size=parse_float(mes_anio_cfg.get("font_size"), 5.6),
            max_width=safe_float(mes_anio_cfg.get("max_width_pt"), 80),
            trim_overflow=True,
            shrink_to_fit=True
        )    
    pdf_canvas.save()
    packet.seek(0)
    return packet

def build_output_filename(row):
    district_value = sanitize_filename(row.get("CRDISTRITO") or "SIN_DISTRITO", fallback="SIN_DISTRITO")
    month_year_value = sanitize_filename(format_month_year_token(row), fallback="MESANIO")
    base_name = f"{district_value}_{month_year_value}"
    return base_name + ".pdf"


def cleanup_generated_pdfs(output_dir):
    removed = 0
    generated_pdf_pattern = re.compile(r".+_[A-Z]{3}\d{4}(?:_\d+)?\.pdf$", re.IGNORECASE)
    for file_name in os.listdir(output_dir):
        file_path = os.path.join(output_dir, file_name)
        if not os.path.isfile(file_path):
            continue
        if not file_name.lower().endswith(".pdf"):
            continue
        if not generated_pdf_pattern.match(file_name):
            continue
        os.remove(file_path)
        removed += 1
    return removed
# Margen de seguridad para evitar PDFs que queden apenas arriba de 7 MB.
MAX_OUTPUT_PDF_SIZE_BYTES = int(6.8 * 1024 * 1024)


def get_active_logger():
    active_logger = globals().get("logger")
    if active_logger is not None:
        return active_logger
    return logging.getLogger(__name__)


def format_file_size(num_bytes):
    try:
        size_value = float(num_bytes)
    except Exception:
        return "desconocido"

    units = ["B", "KB", "MB", "GB"]
    unit_index = 0
    while size_value >= 1024 and unit_index < len(units) - 1:
        size_value /= 1024.0
        unit_index += 1
    return f"{size_value:.2f} {units[unit_index]}"


def optimize_pdf_with_pymupdf(pdf_path, output_path):
    import fitz

    source_pdf = fitz.open(pdf_path)
    try:
        source_pdf.save(
            output_path,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
            pretty=False,
        )
    finally:
        source_pdf.close()


def find_qpdf_executable():
    candidate = shutil.which("qpdf")
    if candidate:
        return candidate

    common_paths = [
        os.path.join(scriptDir, "qpdf.exe"),
        os.path.join(scriptDir, "qpdf", "qpdf.exe"),
        os.path.join(scriptDir, "bin", "qpdf.exe"),
        os.path.join(runtimeBaseDir, "qpdf.exe"),
        os.path.join(runtimeBaseDir, "qpdf", "qpdf.exe"),
        os.path.join(runtimeBaseDir, "bin", "qpdf.exe"),
        r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe",
        r"C:\Program Files\qpdf\bin\qpdf.exe",
        r"C:\Program Files (x86)\qpdf\bin\qpdf.exe",
    ]
    for candidate in common_paths:
        if os.path.exists(candidate):
            return candidate
    return None


def optimize_pdf_with_qpdf(pdf_path):
    qpdf_executable = find_qpdf_executable()
    if not qpdf_executable or not os.path.exists(pdf_path):
        return False, "qpdf no disponible"

    temp_output_path = pdf_path + ".qpdf-opt.pdf"
    try:
        subprocess.run(
            [
                qpdf_executable,
                "--stream-data=compress",
                "--recompress-flate",
                "--object-streams=generate",
                "--compression-level=9",
                "--linearize",
                "--",
                pdf_path,
                temp_output_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if not os.path.exists(temp_output_path):
            return False, "qpdf no genero archivo de salida"

        original_size = os.path.getsize(pdf_path)
        optimized_size = os.path.getsize(temp_output_path)
        if optimized_size > 0 and optimized_size < original_size:
            os.replace(temp_output_path, pdf_path)
            return True, f"{original_size} -> {optimized_size}"

        os.remove(temp_output_path)
        return False, f"sin mejora de tamano ({original_size} -> {optimized_size})"
    except Exception as exc:
        if os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except OSError:
                pass
        return False, str(exc)


def build_python_raster_profiles():
    return [
        {"name": "raster_110_75", "dpi": 110, "quality": 75},
        {"name": "raster_96_65", "dpi": 96, "quality": 65},
        {"name": "raster_84_55", "dpi": 84, "quality": 55},
        {"name": "raster_72_45", "dpi": 72, "quality": 45},
        {"name": "raster_64_35", "dpi": 64, "quality": 35},
        {"name": "raster_56_25", "dpi": 56, "quality": 25},
        {"name": "raster_48_20", "dpi": 48, "quality": 20},
        {"name": "raster_40_15", "dpi": 40, "quality": 15},
        {"name": "raster_32_10", "dpi": 32, "quality": 10},
        {"name": "raster_28_08", "dpi": 28, "quality": 8},
        {"name": "raster_24_06", "dpi": 24, "quality": 6},
    ]


def rasterize_pdf_with_pymupdf(pdf_path, output_path, dpi, quality):
    import fitz
    from PIL import Image

    source_pdf = fitz.open(pdf_path)
    images = []
    try:
        scale = float(dpi) / 72.0
        matrix = fitz.Matrix(scale, scale)

        for page in source_pdf:
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            images.append(image)

        if not images:
            raise ValueError("El PDF no contiene paginas para rasterizar")

        first_image, remaining_images = images[0], images[1:]
        first_image.save(
            output_path,
            "PDF",
            resolution=float(dpi),
            save_all=True,
            append_images=remaining_images,
            quality=int(quality),
            optimize=True,
        )
    finally:
        source_pdf.close()
        for image in images:
            try:
                image.close()
            except Exception:
                pass


def optimize_pdf_with_python_fallbacks(pdf_path, active_logger):
    current_size = os.path.getsize(pdf_path)

    pymupdf_temp_path = pdf_path + ".pymupdf_tmp.pdf"
    try:
        if os.path.exists(pymupdf_temp_path):
            os.remove(pymupdf_temp_path)

        optimize_pdf_with_pymupdf(pdf_path, pymupdf_temp_path)
        if os.path.exists(pymupdf_temp_path):
            optimized_size = os.path.getsize(pymupdf_temp_path)
            if optimized_size < current_size:
                os.replace(pymupdf_temp_path, pdf_path)
                current_size = optimized_size
            else:
                os.remove(pymupdf_temp_path)
    except Exception as exc:
        active_logger.warning(
            "PyMuPDF no pudo optimizar %s: %s",
            os.path.basename(pdf_path),
            exc,
        )
        if os.path.exists(pymupdf_temp_path):
            try:
                os.remove(pymupdf_temp_path)
            except Exception:
                pass

    if current_size <= MAX_OUTPUT_PDF_SIZE_BYTES:
        return

    best_candidate_path = None
    best_candidate_size = current_size
    temp_paths = []

    try:
        for profile in build_python_raster_profiles():
            temp_path = pdf_path + f".{profile['name']}.pdf"
            temp_paths.append(temp_path)
            if os.path.exists(temp_path):
                os.remove(temp_path)

            try:
                rasterize_pdf_with_pymupdf(
                    pdf_path,
                    temp_path,
                    dpi=profile["dpi"],
                    quality=profile["quality"],
                )
            except Exception as exc:
                active_logger.warning(
                    "Fallback raster %s fallo para %s: %s",
                    profile["name"],
                    os.path.basename(pdf_path),
                    exc,
                )
                continue

            if not os.path.exists(temp_path):
                continue

            optimized_size = os.path.getsize(temp_path)

            if optimized_size < best_candidate_size:
                if best_candidate_path and os.path.exists(best_candidate_path) and best_candidate_path != temp_path:
                    os.remove(best_candidate_path)
                best_candidate_path = temp_path
                best_candidate_size = optimized_size
            else:
                os.remove(temp_path)

            if optimized_size <= MAX_OUTPUT_PDF_SIZE_BYTES:
                break

        if best_candidate_path and os.path.exists(best_candidate_path) and best_candidate_size < current_size:
            os.replace(best_candidate_path, pdf_path)
            best_candidate_path = None
    finally:
        for temp_path in temp_paths:
            if temp_path == best_candidate_path:
                continue
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


def optimize_pdf_file_size(pdf_path):
    if not pdf_path or not os.path.exists(pdf_path):
        return

    active_logger = get_active_logger()
    original_size = os.path.getsize(pdf_path)
    if original_size <= MAX_OUTPUT_PDF_SIZE_BYTES:
        return

    qpdf_optimized, _ = optimize_pdf_with_qpdf(pdf_path)
    if not qpdf_optimized or os.path.getsize(pdf_path) > MAX_OUTPUT_PDF_SIZE_BYTES:
        optimize_pdf_with_python_fallbacks(pdf_path, active_logger)

    current_size = os.path.getsize(pdf_path)

    if current_size > MAX_OUTPUT_PDF_SIZE_BYTES:
        active_logger.warning(
            "PDF optimizado parcialmente: %s quedo en %s y sigue arriba del limite de %s.",
            os.path.basename(pdf_path),
            format_file_size(current_size),
            format_file_size(MAX_OUTPUT_PDF_SIZE_BYTES),
        )

def add_row_pages_to_writer(template_path, writer, row, layout):
    # Toma el PDF base, genera el overlay para un registro y fusiona ambas capas en el writer final.
    reader = PdfReader(template_path)
    page_index = layout.get("page_index", 0)

    if page_index < 0 or page_index >= len(reader.pages):
        raise batchPDF(
            f"page_index fuera de rango para template '{template_path}': {page_index}"
        )

    overlay_stream = draw_finiquito_overlay(
        float(reader.pages[page_index].mediabox.width),
        float(reader.pages[page_index].mediabox.height),
        row,
        layout
    )
    overlay_pdf = PdfReader(overlay_stream)

    for current_index, page in enumerate(reader.pages):
        if current_index == page_index:
            page.merge_page(overlay_pdf.pages[0])
        compress_page = getattr(page, "compress_content_streams", None)
        if callable(compress_page):
            try:
                compress_page()
            except Exception:
                pass
        writer.add_page(page)


def group_rows_by_district(rows):
    grouped = {}
    order = []
    for row in rows:
        district_key = str((row.get("CRDISTRITO") or "")).strip() or "SIN_DISTRITO"
        if district_key not in grouped:
            grouped[district_key] = []
            order.append(district_key)
        grouped[district_key].append(row)
    return [(district_key, grouped[district_key]) for district_key in order]


def generate_pdfs_from_csv_template(scriptConfig, arg1, preserve_generated_files=False):
    # Flujo local principal:
    # 1. Lee el CSV formal
    # 2. Resuelve template/layout/RDL
    # 3. Agrupa por distrito
    # 4. Genera un PDF por distrito
    config_section = get_configuration_section(scriptConfig)
    csv_path = resolve_input_csv_path(config_section)
    output_dir = ensure_directory(PDFS_FINIQUITOS_DIR)
    template_default_path = find_default_template_path()
    template_config_value = get_config_value(config_section, "LOCAL_TEMPLATE_PATH", None)
    template_path = resolve_existing_path(
        template_config_value,
        [runtimeScriptsRoot, scriptDir, runtimeProjectRoot, projectRoot]
    )
    if not template_path:
        template_path = resolve_existing_path(
            template_default_path,
            [projectRoot, runtimeProjectRoot, scriptDir, runtimeScriptsRoot]
        )
    layout = load_template_layout(scriptConfig)
    rdl_metadata = layout.get("rdl") or {}

    if not template_path or not os.path.exists(template_path):
        raise batchPDF(
            "Falta el template PDF. Coloca una plantilla en TemplatePDF "
            "(root/scripts o root/Script) o configura LOCAL_TEMPLATE_PATH "
            "con una ruta existente."
        )

    if not csv_path or not os.path.exists(csv_path):
        raise batchPDF(
            "No existe el CSV de entrada requerido para generacion local: "
            f"{csv_path}. Ejecuta primero el proceso que genera "
            "CSVparaPDFFormal.csv en root/Data."
        )

    if rdl_metadata.get("path"):
        print(f"[LOCAL_TEMPLATE] RDL detectado: {rdl_metadata['path']}")
    print(f"[LOCAL_TEMPLATE] CSV de entrada: {csv_path}")

    rows = load_csv_rows(csv_path)

    total_rows = len(rows)
    max_rows = parse_optional_int(
        get_config_value(config_section, "LOCAL_TEMPLATE_MAX_ROWS", None),
        default=None
    )
    if max_rows is not None and max_rows > 0:
        rows = rows[:max_rows]
    print(f"[LOCAL_TEMPLATE] Filas leidas del CSV: {total_rows}")
    print(f"[LOCAL_TEMPLATE] Filas a procesar para PDF: {len(rows)}")

    grouped_rows = group_rows_by_district(rows)
    max_pdfs = parse_optional_int(
        get_config_value(config_section, "LOCAL_TEMPLATE_MAX_PDFS", None),
        default=None
    )
    if max_pdfs is not None and max_pdfs > 0:
        grouped_rows = grouped_rows[:max_pdfs]

    # FUTURO - ENVIO DE CORREOS POR DISTRITO 
    #
    # Objetivo:
    # 1. Leer el CSV exportado desde DistrictEmailTable.
    # 2. Tomar la columna 0 como llave del distrito, por ejemplo: "DIS-10DCU32XXA".
    # 3. Tomar la columna 1 como correo destino.
    # 4. Cuando se genere un PDF de un distrito, enviar ese PDF a todos los correos
    #    asociados a la llave "DIS-" + district_key.
    #
    # Configuracion sugerida (comentada, no activa):
    # district_email_csv_path = os.path.join(DATA_DIR, "DistrictEmailTable.csv")
    # district_email_map = {}
    # if os.path.exists(district_email_csv_path):
    #     with open(district_email_csv_path, "r", encoding="utf-8-sig", newline="") as csv_file:
    #         reader = csv.DictReader(csv_file, delimiter=";")
    #         for row in reader:
    #             district_lookup_key = str(row.get("col_0", "")).strip()
    #             email_value = str(row.get("col_1", "")).strip()
    #             if district_lookup_key and email_value:
    #                 district_email_map.setdefault(district_lookup_key, []).append(email_value)
    #
    # Uso sugerido al generar cada PDF (comentado, no activo):
    # district_lookup_key = f"DIS-{district_key}"
    # district_recipients = district_email_map.get(district_lookup_key, [])
    # if district_recipients:
    #     send_local_template_email(output_path, district_recipients)
    # else:
    #     send_local_template_email(output_path, ["dsuazo@exsoinf.com"])

    email_recipient = str(
        get_config_value(config_section, "LOCAL_TEMPLATE_EMAIL_TO", "dsuazo@exsoinf.com")
    ).strip() or "dsuazo@exsoinf.com"
    generated_files = []

    for index, (district_key, district_rows) in enumerate(grouped_rows, start=1):
        output_name = build_output_filename(district_rows[0])
        output_path = os.path.join(output_dir, output_name)
        writer = PdfWriter()
        for district_row in district_rows:
            add_row_pages_to_writer(template_path, writer, district_row, layout)
        if os.path.exists(output_path):
            os.remove(output_path)
        try:
            writer.compress_identical_objects()
        except Exception as exc:
            print(f"[LOCAL_TEMPLATE] No se pudo optimizar el PDF antes de guardar {output_path}: {exc}")
        with open(output_path, "wb") as output_file:
            writer.write(output_file)

        optimize_pdf_file_size(output_path)
        generated_files.append(output_path)
        print(
            f"[LOCAL_TEMPLATE] PDF generado {index}/{len(grouped_rows)}: {output_path} "
            f"({len(district_rows)} pagina(s) / registro(s) para CRDISTRITO={district_key})"
        )
        
        try:
            sent, detail = send_local_template_email(output_path, email_recipient)
            if sent:
                print(f"[LOCAL_TEMPLATE] Correo enviado a {email_recipient}: {output_path}")
                if not preserve_generated_files and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                        print(f"[LOCAL_TEMPLATE] PDF eliminado tras envio: {output_path}")
                    except Exception as exc:
                        print(f"[LOCAL_TEMPLATE] No se pudo eliminar el PDF enviado {output_path}: {exc}")
            else:
                print(f"[LOCAL_TEMPLATE] Correo omitido para {output_path}: {detail}")
        except Exception as exc:
            print(f"[LOCAL_TEMPLATE] No se pudo enviar correo para {output_path}: {exc}")
    return generated_files


def generate_for_district(key, district_reqs, arg1, arg2, endpoint, repId, grpId, headers,
                          timeout=1200, poll_interval=10):
    """
    1) Create watch_dir & arch_dir.
    2) Launch a ThreadPool to run pyLd(...) for each request in district_reqs.
    3) Poll watch_dir until all expected PDFs appear, move them to arch_dir.
    4) Return (arch_dir, safe_dir, key, first_req_info) so that merge can run later.
    """
    # 1. Build directories exactly as your original code did:
    base_publication = r"/root/Publication/"
    if arg2 == "ADHOC":
        watch_dir = os.path.join(base_publication, str(arg2), str(key))
        arch_dir = os.path.join(base_publication, "Safe", str(arg2), str(key))
        safe_dir = os.path.join(base_publication, "Safe", str(arg2))
    else:
        watch_dir = os.path.join(base_publication, str(key))
        arch_dir = os.path.join(base_publication, "Safe", str(key))
        safe_dir = os.path.join(base_publication, "Safe")

    # Ensure they exist:
    os.makedirs(watch_dir, exist_ok=True)
    os.makedirs(safe_dir, exist_ok=True)
    os.makedirs(arch_dir, exist_ok=True)


    expected = len(district_reqs)
    # 2. Fire off all pyLd(...) calls in parallel:
    #    WeÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ll reuse a small ThreadPool so that pyLd calls themselves run concurrently.
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as gen_pool:
        futures = [
            gen_pool.submit(pyLd, req, endpoint, repId, grpId, arg1, arg2, headers)
            for req in district_reqs
        ]
        pending_futures = set(futures)

        # 3. Meanwhile, poll watch_dir for output PDFs. Once they appear, move them to arch_dir.
        moved_files = set()
        start_time = time.time()

        while len(moved_files) < expected:
            failed_future = None
            for future in list(pending_futures):
                if not future.done():
                    continue
                pending_futures.remove(future)
                exception = future.exception()
                if exception is not None:
                    failed_future = exception
                    break

            if failed_future is not None:
                raise RuntimeError(f"[District {key}] Error generando PDFs: {failed_future}") from failed_future

            for fname in os.listdir(watch_dir):
                if fname in moved_files:
                    continue
                src = os.path.join(watch_dir, fname).replace("\\", "/")
                if os.path.isfile(src) and fname.lower().endswith(".pdf"):
                    try:
                        dst = os.path.join(arch_dir, fname)
                        shutil.move(src, dst)
                        moved_files.add(fname)
                        # print or logging to track progress:
                        print(f"[District {key}] Moved {fname} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ arch_dir")
                    except Exception as e:
                        print(f"[District {key}] Failed to move {fname}: {str(e)}")
            # Timeout check:
            if time.time() - start_time > timeout:
                raise RuntimeError(
                    f"[District {key}] Timed out after {timeout}s waiting for PDFs; "
                    f"moved {len(moved_files)}/{expected}."
                )
            time.sleep(poll_interval)

        # 4. Ensure all pyLd futures have finished (they might have succeeded even if PDFs moved early):
        concurrent.futures.wait(futures)
        for future in futures:
            future.result()

    # Return enough info so the callback can merge+cleanup:
    # If you need the same ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œsome_valueÃƒÂ¢Ã¢â€šÂ¬Ã‚Â as in your original (districts[key][0][4]), pass it back:
    first_req = district_reqs[0]
    # e.g. if districts[key][0] is a tuple and index 4 is something you need in merge_pdfs:
    some_value = str(first_req[4])
    return (arch_dir, safe_dir, key, str(arg1), some_value, watch_dir)


# --- Helper function: merge & delete folders for one district ---
def merge_and_cleanup(arch_dir, safe_dir, key, arg1_str, some_value, watch_dir):
    """
    1) Call merge_pdfs() exactly as you used to.
    2) Delete arch_dir and watch_dir once done.
    """
    print(f"[District {key}] Starting merge of PDFs in {arch_dir} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ {safe_dir}")
    try:
        merge_pdfs(arch_dir, safe_dir, key, arg1_str, some_value)
    except Exception as e:
        print(f"[District {key}] ERROR during merge_pdfs: {e}")
        # You might decide to return here or attempt cleanup anyway.
    else:
        print(f"[District {key}] Merge complete. Cleaning up folders.")

    # Cleanup: delete arch_dir and watch_dir
    for path in (arch_dir, watch_dir):
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
                print(f"[District {key}] Successfully deleted folder: {path}")
            except Exception as e:
                print(f"[District {key}] Error deleting {path}: {e}")
        else:
            print(f"[District {key}] Path does not exist (skipping deletion): {path}")


# ======================================
# Main
# ======================================
def pipeline_all_districts(districts, arg1, arg2, endpoint, repId, grpId, headers, timeout=1200, poll_interval=10):
    # 1) One Executor for ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œgenerationÃƒÂ¢Ã¢â€šÂ¬Ã‚Â tasks (one per district)
    gen_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    #    (Use a small number here if you don't want too many districts generating at once.
    #     Or set max_workers=len(districts) if your machine can handle that.)

    # 2) Another Executor for ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œmerge+cleanupÃƒÂ¢Ã¢â€šÂ¬Ã‚Â tasks
    merge_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    all_generation_futures = []

    for key, req_list in districts.items():
        # Submit a generation task for this district:
        fut = gen_executor.submit(
            generate_for_district,
            key,
            req_list,
            arg1,
            arg2,
            endpoint,
            repId,
            grpId,
            headers,
            timeout,
            poll_interval
        )
        all_generation_futures.append(fut)

        # As soon as regenerate_for_district returns (i.e. all PDFs moved), schedule merge in merge_executor:
        def _schedule_merge(done_future, submitted_key=key):
            try:
                arch_dir, safe_dir, district_key, arg1_str, some_val, watch_dir = done_future.result()
            except Exception as exc:
                print(f"[District {submitted_key}] Generation failed: {exc}")
                return

            # Submit the merge_and_cleanup job:
            merge_executor.submit(
                merge_and_cleanup,
                arch_dir,
                safe_dir,
                district_key,
                arg1_str,
                some_val,
                watch_dir
            )

        fut.add_done_callback(_schedule_merge)

        # Immediately move on to next key: we do NOT wait here.

    # Optionally: wait for all generation tasks to complete before shutting down gen_executor:
    concurrent.futures.wait(all_generation_futures)
    gen_executor.shutdown(wait=True)
    print("All generation tasks finished. You may still have merges running in the background.")

    # If you want to wait until all merging is done before exiting:
    merge_executor.shutdown(wait=True)
    print("All merging+cleanup tasks finished.")



def pyLd(row,endpt,rpt,grp,repName,adh,headers):
    requests = get_requests_module()
    if adh is None:
        adh = ""
    else:
        adh=adh + "/"
    finPayload = {"publisherId": -1,
               "name": str(repName),
               "publishType": "WebReportPdf",
               "publisherParams": [
                   {
                       "webReportId": str(rpt),
                       "workflowGroupId": grp,
                       "parameterValues": {
                           "paTienda": {
                               "literalType": "String",
                               "value": str(row[3]),
                               "isDate": False,
                               "expressionType": "literal"
                           },
                           "paPlaza": {
                               "literalType": "String",
                               "value": str(row[1]),
                               "isDate": False,
                               "expressionType": "literal"
                           },
                           "paPeriodo": {
                               "literalType": "String",
                               "value": str(row[4]),
                               "isDate": False,
                               "expressionType": "literal"
                           },
                           "paComisionista": {
                               "literalType": "String",
                               "value": str(row[0]),
                               "isDate": False,
                               "expressionType": "literal"
                           },
                           "paDistrito": {
                               "literalType": "String",
                               "value": str(row[2]),
                               "isDate": False,
                               "expressionType": "literal"
                           }
                       },
                       "type": "WebReport"
                   },
                   {
                       "mailWebUsers": False,
                       "payeeId": "",
                       "recipients": [],
                       "emailSubject": "",
                       "emailBody": "",
                       "type": "Email"
                   },
                   {
                       "fileName": (str(repName) +str(row[0])+"_"+str(row[1])+"_"+str(row[2])+"_"+str(row[3])+"_"+str(row[4])),
                       "type": "File"
                   },
                   {
                       "numberPages": True,
                       "showTotalPageNumber": True,
                       "portraitOrientation": True,
                       "autoScale": True,
                       "customScaleValue": 100,
                       "footerText": "",
                       "mergePdfFiles": False,
                       "type": "Pdf"
                   }
               ],
               "location": adh + str(row[2])
               }
    try:
        response = requests.request("POST", endpt, data=json.dumps(finPayload), headers=headers, timeout=120)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        detail = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = response.text.strip()
            except Exception:
                detail = ""
        if detail:
            raise RuntimeError(f"Error calling publish API: {exc}. Response: {detail}") from exc
        raise RuntimeError(f"Error calling publish API: {exc}") from exc



def merge_pdfs(input_path,output_path,dist,repName,period):
    merger = PdfWriter()
    file_list=glob.glob(os.path.join(input_path,"*.pdf").replace("\\", "/"))
    for file_path in file_list:
        if not os.path.isfile(file_path):
            print(f"file {file_path} not found")
            continue

        try:
            merger.append(file_path)
            print(f"Appended {file_path}")
        except Exception as e:
            print(f"Error appending '{file_path}': {e}")
    output_file = os.path.join(output_path, str(repName) + "_" + str(dist) + "_" + str(period) + ".pdf")
    try:

        with open(output_file,"wb") as out_f:
            merger.write(out_f)

        optimize_pdf_file_size(output_file)
        print(f"\nSuccessfully wrote merged PDF to: {output_file}")
    except Exception as e:
        print(f"Failed to write output file '{output_file}': {e}")

    finally:
        merger.close()


def merge_pdf_files(file_paths, output_file):
    merger = PdfWriter()
    try:
        for file_path in file_paths:
            if not os.path.isfile(file_path):
                print(f"file {file_path} not found")
                continue

            merger.append(file_path)

        try:
            merger.compress_identical_objects()
        except Exception as exc:
            print(f"[LOCAL_TEMPLATE] No se pudo optimizar el PDF consolidado {output_file}: {exc}")
        with open(output_file, "wb") as output_stream:
            merger.write(output_stream)

        optimize_pdf_file_size(output_file)
    finally:
        merger.close()

def runLogic(scriptConfig, apiConfig, arg1, arg2):
    # Decide entre modo LOCAL_TEMPLATE y modo remoto heredado.
    config_section = get_configuration_section(scriptConfig)
    execution_mode = str(
        get_config_value(config_section, "EXECUTION_MODE", "LOCAL_TEMPLATE")
    ).strip().upper()

    if execution_mode == "LOCAL_TEMPLATE":
        district_email_table = str(
            get_config_value(config_section, "DISTRICT_EMAIL_TABLE", "DistrictEmailTable")
        ).strip()
        district_email_output_csv = os.path.join(DATA_DIR, "DistrictEmailTable.csv")
        if district_email_table:
            try:
                local_api_config = get_runtime_api_config()
                local_headers = {
                    'Authorization': 'Bearer ' + local_api_config["API_USER_KEY"],
                    'Model': local_api_config["MODEL"],
                    'Content-Type': 'application/json'
                }
                export_customtable_view_to_csv(
                    local_api_config["API_URL"],
                    local_headers,
                    district_email_table,
                    district_email_output_csv
                )
            except Exception as exc:
                print(f"[LOCAL_TEMPLATE] No se pudo exportar {district_email_table} a CSV: {exc}")
        merged_output_enabled = parse_bool(
            get_config_value(config_section, "LOCAL_TEMPLATE_MERGE_OUTPUT", False),
            default=False
        )
        generated_files = generate_pdfs_from_csv_template(
            scriptConfig,
            arg1,
            preserve_generated_files=merged_output_enabled,
        )
        if merged_output_enabled and generated_files:
            output_dir = ensure_directory(PDFS_FINIQUITOS_DIR)
            merged_name = sanitize_filename(f"{arg1}_consolidado") + ".pdf"
            merged_path = os.path.join(output_dir, merged_name)
            if os.path.exists(merged_path):
                os.remove(merged_path)
            merge_pdf_files(generated_files, merged_path)
            print(f"[LOCAL_TEMPLATE] PDF consolidado generado: {merged_path}")
            for pdf_path in generated_files:
                if os.path.exists(pdf_path):
                    try:
                        os.remove(pdf_path)
                        print(f"[LOCAL_TEMPLATE] PDF eliminado tras consolidar: {pdf_path}")
                    except Exception as exc:
                        print(f"[LOCAL_TEMPLATE] No se pudo eliminar el PDF consolidado {pdf_path}: {exc}")
        return

    requests = get_requests_module()

    model=            apiConfig["MODEL"]
    API_KEY =         apiConfig["API_USER_KEY"]
    apiUrl =          apiConfig["API_URL"]
    if(arg2 is None):
        view = config_section["VIEW"]
    elif(arg2=="ADHOC"):
        view = config_section["VIEW_ADHOC"]
    else:
        raise batchPDF("Args Not defined:", sys.exc_info()[0])

    repId =           config_section[arg1]
    grpId =           config_section["WORKFLOW_GROUP_ID"]

    #model = 'femcodev'
    #API_KEY = 'icm-I5AgSOSw+BYXgBCkTuRHFk+cV1V++dk3UemgIB3HGTw='
    #host = r'https://api.cloud.varicent.com/'
    endpoint = apiUrl + r'api/v1/rpc/publish'

    headers = {
        'Authorization': 'Bearer ' + API_KEY,
        'Model': model,
        'Content-Type': 'application/json'
    }
    district_email_table = str(
        get_config_value(config_section, "DISTRICT_EMAIL_TABLE", "DistrictEmailTable")
    ).strip()
    district_email_output_csv = os.path.join(DATA_DIR, "DistrictEmailTable.csv")
    if district_email_table:
        try:
            export_customtable_view_to_csv(
                apiUrl,
                headers,
                district_email_table,
                district_email_output_csv
            )
        except Exception as exc:
            print(f"[REMOTE] No se pudo exportar {district_email_table} a CSV: {exc}")

    plListURL=apiUrl + f"api/v1/customtables/{str(view)}/inputforms/0/data?limit=100000"
    #apiUrl = f"https://api.cloud.varicent.com/api/v1/customtables/{str(view)}/inputforms/0/data?limit=100000"
    #apiUrl = r"https://api.cloud.varicent.com/api/v1/customtables/finView/inputforms/0/data?limit=100000"

    try:
        plList = requests.request("GET", plListURL, headers=headers, timeout=120)
        plList.raise_for_status()
    except requests.RequestException as exc:
        detail = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = response.text.strip()
            except Exception:
                detail = ""
        if detail:
            raise batchPDF(f"Error consultando la vista origen: {exc}. Response: {detail}") from exc
        raise batchPDF(f"Error consultando la vista origen: {exc}") from exc

    try:
        jsonData = plList.json()
    except ValueError as exc:
        raise batchPDF(f"La respuesta de la vista origen no es JSON valido: {exc}") from exc

    if not isinstance(jsonData, dict) or not isinstance(jsonData.get("data"), list):
        raise batchPDF("La respuesta de la vista origen no contiene una lista valida en 'data'.")

    districts = {}

    for row in jsonData["data"]:
        key = row[2]
        districts.setdefault(key, []).append(row)

    poll_interval = parse_float(get_config_value(config_section, "REMOTE_POLL_INTERVAL_SECONDS", 10.0), 10.0)
    timeout = parse_int(get_config_value(config_section, "REMOTE_TIMEOUT_SECONDS", 1200), 1200)
    pipeline_all_districts(
        districts,
        arg1,
        arg2,
        endpoint,
        repId,
        grpId,
        headers,
        timeout=timeout,
        poll_interval=poll_interval
    )

def main():
    # Punto de entrada operativo del script:
    # carga configuración, prepara logging, limpia archivos antiguos y ejecuta el flujo seleccionado.
    global logger
    logger = None

    try:
        try:
            scriptConfig = load_json_config(scriptConfigFile)
            config_section = get_configuration_section(scriptConfig)
            execution_mode = str(
                get_config_value(config_section, "EXECUTION_MODE", "LOCAL_TEMPLATE")
            ).strip().upper()
            if execution_mode == "LOCAL_TEMPLATE":
                apiConfig = {}
            else:
                apiConfig = load_json_config(apiConfigFile)
        except batchPDF:
            raise
        except Exception as exc:
            raise batchPDF(f"Error loading config file: {exc}") from exc

        if execution_mode == "LOCAL_TEMPLATE":
            if len(sys.argv) >= 2:
                arg1 = sys.argv[1]
                arg2 = sys.argv[2] if len(sys.argv) >= 3 else None
            else:
                arg1 = "PDFTemplate"
                arg2 = None
        else:
            if(len(sys.argv)==3):
                arg1=sys.argv[1] #Report
                arg2 = sys.argv[2] #Adhoc
            elif(len(sys.argv)==2):
                arg1=sys.argv[1] #Report
                arg2=None #Adhoc
            else:
                raise batchPDF("Too many or not enough arguments: ", sys.exc_info()[0])
        # Setup Logging
        try:
            logLevel = get_config_value(config_section, "LOG_LEVEL", "INFO")
            setup_file_logger(logFile, get_log_level(logLevel))
            logger = logging.getLogger(__name__)

            # print START line with start time
            global eTime
            eTime = ElapsedTime()
            logger.info(get_start_line(eTime, SCRIPT_NAME, VERSION))
            logger.info("Python Version: " + str(sys.version_info))
            logger.info("logFile:               " + logFile)
        except Exception:
            raise batchPDF("Error when setting up log file:{} ".format(logFile) + str(sys.exc_info()[0]))

        # Cleanup Log files
        try:
            logFolder = os.path.dirname(logFile) + "/"
            fileRetentionDays = get_config_value(
                config_section,
                "FILE_RETENTION_DAYS",
                7
            )
            remove_files_x_days_old(logFolder, fileRetentionDays)
        except Exception:
            raise batchPDF("Errors while trying to delete old file(s)" + str(sys.exc_info()[1]))

        # script logic
        runLogic(scriptConfig, apiConfig, arg1,arg2)

    except Exception:
        exit_handler(logger, "Exception caught: " + str(sys.exc_info()[1]), successful=False)

    # script completed with no issues if it makes it to here
    exit_handler(logger, get_end_line(eTime), successful=True)

if __name__ == '__main__':
    main()
