import sys
import os

def agregarpath(libreria, required=False):
    # Construir la ruta completa del archivo o directorio 'libreria'
    ruta_libreria = os.path.join('root', 'Script', 'Library' )

    # Validación 1: Verificar si la ruta existe
    if not os.path.exists(ruta_libreria):
        if required:
            print(f"Error: La ruta {ruta_libreria} no existe.")
        return

    # Validación 2: Si es un archivo .zip, añadirlo correctamente
    if ruta_libreria.endswith('.zip'):
        # Asegurarse de que es un archivo .zip
        if os.path.isfile(ruta_libreria):
            sys.path.insert(0, ruta_libreria)
            print(f"El archivo .zip {ruta_libreria} ha sido agregado a sys.path.")
        else:
            print(f"Error: {ruta_libreria} no es un archivo válido.")
        return

    # Validación 3: Si es un directorio, verificar que sea un directorio válido
    if os.path.isdir(ruta_libreria):
        sys.path.insert(0, ruta_libreria)
        print(f"La ruta {ruta_libreria} ha sido agregada a sys.path.")
        return

    # Si no es un archivo .zip ni un directorio, mostrar error
    print(f"Error: La ruta {ruta_libreria} no es un directorio válido ni un archivo .zip.")


agregarpath("Library")

import requests
import csv
import gc
import json
import pandas as pd
#===================================== MANEJO DE ARCHIVOS Y CARGA A DUCK DB =====================================   

def table_exists(CONN, table_name: str) -> bool:
    try:
        CONN.execute(f"SELECT COUNT(*) FROM {table_name} LIMIT 1")
        return True
    except Exception:
        return False

def OVR(result, default_value=None):
    try:
        """Recibe un resultado de `fetchone()` y devuelve un valor válido o el valor por defecto."""
        if result is not None:
            return result[0]
        else:
            return default_value
    except Exception as e:
        print("OVR Error:", e)
        return None    

def insert_csv_into_table(
    CONN, 
    csv_path: str, 
    table_name: str, 
    header: bool = True,
    truncate: bool = True
    ):
    """
    Inserta datos de un archivo CSV en una tabla de DuckDB.
    
    Args:
        CONN: Conexión a DuckDB
        csv_path: Ruta al archivo CSV
        table_name: Nombre de la tabla destino
        header: Si el CSV tiene encabezados (default: True)
        truncate: Si True, trunca la tabla antes de insertar. Si False, agrega datos.
                 Si la tabla no existe, se crea independientemente de este valor.
    
    Returns:
        None
    """
    try:
        header_sql = "true" if header else "false"
        table_exist = table_exists(CONN, table_name)
        
        if table_exist:
            # La tabla ya existe
            if truncate:
                # Truncar y luego insertar
                CONN.execute(f"TRUNCATE TABLE {table_name};")
                CONN.execute(f"""
                    INSERT INTO {table_name}
                    SELECT * FROM read_csv('{csv_path}', header={header_sql});
                """)
            else:
                # Solo insertar (agregar datos sin truncar)
                CONN.execute(f"""
                    INSERT INTO {table_name}
                    SELECT * FROM read_csv('{csv_path}', header={header_sql});
                """)
        else:
            # La tabla no existe, crearla con los datos del CSV
            CONN.execute(f"""
                CREATE TABLE {table_name} AS
                SELECT * FROM read_csv('{csv_path}', header={header_sql});
            """)
        
    except Exception as e:
        print(f"insert_csv_into_table Error: {e}")
        raise  # Re-lanzar la excepción para que el caller pueda manejarla


def export_table_to_csv(CONN, table_name, output_csv):
    try:
        query = f"""
            SELECT 
               *
            FROM {table_name}
        """
        # Exportar usando pandas para controlar el encoding
        df = CONN.execute(query).fetchdf()

        # Tablas a las que se les debe aplicar formato especial
        tablas_formato_especial = {
            "dtCasosEspecialesNominaOptima",
            "dtConsolidadoNominaOptima",
            "dtOxxoTdaEgresosComisionistaNO",
            "dtPagosManuales",
            "dtPagosManualesIssues",
        }

        if table_name in tablas_formato_especial:
            # Formatear columnas de fecha a MM/DD/YYYY solo para estas tablas
            try:
                for col in df.columns:
                    if pd.api.types.is_datetime64_any_dtype(df[col]) or pd.api.types.is_datetime64tz_dtype(df[col]):
                        df[col] = df[col].dt.strftime('%m/%d/%Y')
            except Exception:
                # Si algo falla en la detección/formateo de fechas, continuamos con el df original
                pass

            # Exportar con 2 decimales para floats
            df.to_csv(output_csv, index=False, encoding='utf-8', float_format='%.2f')
        else:
            # Comportamiento original para el resto de tablas
            df.to_csv(output_csv, index=False, encoding='utf-8')  # ← aquí defines la codificación

        print(f"CSV exportado: {output_csv}")
    except Exception as e:
        print("export_table_to_csv:", e)
        return None
    finally:
        return None

def procesar_tabla(nombre_tabla, query_string, fecha_inicio=None, fecha_fin=None, id_store_str=None,CONN=None,api_url=None,headers=None):
    try:
        validacion = obtener_validacion(CONN, nombre_tabla)

        if validacion==1: 
            payload = {
                "queryString": query_string.format(
                    FechaInicioPeriodo=fecha_inicio or '',
                    FechaFinPeriodo=fecha_fin or '',
                    id_store_str=id_store_str or ''
                    ),
                "offset": 0,
                "limit": 0,
                "exportFileFormat": "Text"
            }
            print(nombre_tabla)
            output_csv = os.path.join('root', 'Script', 'spAnexo20Saldos', f'{nombre_tabla}.csv')
            fetch_and_convert_to_csv(api_url, headers, payload, output_csv,CONN)
            insert_csv_into_table(CONN, output_csv, nombre_tabla, header=True, truncate=True)
    except Exception as e:
        print("procesar_tabla Error:", e)         
    finally:    
        gc.collect()

def limpiar_valor(valor, nombre_columna, tipos_columnas):
    """Limpia el valor según el tipo de la columna."""
    if valor is None:
        return ""

    tipo = tipos_columnas.get(nombre_columna)

    # Si es fecha en formato ISO 8601, nos quedamos solo con la parte de la fecha
    if tipo == "Date" and isinstance(valor, str):
        # Ejemplo: "2014-01-01T00:00:00Z" -> "2014-01-01"
        return valor.split("T", 1)[0]

    return valor

def fetch_and_convert_to_csv(api_url, headers, payload, output_csv, CONN, numero=0):
    nombre_tabla = os.path.splitext(os.path.basename(output_csv))[0]
    print(f'Descargando tabla: {nombre_tabla}')

    local_payload = dict(payload)

    try:
        # 1 => JSON y ';'
        if int(numero) == 1:
            local_payload.pop("exportFileFormat", None)
            api_url_json = api_url.replace('/export', '')

            response = requests.post(api_url_json, headers=headers, json=local_payload, timeout=120)
            response.raise_for_status()
            data = response.json()

            column_definitions = data.get("columnDefinitions") or []
            columnas = [c["name"] for c in column_definitions]
            tipos_columnas = {c["name"]: c.get("type") for c in column_definitions}

            with open(output_csv, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                if columnas:
                    writer.writerow(columnas)

                for fila in data.get("data") or []:
                    fila_limpia = [
                        limpiar_valor(valor, columnas[i], tipos_columnas)
                        for i, valor in enumerate(fila)
                    ]
                    writer.writerow(fila_limpia)

        # 0 => TEXT y ','
        else:
            response = requests.post(api_url, headers=headers, json=local_payload, stream=True, timeout=120)
            response.raise_for_status()

            with open(output_csv, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=',')
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    writer.writerow(line.split(','))

    except requests.exceptions.HTTPError as e:
        response = e.response
        detail = ""
        if response is not None:
            try:
                detail = response.text.strip()
            except Exception:
                detail = ""
        if detail:
            print(f"fetch_and_convert_to_csv Error: {e}. Respuesta API: {detail}")
        else:
            print("fetch_and_convert_to_csv Error:", e)
        raise
    except Exception as e:
        print("fetch_and_convert_to_csv Error:", e)
        raise


def fetch_and_convert_to_csv_old(api_url, headers, payload, output_csv, CONN,numero):
    """
    - Si la tabla es Time / Time_:
        * Se quita exportFileFormat del payload -> la API regresa JSON
        * Se genera CSV usando ';' como delimitador
    - Si NO es Time / Time_:
        * Se usa el payload tal cual (exportFileFormat='Text', etc.)
        * Se asume texto plano separado por comas
    """
    nombre_tabla = os.path.splitext(os.path.basename(output_csv))[0]
    print(f'Descargando tabla: {nombre_tabla}') 

    # Copia local del payload para no modificar el original
    local_payload = dict(payload)
    
    try:
        # ----- TABLA TIME / TIME_ -> JSON -----
        if nombre_tabla.lower() in ("time", "time_", "cfgdatestringperiod", "cfglastperiods", "historyenabletosap"):

            # Forzamos respuesta JSON eliminando exportFileFormat
            local_payload.pop("exportFileFormat", None)

            # Limpiar url del api, para quitar /export de la url
            api_url_json = api_url.replace('/export', '')

            response = requests.post(api_url_json, headers=headers, json=local_payload)
            response.raise_for_status()

            data = response.json()

            column_definitions = data.get("columnDefinitions") or []
            columnas = [c["name"] for c in column_definitions]
            tipos_columnas = {c["name"]: c.get("type") for c in column_definitions}

            with open(output_csv, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')

                # Escribir encabezados
                if columnas:
                    writer.writerow(columnas)

                # Escribir filas (limpiando fechas y valores None)
                for fila in data.get("data") or []:
                    fila_limpia = [
                        limpiar_valor(valor, columnas[i], tipos_columnas)
                        for i, valor in enumerate(fila)
                    ]
                    writer.writerow(fila_limpia)

        # ----- RESTO DE TABLAS -> TEXT -----
        else:
            # stream=True para procesar línea a línea y no cargar todo en RAM
            response = requests.post(api_url, headers=headers, json=local_payload, stream=True)
            response.raise_for_status()

            with open(output_csv, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)  # delimitador por defecto = ','
                for line in response.iter_lines(decode_unicode=False):
                    if not line:  # saltar líneas vacías
                        continue
                    
                    line = line.decode('utf-8')
                    writer.writerow(line.split(','))  # la API manda comas

    except Exception as e:
        print("fetch_and_convert_to_csv Error:", e)
  

# ===================================== EXTRACCION _RESULT ==================================

def export_resultjson_string_to_csv(CONN,outputFolder,json_string, output_filename,calculation_id):
    json_data = json.loads(json_string)
    headers = [col["name"] for col in json_data["columnDefinitions"]]
    rows = json_data["data"]
    df = pd.DataFrame(rows, columns=headers)
    
    output_filename = os.path.join('root', 'Script', outputFolder, output_filename)
    df.to_csv(output_filename, index=False, encoding="utf-8")
    #print(output_filename)
    #print (f"_Result{calculation_id}  antes de insercion")
    #insert_csv_into_table(CONN, output_filename, f"_Result{calculation_id}", header=True, truncate=True)

def fetch_and_convertresult_to_csv(CONN,outputFolder,calculation_id,PeriodName,headers,limit):
    """
    Realiza una solicitud POST a la API, recibe una respuesta en texto plano y la convierte en un archivo CSV.

    :param api_url: URL de la API a la que se realizará la solicitud POST.
    :param headers: Diccionario con los encabezados, incluyendo el token Bearer.
    :param payload: Diccionario con los datos que se enviarán en el cuerpo de la solicitud.
    :param output_csv: Ruta del archivo CSV de salida.
    """
    #auth_token= base64.b64decode(os.getenv("API_KEY")).decode("utf-8")
    #model='femcoprd'
    # headers = {
    #     "Authorization": f"Bearer {auth_token}",
    #     "Content-Type": "application/json",
    #     "model": model
    # }
    params = {
    "offset": 0,
    "limit": limit,
    "allowSync": "false",
    "partialSyncBack": "false",
     "isRowViewer": "false",
    "filter": f"Meses={PeriodName}" 
    }
 
    api_url = f"https://api.cloud.varicent.com/api/v1/calculations/{calculation_id}/data"
    # Realizar la solicitud GET

    response = requests.get(api_url, headers=headers, params=params)

    if response.status_code == 200:
        json_string = response.text
        try:
            json_data = json.loads(json_string)
            if "columnDefinitions" not in json_data or "data" not in json_data:
                print(f"Respuesta inválida para calculation_id={calculation_id}")
                return
        except json.JSONDecodeError:
            print(f"Error decodificando JSON para calculation_id={calculation_id}")
            return
        
        export_resultjson_string_to_csv(CONN,outputFolder, json_string, f"_Result{calculation_id}.csv", calculation_id)
    else:
        print(f"Error en la solicitud: {response.status_code} para calculation_id={calculation_id}")

def safe_delete(conn, tabla, where_clause, params, allowed_tables):
    """
    Ejecuta un DELETE seguro con tabla validada y condición WHERE dinámica con varios parámetros.

    Args:
        conn: Conexión DuckDB.
        tabla (str): Nombre de la tabla.
        where_clause (str): Condición WHERE con placeholders '?'.
        params (tuple/list): Parámetros para los placeholders.
        allowed_tables (list): Lista de tablas permitidas.

    Raises:
        ValueError si la tabla no está permitida.
    """
    if tabla not in allowed_tables:
        raise ValueError(f"❌ Tabla '{tabla}' no está permitida.")
    
    sql = f"DELETE FROM {tabla} WHERE {where_clause}"
    conn.execute(sql, params)

def safe_deleteIN(conn, tabla, where_clause, params=None, allowed_tables=[]):
    """
    Ejecuta un DELETE seguro con validación de tabla. Permite cláusulas WHERE con subconsultas.

    Args:
        conn: Conexión DuckDB.
        tabla (str): Nombre de la tabla.
        where_clause (str): Condición WHERE como string completo (puede incluir subconsultas).
        params (tuple/list): Parámetros opcionales para placeholders.
        allowed_tables (list): Lista blanca de tablas permitidas.
    """
    if tabla not in allowed_tables:
        raise ValueError(f"❌ Tabla '{tabla}' no está permitida.")
    
    sql = f"DELETE FROM {tabla} WHERE {where_clause}"
    if params:
        conn.execute(sql, params)
    else:
        conn.execute(sql)

def safe_truncate(conn, tabla, allowed_tables):
    """
    Ejecuta un TRUNCATE seguro con tabla validada.

    Args:
        conn: Conexión DuckDB.
        tabla (str): Nombre de la tabla.
        allowed_tables (list): Lista de tablas permitidas.

    Raises:
        ValueError si la tabla no está permitida.
    """

    if tabla not in allowed_tables:
        raise ValueError(f" Tabla '{tabla}' no está permitida.")

    elif not table_exists(conn, tabla):
        print(f" Tabla '{tabla}' no existe en la base de datos.")

    else:
        sql = f"TRUNCATE TABLE {tabla}"
        conn.execute(sql)          

def safe_insert(conn, tabla, insert_sql_template, allowed_tables, params=None):
    """
    Ejecuta un INSERT INTO seguro validando que la tabla esté permitida.

    Args:
        conn: Conexión a DuckDB.
        tabla (str): Nombre de la tabla destino.
        insert_sql_template (str): Query SQL con `{tabla}` como placeholder del nombre de tabla.
        params (list/tuple/None): Parámetros a bindear. Puede ser None si no hay placeholders.
        allowed_tables (list): Lista de tablas permitidas.

    Raises:
        ValueError: Si la tabla no está permitida.
    """
    if tabla not in allowed_tables:
        raise ValueError(f" Tabla '{tabla}' no está permitida para INSERT.")
    
    sql = insert_sql_template.format(tabla=tabla)
    if params:
        conn.execute(sql, params)
    else:
        conn.execute(sql)    

def safe_update(conn, tabla, query, allowed_tables, params=None):
    if tabla not in allowed_tables:
        raise ValueError(f"❌ Tabla '{tabla}' no está permitida.")
    if params:
        conn.execute(query, params)
    else:
        conn.execute(query)        

def obtener_validacion(CONN, nombre_tabla):
    query = '''
        SELECT CASE WHEN E.UltimaEjecucion > U.UltimaEjecucion THEN 1 ELSE 0 END AS Validacion 
        FROM ValidacionEjecucion E
        INNER JOIN ValidacionUltimaEjecucion U
            ON E.NombreTabla = U.NombreTabla
        WHERE E.NombreTabla = ?
    '''
    resultado = CONN.execute(query, (nombre_tabla,)).fetchone()
    validacion = OVR(resultado) if resultado else None
    return validacion

def compactar_base_duckdb(ruta_db, umbral_kb=900 * 1024):
    """
    Verifica el tamaño de una base de datos DuckDB y la compacta si excede el umbral dado.

    :param ruta_db: Ruta completa al archivo .duckdb
    :param umbral_kb: Umbral en KB para aplicar compactación (por defecto: 10 MB)
    """
    if not os.path.exists(ruta_db):
        print(f"❌ La base de datos no existe en: {ruta_db}")
        return

    tamano_bytes = os.path.getsize(ruta_db)
    tamano_kb = tamano_bytes // 1024
    print(f"📦 Tamaño actual de la base de datos: {tamano_kb} KB")

    if tamano_kb <= umbral_kb:
        print("✅ Base de datos dentro del tamaño permitido. No se requiere compactación.")
        return

    print("⚠️ Base de datos grande, aplicando minimización...")

    temp_dir = tempfile.mkdtemp()
    con = duckdb.connect(ruta_db)

    try:
        # Exportar a carpeta temporal
        con.execute(f"EXPORT DATABASE '{temp_dir}'")
        con.close()

        # Borrar archivo original
        os.remove(ruta_db)

        # Crear nueva base vacía
        con = duckdb.connect(ruta_db)

        # Importar desde carpeta temporal
        con.execute(f"IMPORT DATABASE '{temp_dir}'")

        print("✅ Base de datos minimizada con éxito.")

    except Exception as e:
        print(f"❌ Error durante la compactación: {e}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            con.close()
        except:
            pass

def borrar_csv_en_directorio(directorio):
    # Lista archivos en el directorio (no entra en subdirectorios)
    for archivo in os.listdir(directorio):
        ruta_completa = os.path.join(directorio, archivo)
        # Solo borra si es archivo y termina en .csv
        if os.path.isfile(ruta_completa) and archivo.lower().endswith('.csv'):
            os.remove(ruta_completa)
