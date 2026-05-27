import sys
import os
import base64
import logging
import configparser


# Detecta la carpeta real de ejecuciÃ³n para soportar corrida local y empaquetada.
def get_runtime_base_dir():
    entry_path = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else os.path.realpath(__file__)
    if os.path.isfile(entry_path):
        return os.path.dirname(entry_path)
    return entry_path


def migrate_legacy_directory(base_dir, old_name, new_name):
    old_path = os.path.join(base_dir, old_name)
    new_path = os.path.join(base_dir, new_name)
    if os.path.isdir(old_path) and not os.path.exists(new_path):
        os.rename(old_path, new_path)
    return new_path

# Rutas base del proyecto y de la ejecuciÃ³n actual.
# SCRIPTS_ROOT: carpeta root/scripts.
# PROJECT_ROOT: carpeta raÃ­z del proyecto.
# RUNTIME_BASE_DIR: carpeta desde donde realmente corre el proceso.
SCRIPTS_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PROJECT_ROOT = os.path.dirname(SCRIPTS_ROOT)
RUNTIME_BASE_DIR = get_runtime_base_dir()

# Rutas propias del proceso de configuracion.
# PROCESS_DIR: carpeta operativa central del proceso dentro de root/scripts/Settings.
# FUNCTIONS_DIR: carpeta con common.py y utilitarios compartidos.
# DATA_DIR: carpeta central de datos/exportables del proyecto.
PROCESS_DIR = migrate_legacy_directory(SCRIPTS_ROOT, 'PDFFiniquito', 'Settings')
FUNCTIONS_DIR = os.path.join(SCRIPTS_ROOT, 'functions')
DATA_DIR = os.path.join(PROJECT_ROOT, 'Data')
LOGS_DIR = os.path.join(SCRIPTS_ROOT, 'Logs')

# ConfiguraciÃ³n propia del proceso y ruta del archivo log principal.
PROCESS_CONFIG_PATH = os.path.join(PROCESS_DIR, 'ConfigCSVScriptFormal.ini')
PROCESS_LOG_PATH = os.path.join(LOGS_DIR, 'CSVScriptFormal.log')

# Mapa entre los marcadores usados en la consulta remota y las claves del .ini.
# REQUIRED_QUERY_PARAM_MAP: parÃ¡metros sin los cuales la consulta no debe ejecutarse.
# OPTIONAL_QUERY_PARAM_MAP: filtros opcionales que pueden omitirse.
REQUIRED_QUERY_PARAM_MAP = {}
OPTIONAL_QUERY_PARAM_MAP = {
    "@Distritos": "distritos_parametro",
    "@Plazas": "plazas_parametro",
    "@Tiendas": "tiendas_parametro",
}

# Ruta de la base DuckDB local donde se cargan y transforman los datos del proceso.
db_path = os.path.join(PROCESS_DIR, 'localbd_CSVScriptFormal_v2.duckdb')


#===================================== NUBE ICM / RUTAS =====================================    

def agregarpath(libreria, required=False):
    # Inserta librerÃ­as externas o zips en sys.path siguiendo el estÃ¡ndar ICM.
    ruta_libreria = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), libreria)

    if not os.path.exists(ruta_libreria):
        if required:
            print(f"Error: La ruta {ruta_libreria} no existe.")
        return

    if ruta_libreria.endswith('.zip'):
        if os.path.isfile(ruta_libreria):
            sys.path.insert(0, ruta_libreria)
            print(f"El archivo .zip {ruta_libreria} ha sido agregado a sys.path.")
        else:
            print(f"Error: {ruta_libreria} no es un archivo vÃ¡lido.")
        return

    if os.path.isdir(ruta_libreria):
        sys.path.insert(0, ruta_libreria)
        print(f"La ruta {ruta_libreria} ha sido agregada a sys.path.")
        return

    print(f"Error: La ruta {ruta_libreria} no es un directorio vÃ¡lido ni un archivo .zip.")


agregarpath("Library")

# Inserta la carpeta de funciones compartidas para poder importar common.py.
sys.path.insert(0, FUNCTIONS_DIR)

import duckdb
import requests
from common import insert_csv_into_table
from common import export_table_to_csv
from common import fetch_and_convert_to_csv
from common import borrar_csv_en_directorio
from dotenv import load_dotenv

dotenv_path = os.path.join(SCRIPTS_ROOT, '.env')
# Archivo .env compartido que contiene credenciales y variables de entorno globales.
load_dotenv(dotenv_path)


def ensure_process_dir_structure():
    # Garantiza que existan carpetas, log y base local antes de ejecutar el proceso.
    os.makedirs(PROCESS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)

    if not os.path.exists(PROCESS_LOG_PATH):
        with open(PROCESS_LOG_PATH, "a", encoding="utf-8"):
            pass

    if not os.path.exists(db_path):
        conn = duckdb.connect(db_path, read_only=False)
        conn.close()


ensure_process_dir_structure()

if not os.path.exists(PROCESS_CONFIG_PATH):
    raise FileNotFoundError(
        "No se encontrÃ³ el archivo de configuraciÃ³n del proceso: "
        f"{PROCESS_CONFIG_PATH}"
    )

# Objeto de configuraciÃ³n que centraliza los parÃ¡metros funcionales del proceso.
config = configparser.ConfigParser()
config.read(PROCESS_CONFIG_PATH)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(PROCESS_LOG_PATH, mode='a'),
        logging.StreamHandler(sys.stdout)
    ]
)


class LoggerWriter:
    # Redirige print y stdout hacia el registro para unificar consola y archivo de log.
    def __init__(self, level):
        self.level = level

    def write(self, message):
        message = message.strip()
        if message:
            self.level(message)

    def flush(self):
        pass


sys.stdout = LoggerWriter(logging.info)
sys.stderr = LoggerWriter(logging.error)


#===================================== BASE DUCKDB =====================================    

def get_duckdb_connection(db_path):
    # Abre DuckDB aplicando el lÃ­mite de memoria definido en la configuraciÃ³n.
    conn = duckdb.connect(db_path, read_only=False)
    memory_limit = config['DEFAULT']['duckdblimit']
    conn.execute(f"SET memory_limit = '{memory_limit}';")
    return conn


def resolve_export_csv_path():
    # Resuelve la ruta final del CSV exportado a partir de la configuraciÃ³n.
    configured_path = config["DEFAULT"].get("output_csv_path", "").strip()
    if configured_path:
        normalized = os.path.expandvars(os.path.expanduser(configured_path))
        if os.path.isabs(normalized):
            return normalized
        return os.path.normpath(os.path.join(PROJECT_ROOT, normalized))
    return os.path.join(DATA_DIR, 'CSVparaPDFFormal.csv')


def require_env_var(name):
    # Obliga a que las credenciales y la configuraciÃ³n global vivan en .env.
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Falta la variable de entorno requerida: {name}")
    return value


def validate_query_parameters(query_string):
    # Verifica que la consulta incrustada tenga todos los parÃ¡metros requeridos en la configuraciÃ³n.
    missing = []
    defaults = config["DEFAULT"]
    for placeholder, config_key in REQUIRED_QUERY_PARAM_MAP.items():
        if placeholder in query_string and not defaults.get(config_key, "").strip():
            missing.append(f"{placeholder} -> {config_key}")

    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Faltan parametros para la query remota. "
            f"Completa estos valores en ConfigCSVScriptFormal.ini: {joined}"
        )

    optional_missing = []
    for placeholder, config_key in OPTIONAL_QUERY_PARAM_MAP.items():
        if placeholder in query_string and not defaults.get(config_key, "").strip():
            optional_missing.append(f"{placeholder} -> {config_key}")

    if optional_missing:
        joined = ", ".join(optional_missing)
        print(
            "Advertencia: no se definieron algunos parametros opcionales en ConfigCSVScriptFormal.ini: "
            f"{joined}. Se asume que ICM los resolvera por contexto."
        )


def ensure_required_csvs(base_path, required_files):
    # Falla temprano si faltan archivos CSV necesarios para cargar DuckDB.
    missing_files = []
    for file_name in required_files:
        csv_file = os.path.join(base_path, file_name)
        if not os.path.exists(csv_file):
            missing_files.append(csv_file)

    if missing_files:
        sample = ", ".join(missing_files[:5])
        extra = "" if len(missing_files) <= 5 else f" y {len(missing_files) - 5} mas"
        raise RuntimeError(
            "Faltan archivos CSV requeridos antes de ejecutar DuckDB: "
            f"{sample}{extra}"
        )


def deduplicate_table_by_max_comisionid(conn, table_name):
    # Elimina duplicados funcionales conservando el mayor COMISIONID por grupo.
    allowed_tables = {"CatCalculation", "CatCalculoProceso", "CatCalculoRetroactivo"}
    if table_name not in allowed_tables:
        raise RuntimeError(f"No se permite deduplicar la tabla: {table_name}")

    conn.execute(
        f'''
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT * EXCLUDE (_rn)
        FROM (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        "plaza",
                        "tienda",
                        "CRDISTRITO",
                        "FECHAINICIAL",
                        "FECHAFINAL",
                        "mes",
                        "mesesp",
                        "RFC",
                        "NOMBRECOMISIONISTA",
                        "CLASIFICACION"
                    ORDER BY TRY_CAST("COMISIONID" AS DOUBLE) DESC NULLS LAST
                ) AS _rn
            FROM {table_name}
        ) t
        WHERE _rn = 1;
        '''
    )


#===================================== CORREO =====================================    

def get_configured_email_list(key, required=True):
    # Obtiene una lista de correos desde el .ini.
    # ToEmail es obligatorio; CC puede venir vacio.
    raw_value = config["DEFAULT"].get(key, "").strip()
    if not raw_value:
        if required:
            raise RuntimeError(
                f"Falta configurar '{key}' en ConfigCSVScriptFormal.ini."
            )
        return []
    return [email.strip() for email in raw_value.split(",") if email.strip()]


def send_mail(
        subject,
        body,
        email_id,
        cc,
        model=base64.b64decode(os.getenv("model")).decode("utf-8"),
        api_url="https://api.cloud.varicent.com/api/v1/admin/tsapi/sendMail",
        auth_token=base64.b64decode(os.getenv("API_KEY")).decode("utf-8")):
    # EnvÃ­a notificaciones de Ã©xito o error usando las credenciales del entorno.
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "model": model
    }
    
    payload = {
        "to": email_id,
        "cc": cc,
        "subject": subject,
        "body": f"""
        <html>
          <body>
            <div class="container">
              {body}
              <p>Gracias por su atención.</p>
              <p>Correo automático. Favor de no responder este correo.</p>
            </div>
          </body>
        </html>
        """,
        "useHtml": True,
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"Correo #{email_id} enviado con Ã©xito.")
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        print(f"Error HTTP en correo #{email_id}: {http_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"Error en la solicitud del correo #{email_id}: {req_err}")


#===================================== LOGICA PRINCIPAL =====================================

def main(db_path: str):
    # Orquesta la ejecuciÃ³n principal y envÃ­a notificaciones de resultado.
    CONN = None
    try:
        CONN = get_duckdb_connection(db_path)
        Query(CONN)

        to_emails = get_configured_email_list("ToEmail")
        cc = get_configured_email_list("CC", required=False)
        bodyA = '''
            Se ha concluido el proceso de CSVFiniquitoFormal.
            Los resultados se encuentran en la tabla 
            <strong> CSVFiniquitoFormal </strong> 
            en el modelo de ICM FEMCO.
        '''
        subjectA = 'Se realizó correctamente el proceso de CSVFiniquitoFormal - Favor de validar los resultados'
        send_mail(
            subject=subjectA,
            body=bodyA,
            email_id=to_emails,
            cc=cc
        )
    except Exception as e:
        to_emails = get_configured_email_list("ToEmail")
        cc = get_configured_email_list("CC", required=False)
        send_mail(
            subject='Error al ejecutar proceso de CSVFiniquitoFormal',
            body=f'''
            Se han presentado inconvenientes al ejecutar el proceso de <strong> CSVFiniquitoFormal</strong> en modelo de ICM FEMCO 
            <br><br>Error: <strong>  {e}  </strong> 
            <ul>
                <li>Favor de revisar el query de lectura a ICM o el INSERT a la tabla de FEMCO.</li> 
                <li>Por favor contacte al equipo de TI ICM Xpertal para su seguimiento correspondiente</li> 
            </ul>
        ''',
            email_id=to_emails,
            cc=cc
        )
        raise
    finally:
        if CONN is not None:
            CONN.close()


def Query(CONN):
    # Ejecuta la transformaciÃ³n en DuckDB usando CatCalculation como tabla base del proceso.
    existing_tables = {
        row[0]
        for row in CONN.execute("SHOW TABLES").fetchall()
    }
    if "CatCalculation" not in existing_tables:
        raise RuntimeError("No existe la tabla CatCalculation para generar CatCalculoProceso.")

    output_csv_path = resolve_export_csv_path()
    print(
        f"Se generarÃ¡ {output_csv_path} directamente desde CatCalculation."
    )

    CONN.execute("DROP TABLE IF EXISTS CatCalculoProceso;")
    CONN.execute(
        '''
        CREATE TABLE CatCalculoProceso AS
        SELECT *
        FROM CatCalculation;
        '''
    )

    CONN.execute("DROP TABLE IF EXISTS CatCalculoRetroactivo;")
    CONN.execute(
        '''
        CREATE TABLE CatCalculoRetroactivo AS
        SELECT *
        FROM CatCalculation
        WHERE 1 = 0;
        '''
    )

    count_CatCalculoProceso = CONN.execute(
        "SELECT COUNT(*) FROM CatCalculoProceso"
    ).fetchall()[0][0]
    print(f"Filas preparadas para {output_csv_path}: {count_CatCalculoProceso}")


#===================================== ORQUESTACIÃ“N DEL PROCESO =====================================

def sql_quote(value):
    # Escapa valores simples para interpolarlos en la consulta remota.
    return "'" + value.replace("'", "''") + "'"


def get_api_runtime_context():
    # Construye la URL y los encabezados para consumir QueryTool en Varicent.
    api_url = config["DEFAULT"].get("api_url_querytool", "https://api.cloud.varicent.com/api/v1/rpc/querytool/export")
    token = base64.b64decode(require_env_var("API_KEY")).decode("utf-8")
    model = base64.b64decode(require_env_var("model")).decode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Model": model,
    }
    return api_url, headers


def build_runtime_filters():
    # Convierte la configuraciÃ³n de filtros en fragmentos SQL listos para usar.
    distritos_parametro = config["DEFAULT"].get("distritos_parametro", "").strip()
    plazas_parametro = config["DEFAULT"].get("plazas_parametro", "").strip()
    tiendas_parametro = config["DEFAULT"].get("tiendas_parametro", "").strip()

    if distritos_parametro:
        distritos_sql = ", ".join(
            sql_quote(value.strip())
            for value in distritos_parametro.split(",")
            if value.strip()
        )
        filtro_distritos_sql = f"('DIS-' || S.\"CRDISTRITO\") IN ({distritos_sql})"
    else:
        filtro_distritos_sql = "1 = 1"

    if plazas_parametro:
        plazas_sql = plazas_parametro.replace("'", "''")
        filtro_plazas_sql = f"('PLA-' || S.\"CRPLAZA\") LIKE '%{plazas_sql}%'"
    else:
        filtro_plazas_sql = "1 = 1"

    if tiendas_parametro:
        tiendas_sql = ", ".join(
            sql_quote(value.strip())
            for value in tiendas_parametro.split(",")
            if value.strip()
        )
        filtro_tiendas_sql = f'S."CRTIENDA" IN ({tiendas_sql})'
    else:
        filtro_tiendas_sql = "1 = 1"

    return {
        "filtro_distritos_sql": filtro_distritos_sql,
        "filtro_plazas_sql": filtro_plazas_sql,
        "filtro_tiendas_sql": filtro_tiendas_sql,
    }


def download_remote_queries(api_url, headers, queries, base_path):
    # Descarga cada consulta remota y la materializa como CSV dentro de la carpeta del proceso.
    for query in queries:
        validate_query_parameters(query["queryString"])
        payload = {
            "queryString": query["queryString"],
            "offset": 0,
            "limit": 0,
            "exportFileFormat": "Text"
        }
        CONN = get_duckdb_connection(db_path)
        try:
            output_csv = os.path.join(base_path, query["output"])
            fetch_and_convert_to_csv(api_url, headers, payload, output_csv, CONN, 1)
        finally:
            CONN.close()


def load_source_csvs_into_duckdb(base_path, csv_table_map):
    # Inserta los CSV descargados en DuckDB y aplica deduplicaciÃ³n a CatCalculation.
    load_source_csvs_into_duckdb(base_path, csv_table_map)


def export_main_output():
    # Exporta la tabla final del proceso al CSV de salida configurado.
    CONN = get_duckdb_connection(db_path)
    try:
        table_name = 'CatCalculoProceso'
        deduplicate_table_by_max_comisionid(CONN, table_name)
        output_file = resolve_export_csv_path()
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        export_table_to_csv(CONN, table_name, output_file)
        print(f"CSV generado en: {output_file}")
    finally:
        CONN.close()


def cleanup_intermediate_csvs():
    # Limpia los CSV temporales generados durante la ejecuciÃ³n.
    borrar_csv_en_directorio(PROCESS_DIR)


def run_process():
    # Punto de entrada operativo: descarga, carga, procesa, exporta y limpia.
    # api_url y headers: contexto de autenticaciÃ³n hacia QueryTool.
    # base_path: carpeta temporal/operativa donde se guardan los CSV del proceso.
    # filters: fragmentos SQL construidos a partir del .ini.
    api_url, headers = get_api_runtime_context()
    base_path = PROCESS_DIR
    filters = build_runtime_filters()

    #===================================== Descarga de datos desde ICM a DuckDB =====================================
    try:
        queries = [
            {
                "queryString": f"""
                    SELECT
                        *
                    FROM (
                        WITH periodo AS (
                            SELECT
                                "StarDate" AS FECHAINI,
                                "EndDate" AS FECHAFIN,
                                UPPER("PeriodName") AS MES,
                                CASE EXTRACT(MONTH FROM "EndDate")
                                    WHEN 1 THEN 'enero'
                                    WHEN 2 THEN 'febrero'
                                    WHEN 3 THEN 'marzo'
                                    WHEN 4 THEN 'abril'
                                    WHEN 5 THEN 'mayo'
                                    WHEN 6 THEN 'junio'
                                    WHEN 7 THEN 'julio'
                                    WHEN 8 THEN 'agosto'
                                    WHEN 9 THEN 'septiembre'
                                    WHEN 10 THEN 'octubre'
                                    WHEN 11 THEN 'noviembre'
                                        WHEN 12 THEN 'diciembre'
                                END AS MesEsp
                            FROM "DateStringPeriods"
                            WHERE "IsOutputInterface" = 'SI'
                            ORDER BY "EndDate" DESC
                            LIMIT 1
                        ),
                        incentivo_venta_cat AS (
                            SELECT
                                C."COMISIONID",
                                SUM(CAST(NULLIF(CAST(C."IMPORTE" AS VARCHAR), '') AS DOUBLE PRECISION)) AS INCENTIVOVTACAT
                            FROM "dtResumenCompensaciones" C
                            CROSS JOIN periodo
                            WHERE C."FECHAINI" >= periodo.FECHAINI
                              AND C."FECHAFIN" <= periodo.FECHAFIN
                              AND C."COMPONENTEID" IN (
                                  'DULCES PAGO MENSUAL',
                                  'FAST FOOD ALIMENTOS PAGO MENSUAL',
                                  'FAST FOOD BEBIDAS PAGO MENSUAL',
                                  'CERVEZA PAGO MENSUAL',
                                  'BOTANAS PAGO MENSUAL',
                                  'REFRESCOS PAGO MENSUAL',
                                  'CIGARROS PAGO MENSUAL'
                              )
                            GROUP BY C."COMISIONID"
                        ),
                        dev_aguinaldo AS (
                            SELECT
                                R."COMISIONID",
                                MAX(CAST(NULLIF(CAST(R."ANT" AS VARCHAR), '') AS DOUBLE PRECISION)) AS DEV_AGUINALDO_ANTERIOR,
                                MAX(CAST(NULLIF(CAST(R."MES" AS VARCHAR), '') AS DOUBLE PRECISION)) AS DEV_AGUINALDO_MENSUAL,
                                MAX(CAST(NULLIF(CAST(R."ACUM" AS VARCHAR), '') AS DOUBLE PRECISION)) AS DEV_AGUINALDO_ACUMULADA,
                                MAX(CAST(NULLIF(CAST(R."RECUP" AS VARCHAR), '') AS DOUBLE PRECISION)) AS DEV_AGUINALDO_RECUP,
                                MAX(CAST(NULLIF(CAST(R."SALDO" AS VARCHAR), '') AS DOUBLE PRECISION)) AS DEV_AGUINALDO_SALDO
                            FROM "dtResumenCompensaciones" R
                            CROSS JOIN periodo
                            WHERE R."FECHAINI" >= periodo.FECHAINI
                              AND R."FECHAFIN" <= periodo.FECHAFIN
                              AND R."COMPONENTEID" = 'DEVOLUCION RESERVA AGUINALDO SALDO MENSUAL'
                            GROUP BY R."COMISIONID"
                        ),
                        asignacion_centro_trabajo AS (
                            SELECT
                                A."CentroTrabajoID",
                                P."RFC",
                                CASE
                                    WHEN A."FechaInicio" < periodo.FECHAINI THEN periodo.FECHAINI
                                    ELSE A."FechaInicio"
                                END AS "FechaInicio",
                                CASE
                                    WHEN A."FechaFin" = DATE '9998-12-31' THEN periodo.FECHAFIN
                                    ELSE A."FechaFin"
                                END AS "FechaFin"
                            FROM "sptAsignacionCentroTrabajo" A
                            JOIN "Payee_" P
                              ON A."EmpleadoID" = P."PayeeID_"
                            CROSS JOIN periodo
                            WHERE A."FechaInicio" <= periodo.FECHAFIN
                              AND A."FechaFin" >= periodo.FECHAINI
                        )
                        SELECT
                            S."COMISIONID",
                            'PLA-' || S."CRPLAZA" AS PLAZA,
                            'TIE-' || S."CRPLAZA" || S."CRTIENDA" || '(' || S."DESCTIENDA" || ')' AS TIENDA,
                            S."CRDISTRITO",
                            S."FECHAINICIAL",
                            S."FECHAFINAL",
                            periodo.MES AS MES,
                            periodo.MesEsp AS MesEsp,
                            S."RFC",
                            S."NOMBRECOMISIONISTA",
                            CAST(NULLIF(CAST(S."AGUINALDOEMPLEADOSPAGO" AS VARCHAR), '') AS DOUBLE PRECISION) AS AGUINALDOEMPLEADOSPAGO,
                            S."CLASIFICACION",
                            CAST(NULLIF(CAST(S."COMISIONEXTRAORDINARIA" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONEXTRAORDINARIA,
                            CAST(NULLIF(CAST(S."COMISIONEXTRAORDINARIAPC" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONEXTRAORDINARIAPC,
                            CAST(NULLIF(CAST(S."COMISIONFIJA" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONFIJA,
                            CAST(NULLIF(CAST(S."COMISIONVARIABLE" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONVARIABLE,
                            CAST(NULLIF(CAST(S."COMISIONVENTATABULADOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONVENTATABULADOR,
                            CAST(NULLIF(CAST(S."COMISIONPORESTRUCTURA" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONPORESTRUCTURA,
                            CAST(NULLIF(CAST(S."COMISIONEXTRAORDINARIACA" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONEXTRAORDINARIACA,
                            CAST(NULLIF(CAST(S."VENTATAE" AS VARCHAR), '') AS DOUBLE PRECISION) AS VENTATAE,
                            CAST(NULLIF(CAST(S."INCENTIVOVENTADULCES" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOVENTADULCES,
                            CAST(NULLIF(CAST(S."INCENTIVOVENTAFASTFOOD" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOVENTAFASTFOOD,
                            CAST(NULLIF(CAST(S."INCENTIVOVENTABEBIDAS" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOVENTABEBIDAS,
                            CAST(NULLIF(CAST(S."ESTRUCTURAOPTIMA" AS VARCHAR), '') AS DOUBLE PRECISION) AS ESTRUCTURAOPTIMA,
                            CAST(NULLIF(CAST(S."ESTRUCTURAREALENTIENDA" AS VARCHAR), '') AS DOUBLE PRECISION) AS ESTRUCTURAREALENTIENDA,
                            CAST(NULLIF(CAST(S."IMSSYSAREMPLEADOS" AS VARCHAR), '') AS DOUBLE PRECISION) AS IMSSYSAREMPLEADOS,
                            CAST(NULLIF(CAST(S."INCENTIVOAPERTURASEGCAJA" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOAPERTURASEGCAJA,
                            CAST(NULLIF(CAST(S."COMISIONVENTATAE" AS VARCHAR), '') AS DOUBLE PRECISION) AS COMISIONVENTATAE,
                            CAST(NULLIF(CAST(S."PORCENTAJESEGUNDACAJA" AS VARCHAR), '') AS DOUBLE PRECISION) AS PORCENTAJESEGUNDACAJA,
                            CAST(NULLIF(CAST(S."INCENTIVOBASEEJECUCION" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOBASEEJECUCION,
                            CAST(NULLIF(CAST(S."INCENTIVOBASESOBREMERMA" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOBASESOBREMERMA,
                            CAST(NULLIF(CAST(S."INCENTIVOEXTRAORDINARIO" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOEXTRAORDINARIO,
                            CAST(NULLIF(CAST(S."INCENTIVOPOREJECUCION" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOPOREJECUCION,
                            CAST(NULLIF(CAST(S."INCENTIVORESULTADOSLIDER" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVORESULTADOSLIDER,
                            CAST(NULLIF(CAST(S."INCENTIVORESULTADOSEMP" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVORESULTADOSEMP,
                            CAST(NULLIF(CAST(S."INCENTIVOSOBREMERMA" AS VARCHAR), '') AS DOUBLE PRECISION) AS INCENTIVOSOBREMERMA,
                            CAST(NULLIF(CAST(S."PORCENTAJEMERMA" AS VARCHAR), '') AS DOUBLE PRECISION) AS PORCENTAJEMERMA,
                            CAST(NULLIF(CAST(S."INFONAVITEMPLEADOS" AS VARCHAR), '') AS DOUBLE PRECISION) AS INFONAVITEMPLEADOS,
                            CAST(NULLIF(CAST(S."INGRESONETOOPERATIVO" AS VARCHAR), '') AS DOUBLE PRECISION) AS INGRESONETOOPERATIVO,
                            CAST(NULLIF(CAST(S."ISNEMPLEADOS" AS VARCHAR), '') AS DOUBLE PRECISION) AS ISNEMPLEADOS,
                            CAST(NULLIF(CAST(S."IVASUBTOTALCOMISION" AS VARCHAR), '') AS DOUBLE PRECISION) AS IVASUBTOTALCOMISION,
                            CAST(NULLIF(CAST(S."MULTIPLOMERMA" AS VARCHAR), '') AS DOUBLE PRECISION) AS MULTIPLOMERMA,
                            CAST(NULLIF(CAST(S."MULTIPLOPOREJECUCION" AS VARCHAR), '') AS DOUBLE PRECISION) AS MULTIPLOPOREJECUCION,
                            CAST(NULLIF(CAST(S."PRIMAVACACIONALEMPLEADOS" AS VARCHAR), '') AS DOUBLE PRECISION) AS PRIMAVACACIONALEMPLEADOS,
                            CAST(NULLIF(CAST(S."RETENCIONDOSTERCIOSIVA" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENCIONDOSTERCIOSIVA,
                            CAST(NULLIF(CAST(S."SUBTOTALCOMISON" AS VARCHAR), '') AS DOUBLE PRECISION) AS SUBTOTALCOMISON,
                            CAST(NULLIF(CAST(S."SUELDOEMPLEADOS" AS VARCHAR), '') AS DOUBLE PRECISION) AS SUELDOEMPLEADOS,
                            CAST(NULLIF(CAST(S."TOTALCOMISION" AS VARCHAR), '') AS DOUBLE PRECISION) AS TOTALCOMISION,
                            CAST(NULLIF(CAST(S."TRAFICO" AS VARCHAR), '') AS DOUBLE PRECISION) AS TRAFICO,
                            CAST(NULLIF(CAST(S."VENTATABULADORPORLIDER" AS VARCHAR), '') AS DOUBLE PRECISION) AS VENTATABULADORPORLIDER,
                            S."VENTATOTAL",
                            CAST(NULLIF(CAST(S."MERMAIEPS_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIEPS_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMAIMPESTATAL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIMPESTATAL_ANTERIOR,
                            (
                                CAST(NULLIF(CAST(S."MERMAIVAIEPS_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) +
                                CAST(NULLIF(CAST(S."MERMAIVA_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION)
                            ) AS MERMAIVA_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMAMCIACERO_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACERO_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMAMCIACONSIG_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACONSIG_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMAMCIAEXENTA_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAEXENTA_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMAMCIAGRAVAD_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAGRAVAD_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMASUBTOTAL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMASUBTOTAL_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMATOTAL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMATOTAL_ANTERIOR,
                            CAST(NULLIF(CAST(S."MERMAIEPS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIEPS_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMAIMPESTATAL_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIMPESTATAL_MENSUAL,
                            (
                                CAST(NULLIF(CAST(S."MERMAIVAIEPS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) +
                                CAST(NULLIF(CAST(S."MERMAIVA_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION)
                            ) AS MERMAIVA_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMAMCIACERO_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACERO_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMAMCIACONSIG_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACONSIG_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMAMCIAEXENTA_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAEXENTA_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMAMCIAGRAVAD_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAGRAVAD_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMASUBTOTAL_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMASUBTOTAL_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMATOTAL_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMATOTAL_MENSUAL,
                            CAST(NULLIF(CAST(S."MERMAIEPS_ACUMULADA" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIEPS_ACUMULADA,
                            CAST(NULLIF(CAST(S."MERMAIMPESTATAL_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIMPESTATAL_ACUMULAD,
                            (
                                CAST(NULLIF(CAST(S."MERMAIVAIEPS_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) +
                                CAST(NULLIF(CAST(S."MERMAIVA_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION)
                            ) AS MERMAIVA_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMAMCIACERO_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACERO_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMAMCIACONSIG_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACONSIG_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMAMCIAEXENTA_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAEXENTA_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMAMCIAGRAVAD_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAGRAVAD_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMASUBTOTAL_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMASUBTOTAL_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMATOTAL_ACUMULAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMATOTAL_ACUMULAD,
                            CAST(NULLIF(CAST(S."MERMAIEPS_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIEPS_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMAIMPESTATAL_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIMPESTATAL_FACTURAD,
                            (
                                CAST(NULLIF(CAST(S."MERMAIVAIEPS_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) +
                                CAST(NULLIF(CAST(S."MERMAIVA_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION)
                            ) AS MERMAIVA_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMAMCIACERO_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACERO_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMAMCIACONSIG_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACONSIG_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMAMCIAEXENTA_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAEXENTA_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMAMCIAGRAVAD_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAGRAVAD_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMASUBTOTAL_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMASUBTOTAL_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMATOTAL_FACTURAD" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMATOTAL_FACTURAD,
                            CAST(NULLIF(CAST(S."MERMAIEPS_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIEPS_NOFAC,
                            CAST(NULLIF(CAST(S."MERMAIMPESTATAL_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIMPESTATAL_NOFAC,
                            (
                                CAST(NULLIF(CAST(S."MERMAIVAIEPS_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) +
                                CAST(NULLIF(CAST(S."MERMAIVA_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION)
                            ) AS MERMAIVA_NOFAC,
                            CAST(NULLIF(CAST(S."MERMAMCIACERO_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACERO_NOFAC,
                            CAST(NULLIF(CAST(S."MERMAMCIACONSIG_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACONSIG_NOFAC,
                            CAST(NULLIF(CAST(S."MERMAMCIAEXENTA_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAEXENTA_NOFAC,
                            CAST(NULLIF(CAST(S."MERMAMCIAGRAVAD_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAGRAVAD_NOFAC,
                            CAST(NULLIF(CAST(S."MERMASUBTOTAL_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMASUBTOTAL_NOFAC,
                            CAST(NULLIF(CAST(S."MERMATOTAL_NOFAC" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMATOTAL_NOFAC,
                            CAST(NULLIF(CAST(S."MERMAIEPS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIEPS_SALDO,
                            CAST(NULLIF(CAST(S."MERMAIMPESTATAL_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAIMPESTATAL_SALDO,
                            (
                                CAST(NULLIF(CAST(S."MERMAIVAIEPS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) +
                                CAST(NULLIF(CAST(S."MERMAIVA_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION)
                            ) AS MERMAIVA_SALDO,
                            CAST(NULLIF(CAST(S."MERMAMCIACERO_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACERO_SALDO,
                            CAST(NULLIF(CAST(S."MERMAMCIACONSIG_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIACONSIG_SALDO,
                            CAST(NULLIF(CAST(S."MERMAMCIAEXENTA_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAEXENTA_SALDO,
                            CAST(NULLIF(CAST(S."MERMAMCIAGRAVAD_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMAMCIAGRAVAD_SALDO,
                            CAST(NULLIF(CAST(S."MERMASUBTOTAL_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMASUBTOTAL_SALDO,
                            CAST(NULLIF(CAST(S."MERMATOTAL_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS MERMATOTAL_SALDO,
                            CAST(NULLIF(CAST(S."ANTICIPOCONT_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOCONT_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOIMPRFAC_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOIMPRFAC_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOLIDERCO_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOLIDERCO_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOPAMULTA_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOPAMULTA_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOPAGOSAL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOPAGOSAL_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOPAGOTEL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOPAGOTEL_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOCAPACIT_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOCAPACIT_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOSPECIAL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOSPECIAL_ANTERIOR,
                            CAST(NULLIF(CAST(S."APORTCAJAAHORRO_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS APORTCAJAAHORRO_ANTERIOR,
                            CAST(NULLIF(CAST(S."DESCUENTOFASTFO_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCUENTOFASTFO_ANTERIOR,
                            CAST(NULLIF(CAST(S."DESCUENTOSEGVOL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCUENTOSEGVOL_ANTERIOR,
                            CAST(NULLIF(CAST(S."DEVRETDIFIVA_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS DEVRETDIFIVA_ANTERIOR,
                            CAST(NULLIF(CAST(S."DIFERENCIADEPOS_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS DIFERENCIADEPOS_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPOSOTROS_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOSOTROS_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTICIPESPTOTAL_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPESPTOTAL_ANTERIOR,
                            CAST(NULLIF(CAST(S."PRESTAMOCAJAAHO_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS PRESTAMOCAJAAHO_ANTERIOR,
                            CAST(NULLIF(CAST(S."RESERAGUINALRED_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERAGUINALRED_ANTERIOR,
                            CAST(NULLIF(CAST(S."RESERCRECIPATRI_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERCRECIPATRI_ANTERIOR,
                            CAST(NULLIF(CAST(S."RESERVACARED_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERVACARED_ANTERIOR,
                            CAST(NULLIF(CAST(S."RETENUNTERCIO_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENUNTERCIO_ANTERIOR,
                            CAST(NULLIF(CAST(S."RETENIMPUESTOS_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPUESTOS_ANTERIOR,
                            CAST(NULLIF(CAST(S."RETENIMPNOMEMP_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPNOMEMP_ANTERIOR,
                            CAST(NULLIF(CAST(S."TOTALANTICIPOS_ANTERIOR" AS VARCHAR), '') AS DOUBLE PRECISION) AS TOTALANTICIPOS_ANTERIOR,
                            CAST(NULLIF(CAST(S."ANTIDESPCONT_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIDESPCONT_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIIMPRFACT_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIIMPRFACT_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTILIDERESCOM_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTILIDERESCOM_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIPAGOMULTAS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOMULTAS_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIPAGOSALARIOS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOSALARIOS_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIPAGOTEL_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOTEL_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTICAPACITACION_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICAPACITACION_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIESPECIAL_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIESPECIAL_MENSUAL,
                            CAST(NULLIF(CAST(S."APORTCAJAAHORRO_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS APORTCAJAAHORRO_MENSUAL,
                            CAST(NULLIF(CAST(S."DESCFASTFOOD_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCFASTFOOD_MENSUAL,
                            CAST(NULLIF(CAST(S."DESCSEGVOLUN_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCSEGVOLUN_MENSUAL,
                            CAST(NULLIF(CAST(S."DEVRETDIFIVA_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS DEVRETDIFIVA_MENSUAL,
                            CAST(NULLIF(CAST(S."DIFERENCIADEPOS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS DIFERENCIADEPOS_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIOTROS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIOTROS_MENSUAL,
                            CAST(NULLIF(CAST(S."PAGOTOTANTIESPE_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS PAGOTOTANTIESPE_MENSUAL,
                            CAST(NULLIF(CAST(S."PRESTCAJAAHORRO_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS PRESTCAJAAHORRO_MENSUAL,
                            CAST(NULLIF(CAST(S."RESERAGUINREDPAT_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERAGUINREDPAT_MENSUAL,
                            CAST(NULLIF(CAST(S."RESERCRECIPATRI_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERCRECIPATRI_MENSUAL,
                            CAST(NULLIF(CAST(S."RESERVACARED_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERVACARED_MENSUAL,
                            CAST(NULLIF(CAST(S."RETENUNTERCIOIVA_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENUNTERCIOIVA_MENSUAL,
                            CAST(NULLIF(CAST(S."RETENIMPUESTOS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPUESTOS_MENSUAL,
                            CAST(NULLIF(CAST(S."RETENIMPNOMEMP_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPNOMEMP_MENSUAL,
                            CAST(NULLIF(CAST(S."TOTALANTICIPOS_MENSUAL" AS VARCHAR), '') AS DOUBLE PRECISION) AS TOTALANTICIPOS_MENSUAL,
                            CAST(NULLIF(CAST(S."ANTIDESPCONT_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIDESPCONT_ACUM,
                            CAST(NULLIF(CAST(S."ANTIIMPRFACT_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIIMPRFACT_ACUM,
                            CAST(NULLIF(CAST(S."ANTILIDERESCOM_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTILIDERESCOM_ACUM,
                            CAST(NULLIF(CAST(S."ANTIPAGOMULTAS_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOMULTAS_ACUM,
                            CAST(NULLIF(CAST(S."ANTIPAGOSALARIOS_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOSALARIOS_ACUM,
                            CAST(NULLIF(CAST(S."ANTIPAGOTEL_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOTEL_ACUM,
                            CAST(NULLIF(CAST(S."ANTICAPACITACION_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICAPACITACION_ACUM,
                            CAST(NULLIF(CAST(S."ANTIESPECIALES_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIESPECIALES_ACUM,
                            CAST(NULLIF(CAST(S."APORTCAJAAHORROS_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS APORTCAJAAHORROS_ACUM,
                            CAST(NULLIF(CAST(S."DESCFASTFOOD_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCFASTFOOD_ACUM,
                            CAST(NULLIF(CAST(S."DESCSEGVOLUN_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCSEGVOLUN_ACUM,
                            CAST(NULLIF(CAST(S."DEVRETENDIFIVA_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS DEVRETENDIFIVA_ACUM,
                            CAST(NULLIF(CAST(S."DIFERENCIADEPO_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS DIFERENCIADEPO_ACUM,
                            CAST(NULLIF(CAST(S."ANTICIPOSOTROS_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOSOTROS_ACUM,
                            CAST(NULLIF(CAST(S."PAGOTOTANTIESPE_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS PAGOTOTANTIESPE_ACUM,
                            CAST(NULLIF(CAST(S."PRESTAMOCAJAAHORRO_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS PRESTAMOCAJAAHORRO_ACUM,
                            CAST(NULLIF(CAST(S."RESERAGUINRED_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERAGUINRED_ACUM,
                            CAST(NULLIF(CAST(S."RESERCRECIPAT_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERCRECIPAT_ACUM,
                            CAST(NULLIF(CAST(S."RESERVACARED_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERVACARED_ACUM,
                            CAST(NULLIF(CAST(S."RETENCIONUNTERCIO_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENCIONUNTERCIO_ACUM,
                            CAST(NULLIF(CAST(S."RETENCIONIMP_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENCIONIMP_ACUM,
                            CAST(NULLIF(CAST(S."RETENCIONIMPNOMEMP_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENCIONIMPNOMEMP_ACUM,
                            CAST(NULLIF(CAST(S."TOTALANTICIPOS_ACUM" AS VARCHAR), '') AS DOUBLE PRECISION) AS TOTALANTICIPOS_ACUM,
                            CAST(NULLIF(CAST(S."ANTIDESPCONT_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIDESPCONT_RECUP,
                            CAST(NULLIF(CAST(S."ANTIIMPRFACT_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIIMPRFACT_RECUP,
                            CAST(NULLIF(CAST(S."ANTILIDERESCOM_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTILIDERESCOM_RECUP,
                            CAST(NULLIF(CAST(S."ANTIPAGOMULTAS_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOMULTAS_RECUP,
                            CAST(NULLIF(CAST(S."ANTIPAGOSALARIOS_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOSALARIOS_RECUP,
                            CAST(NULLIF(CAST(S."ANTIPAGOTEL_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOTEL_RECUP,
                            CAST(NULLIF(CAST(S."ANTICAPACITACION_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICAPACITACION_RECUP,
                            CAST(NULLIF(CAST(S."ANTIESPECIALES_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIESPECIALES_RECUP,
                            CAST(NULLIF(CAST(S."APORTCAJAAHORROS_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS APORTCAJAAHORROS_RECUP,
                            CAST(NULLIF(CAST(S."DESCUENTOFASTFOOD_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCUENTOFASTFOOD_RECUP,
                            CAST(NULLIF(CAST(S."DESCSEGVOLUN_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCSEGVOLUN_RECUP,
                            CAST(NULLIF(CAST(S."DEVRETENDIFIVA_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS DEVRETENDIFIVA_RECUP,
                            CAST(NULLIF(CAST(S."DIFERENCIADEPO_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS DIFERENCIADEPO_RECUP,
                            CAST(NULLIF(CAST(S."ANTICIPOSOTROS_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOSOTROS_RECUP,
                            CAST(NULLIF(CAST(S."PAGOTOTANTIESPE_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS PAGOTOTANTIESPE_RECUP,
                            CAST(NULLIF(CAST(S."PRESTAMOCAJAAHORRO_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS PRESTAMOCAJAAHORRO_RECUP,
                            CAST(NULLIF(CAST(S."RESERAGUINRED_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERAGUINRED_RECUP,
                            CAST(NULLIF(CAST(S."RESERCRECIPAT_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERCRECIPAT_RECUP,
                            CAST(NULLIF(CAST(S."RESERVACARED_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERVACARED_RECUP,
                            CAST(NULLIF(CAST(S."RETENUNTERCIOIVA_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENUNTERCIOIVA_RECUP,
                            CAST(NULLIF(CAST(S."RETENIMPNOMEMP_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPNOMEMP_RECUP,
                            CAST(NULLIF(CAST(S."RETENIMPUESTOS_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPUESTOS_RECUP,
                            CAST(NULLIF(CAST(S."TOTALANTICIPOS_RECUP" AS VARCHAR), '') AS DOUBLE PRECISION) AS TOTALANTICIPOS_RECUP,
                            CAST(NULLIF(CAST(S."ANTIDESPCONTABLE_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIDESPCONTABLE_SALDO,
                            CAST(NULLIF(CAST(S."ANTIIMPFACT_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIIMPFACT_SALDO,
                            CAST(NULLIF(CAST(S."ANTILIDERESCOM_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTILIDERESCOM_SALDO,
                            CAST(NULLIF(CAST(S."ANTIPAGOMULTAS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOMULTAS_SALDO,
                            CAST(NULLIF(CAST(S."ANTIPAGOSALARIOS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOSALARIOS_SALDO,
                            CAST(NULLIF(CAST(S."ANTIPAGOTELEFONO_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIPAGOTELEFONO_SALDO,
                            CAST(NULLIF(CAST(S."ANTICAPACITACION_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICAPACITACION_SALDO,
                            CAST(NULLIF(CAST(S."ANTIESPECIALES_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTIESPECIALES_SALDO,
                            CAST(NULLIF(CAST(S."APORTCAJAAHORRO_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS APORTCAJAAHORRO_SALDO,
                            CAST(NULLIF(CAST(S."DESCFASTFOOD_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCFASTFOOD_SALDO,
                            CAST(NULLIF(CAST(S."DESCSEGDIFIVA_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS DESCSEGDIFIVA_SALDO,
                            CAST(NULLIF(CAST(S."DEVRETENDIFIVA_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS DEVRETENDIFIVA_SALDO,
                            CAST(NULLIF(CAST(S."DIFDESPOSITOS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS DIFDESPOSITOS_SALDO,
                            CAST(NULLIF(CAST(S."ANTICIPOSOTROS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS ANTICIPOSOTROS_SALDO,
                            CAST(NULLIF(CAST(S."PAGOTOTANTIESPECIA_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS PAGOTOTANTIESPECIA_SALDO,
                            CAST(NULLIF(CAST(S."PRESTOTALCAJAHORRO_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS PRESTOTALCAJAHORRO_SALDO,
                            CAST(NULLIF(CAST(S."RESERAGUINRED_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERAGUINRED_SALDO,
                            CAST(NULLIF(CAST(S."RESERCRECIPAT_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERCRECIPAT_SALDO,
                            CAST(NULLIF(CAST(S."RESERVACARED_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS RESERVACARED_SALDO,
                            CAST(NULLIF(CAST(S."RETENUNTERCIOIVA_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENUNTERCIOIVA_SALDO,
                            CAST(NULLIF(CAST(S."RETENIMPNOMEMP_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPNOMEMP_SALDO,
                            CAST(NULLIF(CAST(S."RETENIMPUESTOS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENIMPUESTOS_SALDO,
                            CAST(NULLIF(CAST(S."TOTALANTICIPOS_SALDO" AS VARCHAR), '') AS DOUBLE PRECISION) AS TOTALANTICIPOS_SALDO,
                            I.INCENTIVOVTACAT AS INCENTIVOVTACAT,
                            D.DEV_AGUINALDO_ANTERIOR,
                            D.DEV_AGUINALDO_MENSUAL,
                            D.DEV_AGUINALDO_ACUMULADA,
                            D.DEV_AGUINALDO_RECUP,
                            D.DEV_AGUINALDO_SALDO,
                            A."FechaInicio",
                            A."FechaFin",
                            CAST(NULLIF(CAST(S."RETENCIONISR" AS VARCHAR), '') AS DOUBLE PRECISION) AS RETENCIONISR,
                            CAST(NULLIF(CAST(S."IMPCEDULAR" AS VARCHAR), '') AS DOUBLE PRECISION) AS IMPCEDULAR
                        FROM "dtSabananew" S
                        CROSS JOIN periodo
                        LEFT JOIN incentivo_venta_cat I
                            ON S."COMISIONID" = I."COMISIONID"
                        LEFT JOIN dev_aguinaldo D
                            ON S."COMISIONID" = D."COMISIONID"
                        LEFT JOIN asignacion_centro_trabajo A
                            ON ('TIE-' || S."CRPLAZA" || S."CRTIENDA") = A."CentroTrabajoID"
                           AND S."RFC" = A."RFC"
                        WHERE S."FECHAINICIAL" >= periodo.FECHAINI
                          AND S."FECHAFINAL" <= periodo.FECHAFIN
                          AND {filters["filtro_distritos_sql"]}
                          AND {filters["filtro_plazas_sql"]}
                          AND {filters["filtro_tiendas_sql"]}
                        ORDER BY 'PLA-' || S."CRPLAZA", S."CRDISTRITO", S."NOMBRECOMISIONISTA"
                    ) q
            """
,
                "output": "CatCalculation.csv"
            }
        ]

        # Descarga de datos desde ICM a DuckDB
        download_remote_queries(api_url, headers, queries, base_path)
    except Exception as e:
        print(f"Error descargando datos desde API: {e}")
        raise

    csv_table_map = {
        # RelaciÃ³n entre el nombre fÃ­sico del CSV descargado y la tabla destino en DuckDB.
        "CatCalculation.csv": "CatCalculation",
    }

    ensure_required_csvs(base_path, csv_table_map.keys())

    CONN = get_duckdb_connection(db_path)
    try:
        for file_name, table_name in csv_table_map.items():
            csv_file = os.path.join(base_path, file_name)
            if os.path.exists(csv_file):
                print(f"Se insertarÃ¡ la siguiente tabla: {table_name}")
                CONN.execute(f"DROP TABLE IF EXISTS {table_name};")
                insert_csv_into_table(CONN, csv_file, table_name, header=True, truncate=True)
                if table_name == "CatCalculation":
                    deduplicate_table_by_max_comisionid(CONN, "CatCalculation")
            else:
                print(f"Advertencia: Archivo {csv_file} no existe")
    finally:
        CONN.close()
   
    # Ejecutar proceso principal
    main(db_path)

    # Exportar resultado a CSV local
    export_main_output()

    # Limpieza de CSV intermedios
    cleanup_intermediate_csvs()


if __name__ == "__main__":
    # Ejecuta el flujo completo cuando el archivo se corre como script principal.
    run_process()
