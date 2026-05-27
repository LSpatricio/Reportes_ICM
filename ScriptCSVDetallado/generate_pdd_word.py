#!/usr/bin/env python3
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
ET.register_namespace("w", NS["w"])


def qn(name: str) -> str:
    return f"{{{NS['w']}}}{name}"


def text_of(paragraph: ET.Element) -> str:
    return "".join((t.text or "") for t in paragraph.findall(".//w:t", NS))


def clear(paragraph: ET.Element) -> None:
    for child in list(paragraph):
        if child.tag != qn("pPr"):
            paragraph.remove(child)


def set_text(paragraph: ET.Element, text: str) -> None:
    clear(paragraph)
    run = ET.SubElement(paragraph, qn("r"))
    t = ET.SubElement(run, qn("t"))
    if text.startswith(" ") or text.endswith(" "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text


def set_lines(paragraph: ET.Element, lines: list[str]) -> None:
    clear(paragraph)
    for idx, line in enumerate(lines):
        if idx:
            ET.SubElement(ET.SubElement(paragraph, qn("r")), qn("br"))
        run = ET.SubElement(paragraph, qn("r"))
        ET.SubElement(run, qn("t")).text = line


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fill_table(table: ET.Element, header: list[str], rows: list[list[str]]) -> bool:
    trs = table.findall("./w:tr", NS)
    if not trs:
        return False
    first = [text_of(c).strip().lower() for c in trs[0].findall("./w:tc", NS)]
    if first[: len(header)] != [h.lower() for h in header]:
        return False
    for r_idx in range(1, len(trs)):
        cells = trs[r_idx].findall("./w:tc", NS)
        vals = rows[r_idx - 1] if r_idx - 1 < len(rows) else [""] * len(cells)
        for c_idx, cell in enumerate(cells):
            paras = cell.findall(".//w:p", NS)
            if paras:
                set_text(paras[0], vals[c_idx] if c_idx < len(vals) else "")
                for p in paras[1:]:
                    clear(p)
    return True


def paragraphs_from(data: dict) -> list[str]:
    parts = data.get("process_details_paragraphs") or []
    clean = [str(x).strip() for x in parts if str(x).strip()]
    if clean:
        return clean
    return [
        "La ejecucion inicia cuando el usuario o el programador externo lanza CSVScriptDetallado. En ese momento el proceso identifica su ruta real de ejecucion, prepara los directorios operativos, valida la existencia del log principal y asegura que la base DuckDB local pueda abrirse correctamente antes de avanzar.",
        "Posteriormente se cargan las variables de entorno y la configuracion funcional desde los archivos correspondientes. Con esa informacion el proceso construye el contexto de autenticacion para QueryTool, lee los filtros opcionales de distrito, plaza y tienda y deja listo el entorno para consultar Varicent ICM sin depender de intervencion manual adicional.",
    ]


def list_from(data: dict, key: str, defaults: list[str]) -> list[str]:
    items = data.get(key) or []
    clean = [str(x).strip() for x in items if str(x).strip()]
    return clean if clean else defaults


def fill_placeholder_sequence(root: ET.Element, placeholders: list[str], items: list[str]) -> None:
    clean_items = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not clean_items:
        return

    paragraphs = []
    for p in root.findall(".//w:p", NS):
        txt = text_of(p).strip()
        if txt in placeholders:
            paragraphs.append((txt, p))

    if not paragraphs:
        return

    for idx, placeholder in enumerate(placeholders):
        target = None
        for txt, paragraph in paragraphs:
            if txt == placeholder:
                target = paragraph
                break
        if target is None:
            continue

        if idx < len(clean_items):
            if idx == len(placeholders) - 1 and len(clean_items) > len(placeholders):
                value = "; ".join(clean_items[idx:])
            else:
                value = clean_items[idx]
        else:
            value = ""
        set_text(target, value)


def flow_lines() -> list[str]:
    return [
        "1. Inicio de ejecucion",
        "2. Lectura de .env, INI y preparacion de DuckDB",
        "3. Construccion de filtros de distrito, plaza y tienda",
        "4. Ejecucion de consulta remota en QueryTool de Varicent",
        "5. Descarga de CatCalculation.csv",
        "6. Carga y deduplicacion de CatCalculation en DuckDB",
        "7. Creacion de CSVDetalladoProceso y CSVDetalladoRetroactivo",
        "8. Exportacion del CSV final CSVparaPDFDetallado.csv",
        "9. Envio de correo de exito o error",
        "10. Limpieza de CSV temporales",
    ]


def replace_paragraphs(root: ET.Element, data: dict) -> None:
    replacements = {
        "[Cliente]": data.get("client", "No informado"),
        "[Nombre de Proceso]": data.get("process_name", "No informado"),
        "[Mes y Año]": data.get("month_year", "No informado"),
        "[Objetivo]": data.get("objective", "No informado"),
        "[Descripción de la situación actual de la solución a desarrollar]": data.get("current_situation", "No informado"),
        "[Descripción sobre la situación esperada y las mejoras con la solución a desarrollar]": data.get("expected_situation", "No informado"),
        "[Descripción sobre el alcance completo del proyecto, con el fin de plasmar lo que la solución podrá y no podrá realizar]": data.get("scope", "No informado"),
        "PDD | [Nombre de Proceso]": f"PDD | {data.get('process_name', 'No informado')}",
        "Fecha: 20/09/2024": f"Fecha: {datetime.now().strftime('%d/%m/%Y')}",
        "Versión: 1": f"Versión: {data.get('version', '1.0')}",
        "Documento: PRV_TPL_PropuestaAnexoTGV.doc": f"Documento: {data.get('process_name', 'PDD_CSVScriptDetallado')}.docx",
    }
    paras = paragraphs_from(data)
    for p in root.findall(".//w:p", NS):
        txt = text_of(p)
        if txt == "[Diagrama]":
            set_lines(p, flow_lines())
        elif txt in ("[Captura de pantalla 1][Narrativa]", "[Captura de Pantalla N][Narrativa]", "[Narrativa]"):
            set_text(p, "\n\n".join(paras) if txt != "[Captura de Pantalla N][Narrativa]" else "")
        else:
            new = txt
            for k, v in replacements.items():
                if k in new:
                    new = new.replace(k, v)
            if new != txt:
                set_text(p, new)

    fill_placeholder_sequence(
        root,
        ["Restricción 1:", "Restricción 2:", "Restricción N:"],
        list_from(
            data,
            "operational_restrictions",
            [
                "El archivo .env debe contener API_KEY y model codificados en base64.",
                "El archivo ConfigScriptCSVDetallado.ini debe existir y contener al menos ToEmail.",
                "La API de Varicent debe estar disponible para descarga y notificacion.",
                "La tabla DateStringPeriods debe contener al menos un periodo marcado como IsOutputInterface = SI.",
            ],
        ),
    )
    fill_placeholder_sequence(
        root,
        ["Punto 1:", "Punto 2:", "Punto N:"],
        list_from(
            data,
            "out_of_scope",
            [
                "La maquetacion o generacion de PDF final.",
                "La correccion manual de informacion origen dentro de Varicent.",
                "La conciliacion manual de diferencias entre el CSV y la fuente remota.",
                "La creacion de nuevos canales de notificacion o formatos de salida no definidos en la configuracion.",
            ],
        ),
    )
    fill_placeholder_sequence(
        root,
        ["Excepción 1:", "Excepción 2:"],
        list_from(
            data,
            "business_exceptions",
            [
                "El periodo consultado no devuelve registros y el CSV final se genera vacio.",
                "El filtro de distritos, plazas o tiendas reduce la salida a cero filas.",
                "La fuente remota trae movimientos fuera del periodo operativo y el corte no coincide con otros reportes.",
                "La estructura funcional del modelo cambia y obliga a revisar la validez de CatCalculation.",
            ],
        ),
    )
    fill_placeholder_sequence(
        root,
        ["Ejemplo 1: Sistemas caídos", "Ejemplo 2: Conexión lenta", "Ejemplo N: Sin conexión a internet"],
        list_from(
            data,
            "system_exceptions",
            [
                "Falta API_KEY o model en .env.",
                "La API de Varicent responde con error HTTP, timeout o credenciales invalidas.",
                "DuckDB no puede abrir la base local o excede el limite de memoria configurado.",
                "La descarga remota genera un CSV parcial o truncado antes de finalizar la transferencia.",
            ],
        ),
    )
    fill_placeholder_sequence(
        root,
        ["Problema 1:", "Problema 2:", "Problema N:"],
        list_from(
            data,
            "dependencies_assumptions",
            [
                "La operacion depende de la disponibilidad de Varicent ICM y de sus APIs.",
                "El archivo de salida debe conservar el nombre y la ruta acordados con PDFScriptDetallado.",
                "Se asume que no hay ejecuciones concurrentes sobre la misma base DuckDB ni sobre el mismo directorio de temporales.",
                "Se asume que la estructura del resultado remoto mantiene la columna COMISIONID y los campos utilizados para deduplicacion y exportacion.",
            ],
        ),
    )
    fill_placeholder_sequence(
        root,
        ["Ejemplo: Archivo Excel + Nombre de archivo"],
        [data.get("deliverable", "Archivo CSV final generado.")],
    )


def replace_tables(root: ET.Element, data: dict) -> None:
    tables = root.findall(".//w:tbl", NS)
    systems = [[s.get("name", ""), s.get("description", ""), s.get("url", "")] for s in data.get("systems", [])]
    business_areas = [[a, "Participa en la validacion y consumo del resultado del proceso."] for a in data.get("business_areas", [])]
    if not systems:
        systems = [["No informado", "No informado", "No informado"]]
    if not business_areas:
        business_areas = [["No informado", "No informado"]]
    for t in tables:
        if fill_table(t, ["Fecha", "Versión", "Descripción", "Autor"], [[datetime.now().strftime("%d-%m-%Y"), data.get("version", "1.0"), "Confeccion de PDD formal para CSVScriptDetallado", data.get("author", "No informado")]]):
            continue
        if fill_table(t, ["Nombre", "Rol", "Área"], [[data.get("author", "No informado"), "Documentacion", "TI / Automatizacion"]]):
            continue
        if fill_table(t, ["Titulo", "Autor", "Fecha", "Archivo"], [["PDD_CSVScriptDetallado", data.get("author", "No informado"), data.get("month_year", "No informado"), "PDD_CSVScriptDetallado.docx"]]):
            continue
        if fill_table(t, ["Ejecuciones por mes", ""], [[data.get("runs_per_month", "No informado")], [data.get("manual_time", "No informado")], [data.get("frequency", "Mensual o bajo demanda operativa")]]):
            continue
        if fill_table(t, ["Nombre", "Descripción", "URL"], systems):
            continue
        if fill_table(t, ["Área de negocio", "Impacto"], business_areas):
            continue


def transform(content: bytes, data: dict) -> bytes:
    root = ET.fromstring(content)
    replace_paragraphs(root, data)
    replace_tables(root, data)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("Usage: generate_pdd_word.py <template.docx> <input.json> <output.docx>")
        return 1
    template = Path(argv[1])
    input_json = Path(argv[2])
    output = Path(argv[3])
    data = load_json(input_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(template, "r") as zin, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            content = zin.read(item.filename)
            if item.filename in {"word/document.xml", "word/header1.xml", "word/header2.xml", "word/header3.xml", "word/footer1.xml", "word/footer2.xml"} and content.lstrip().startswith((b"<?xml", b"<")):
                try:
                    content = transform(content, data)
                except Exception:
                    pass
            zout.writestr(item, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
