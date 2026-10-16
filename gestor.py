#!/usr/bin/env python3
"""
Orquestador del servidor del TFG.

    sudo python3 gestor.py

Prende y apaga los servicios, y muestra el estado de la red sin abrir el
panel web: cuentas, estaciones y metricas del broker.

Usa solo la biblioteca estandar a proposito: tiene que andar aunque el venv
de la API este roto o los servicios esten caidos, que es justamente cuando
mas se lo necesita.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
API_DIR = RAIZ / "api"

CONTENEDOR_MQTT = "mosquitto"
TOPIC_ESTADO_GATEWAY = "estaciones/estado"   # Last Will del gateway
TOPIC_NODOS = "gateway/config/nodos"         # el mismo que api/configuracion_red.py

# CONFIGURACIÓN
# Lista exacta de los servicios systemd a controlar
SERVICIOS = [
    "nginx",
    "docker",                 # Contenedor de Mosquitto
    "tfg-api",                # API Flask/Gunicorn
    "tfg-ingesta",            # Ingesta MQTT -> SQLite (antes corria en tmux)
    "google-cloud-ops-agent"  # Agente de monitoreo (consumo de ancho de banda)
]

# COLORES ANSI
VERDE = "\033[92m"
ROJO = "\033[91m"
AMARILLO = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"
GRIS = "\033[90m"


# =================================================================
# Configuracion: la misma que usan la API y la ingesta
# =================================================================

def leer_env(ruta):
    """Lee el .env de la API sin depender de python-dotenv."""
    valores = {}
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return valores
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        if clave.startswith("export "):
            clave = clave[len("export "):].strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        valores[clave] = valor
    return valores


ENV = leer_env(API_DIR / ".env")


def _entero(nombre, por_defecto):
    try:
        return int(ENV.get(nombre, por_defecto))
    except ValueError:
        return por_defecto


def _ruta_base():
    ruta = Path(ENV.get("DB_PATH") or API_DIR / "sensores.db")
    return ruta if ruta.is_absolute() else API_DIR / ruta


DB_PATH = _ruta_base()
TZ_OFFSET_HORAS = _entero("TZ_OFFSET_HORAS", -3)
SESION_HORAS = _entero("SESION_HORAS", 12)

def limpiar():
    os.system('clear')

def ejecutar(cmd):
    """Ejecuta comando silencioso, retorna True si fue exitoso"""
    try:
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def get_estado(svc):
    """Verifica si un servicio está activo"""
    if ejecutar(f"systemctl is-active --quiet {svc}"):
        return f"{VERDE}ACTIVO{RESET}"
    return f"{ROJO}INACTIVO{RESET}"

def mostrar_dashboard():
    limpiar()
    print(f"{BOLD}========================================{RESET}")
    print(f"{BOLD}   ORQUESTADOR TFG - SISTEMAS{RESET}")
    print(f"{BOLD}========================================{RESET}")
    print("Estado actual:")
    
    todos_activos = True
    for svc in SERVICIOS:
        estado = get_estado(svc)
        print(f" • {svc:<25}: {estado}")
        if "INACTIVO" in estado:
            todos_activos = False
            
    print(f"{BOLD}========================================{RESET}")
    
    # Diagnóstico de puertos rápido
    try:
        puertos = subprocess.check_output("netstat -tulpn | grep LISTEN | grep -v 127.0.0.1", shell=True).decode()
        print(f"{AMARILLO}Puertos Públicos Abiertos:{RESET}")
        print(puertos if puertos else "Ninguno (Modo Ahorro Total)")
    except:
        print("Ninguno (Modo Ahorro Total)")
    print("----------------------------------------")

def iniciar_todo():
    print(f"\n{VERDE}>>> INICIANDO SISTEMAS (MODO PRODUCCIÓN)...{RESET}")
    for svc in SERVICIOS:
        print(f"Arrancando {svc}...", end=" ", flush=True)
        # Usamos enable --now para asegurar que arranque y se habilite
        if ejecutar(f"systemctl enable --now {svc}"):
            print(f"{VERDE}OK{RESET}")
        else:
            print(f"{ROJO}ERROR{RESET}")
    time.sleep(2)

def detener_todo():
    print(f"\n{ROJO}>>> DETENIENDO TODO (MODO AHORRO)...{RESET}")
    for svc in SERVICIOS:
        print(f"Deteniendo {svc}...", end=" ", flush=True)
        # Usamos disable --now para detener y evitar auto-arranque
        if ejecutar(f"systemctl disable --now {svc}"):
            print(f"{VERDE}DETENIDO{RESET}")
        else:
            print(f"{ROJO}ERROR{RESET}")
    
    # Limpieza profunda de seguridad
    print("Asegurando cierre de puertos...", end=" ")
    os.system("killall -q gunicorn")
    print(f"{VERDE}OK{RESET}")
    time.sleep(2)

def reiniciar_nginx():
    print("\nReiniciando Nginx...", end=" ")
    if ejecutar("systemctl restart nginx"):
        print(f"{VERDE}OK{RESET}")
    else:
        print(f"{ROJO}ERROR{RESET}")
    time.sleep(1)

# =================================================================
# Consultas (solo lectura)
# =================================================================

def titulo(texto):
    print(f"{BOLD}{'=' * 66}{RESET}")
    print(f"{BOLD}  {texto}{RESET}")
    print(f"{BOLD}{'=' * 66}{RESET}")


def seccion(texto):
    print(f"\n{BOLD}{texto}{RESET}")


def celda(texto, ancho):
    texto = str(texto)
    if len(texto) > ancho:
        texto = texto[: ancho - 1] + "…"
    return texto.ljust(ancho)


def miles(n):
    return f"{n:,}".replace(",", ".")


def a_utc(texto):
    """Interpreta los timestamps del sistema, que se guardan todos en UTC."""
    if not texto:
        return None
    try:
        dt = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hora_local(dt):
    if dt is None:
        return "—"
    return (dt + timedelta(hours=TZ_OFFSET_HORAS)).strftime("%d/%m %H:%M")


def antiguedad(dt):
    """Devuelve (texto, color) segun cuanto hace que paso dt."""
    if dt is None:
        return "nunca", ROJO
    s = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if s < 60:
        texto = f"hace {s} s"
    elif s < 3600:
        texto = f"hace {s // 60} min"
    elif s < 86400:
        texto = f"hace {s // 3600} h"
    else:
        texto = f"hace {s // 86400} d"
    color = VERDE if s < 300 else AMARILLO if s < 3600 else ROJO
    return texto, color


@contextmanager
def base_de_datos():
    """Abre la base en solo lectura y con la identidad de su dueño.

    El gestor corre como root. Si abriera la base como root, SQLite podria
    crear los archivos -wal y -shm a nombre de root, y desde ese momento la
    ingesta y la API (que corren con el usuario normal) no podrian escribir:
    la proxima medicion fallaria con 'attempt to write a readonly database'.
    Leer con el uid del dueño del archivo hace imposible ese accidente.
    """
    if not DB_PATH.exists():
        raise RuntimeError(f"No existe la base en {DB_PATH}")
    dueno = DB_PATH.stat()
    os.setegid(dueno.st_gid)
    os.seteuid(dueno.st_uid)
    try:
        conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        os.seteuid(0)
        os.setegid(0)


def ver_cuentas():
    titulo("CUENTAS ACTIVAS")
    ahora = datetime.now(timezone.utc)
    with base_de_datos() as conn:
        usuarios = conn.execute(
            "SELECT id, email, nombre, rol, ultimo_acceso FROM usuarios "
            "WHERE activo = 1 ORDER BY rol, email"
        ).fetchall()
        bajas = conn.execute("SELECT COUNT(*) FROM usuarios WHERE activo = 0").fetchone()[0]
        sesiones = conn.execute("SELECT usuario_id, expira_en FROM sesiones").fetchall()

    abiertas = {}
    for sesion in sesiones:
        vence = a_utc(sesion["expira_en"])
        if vence and vence > ahora:
            abiertas[sesion["usuario_id"]] = abiertas.get(sesion["usuario_id"], 0) + 1

    if not usuarios:
        print(f"\n{AMARILLO}No hay cuentas activas.{RESET} "
              "La primera se crea con api/crear_usuario.py.")
    else:
        print(f"\n{GRIS} {celda('ROL', 9)} {celda('CORREO', 30)} {celda('NOMBRE', 20)} "
              f"{celda('ÚLTIMO ACCESO', 14)} SESIONES{RESET}")
        for u in usuarios:
            acceso = a_utc(u["ultimo_acceso"])
            n = abiertas.get(u["id"], 0)
            texto_sesion = (f"{VERDE}{n} abierta{'s' if n != 1 else ''}{RESET}"
                            if n else f"{GRIS}—{RESET}")
            print(f" {celda(u['rol'], 9)} {celda(u['email'], 30)} {celda(u['nombre'], 20)} "
                  f"{celda(hora_local(acceso) if acceso else 'nunca entró', 14)} {texto_sesion}")

    print(f"\n{len(usuarios)} activas · {bajas} dadas de baja (no se listan)")
    print(f"{GRIS}Sesión abierta = cookie todavía válida; vence tras {SESION_HORAS} h sin "
          "actividad.\nNo implica que la persona tenga el panel abierto ahora. "
          f"Horas en UTC{TZ_OFFSET_HORAS:+d}.{RESET}")


def ver_estaciones():
    titulo("ESTACIONES ACTIVAS")
    desde = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with base_de_datos() as conn:
        estaciones = conn.execute(
            "SELECT id, nombre, direccion_lora FROM estaciones WHERE activa = 1 "
            "ORDER BY direccion_lora IS NULL, direccion_lora, id"
        ).fetchall()
        bajas = conn.execute("SELECT COUNT(*) FROM estaciones WHERE activa = 0").fetchone()[0]
        # Dos consultas por estacion, ambas resueltas por el indice
        # (station_id, timestamp): ninguna recorre la tabla de mediciones.
        filas = []
        for e in estaciones:
            ultima = conn.execute(
                "SELECT MAX(timestamp) FROM mediciones WHERE station_id = ?", (e["id"],)
            ).fetchone()[0]
            recientes = conn.execute(
                "SELECT COUNT(*) FROM mediciones WHERE station_id = ? AND timestamp >= ?",
                (e["id"], desde),
            ).fetchone()[0]
            filas.append((e, a_utc(ultima), recientes))

    if not filas:
        print(f"\n{AMARILLO}No hay estaciones activas.{RESET}")
    else:
        print(f"\n{GRIS} {'DIR':>3}  {celda('ID', 14)} {celda('NOMBRE', 24)} "
              f"{celda('ÚLTIMA MEDICIÓN', 16)} {'ÚLT. 24 H':>9}{RESET}")
        for e, ultima, recientes in filas:
            texto, color = antiguedad(ultima)
            direccion = "—" if e["direccion_lora"] is None else str(e["direccion_lora"])
            print(f" {direccion:>3}  {celda(e['id'], 14)} {celda(e['nombre'], 24)} "
                  f"{color}{celda(texto, 16)}{RESET} {miles(recientes):>9}")

    interrogadas = [e["direccion_lora"] for e, _, _ in filas if e["direccion_lora"] is not None]
    print(f"\nEl gateway debería interrogar: {interrogadas}")
    for e, _, _ in filas:
        if e["direccion_lora"] is None:
            print(f"{AMARILLO}! {e['id']} está activa pero sin dirección LoRa: "
                  f"el gateway no la interroga.{RESET}")
    print(f"{len(filas)} activas · {bajas} dadas de baja (no se listan)")
    print(f"{GRIS}Última medición: verde < 5 min, amarillo < 1 h, rojo más de 1 h o nunca.\n"
          f"Para ver si el gateway recibió esta lista: opción 6.{RESET}")


def consultar_broker(topics, espera=2):
    """Lee los mensajes retenidos de los topics, desde adentro del contenedor.

    Usa el mosquitto_sub del propio contenedor para no depender de paho en el
    Python del sistema. La clave viaja como argumento, asi que es visible en
    'ps' durante los dos segundos de la consulta: aceptable en una VM donde el
    unico usuario con shell es el administrador.
    """
    usuario = ENV.get("MQTT_USER")
    if not usuario:
        raise RuntimeError("No encontré MQTT_USER en api/.env.")
    comando = [
        "docker", "exec", CONTENEDOR_MQTT, "mosquitto_sub",
        "-h", "localhost", "-p", "1883",
        "-u", usuario, "-P", ENV.get("MQTT_PASS", ""),
        "-W", str(espera),
        # Formato propio: hay topics con espacios ("$SYS/broker/retained
        # messages/count") y -v separa topic y contenido con un espacio.
        "-F", "%t\t%p\n",
    ]
    for topic in topics:
        comando += ["-t", topic]
    try:
        r = subprocess.run(comando, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=espera + 10)
    except FileNotFoundError:
        raise RuntimeError("No encontré el comando docker.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("El broker no respondió a tiempo.")

    mensajes = {}
    for linea in r.stdout.splitlines():
        if "\t" in linea:
            topic, carga = linea.split("\t", 1)
            mensajes[topic] = carga
    # -W termina con codigo 27 al vencer la espera: es el final normal.
    if not mensajes and r.returncode not in (0, 27):
        detalle = (r.stderr or r.stdout).strip()
        raise RuntimeError(detalle or f"mosquitto_sub terminó con código {r.returncode}.")
    return mensajes


def _sys(mensajes, clave):
    """Valor numerico de un topic $SYS, o None si el broker no lo publica."""
    try:
        return float(mensajes[f"$SYS/broker/{clave}"].split()[0])
    except (KeyError, ValueError, IndexError):
        return None


def _num(valor, decimales=1):
    if valor is None:
        return "—"
    return f"{valor:.{decimales}f}" if decimales else miles(int(valor))


def _duracion(segundos):
    d, resto = divmod(int(segundos), 86400)
    h, resto = divmod(resto, 3600)
    m = resto // 60
    if d:
        return f"{d} d {h} h"
    return f"{h} h {m} min" if h else f"{m} min"


def ver_broker():
    titulo("BROKER MQTT")
    mensajes = consultar_broker(["$SYS/broker/#", TOPIC_ESTADO_GATEWAY, TOPIC_NODOS])

    # --- Gateway: lo dice su Last Will ---------------------------------
    seccion("Gateway")
    estado = mensajes.get(TOPIC_ESTADO_GATEWAY)
    if estado is None:
        print(f"  Estado: {AMARILLO}desconocido{RESET} "
              f"(no hay estado retenido en {TOPIC_ESTADO_GATEWAY})")
    else:
        try:
            valor = str(json.loads(estado).get("gateway_status", estado))
        except (ValueError, AttributeError):
            valor = estado
        color = VERDE if valor.upper() == "ONLINE" else ROJO
        print(f"  Estado: {color}{valor}{RESET}")
    print(f"  {GRIS}Lo publica el gateway al conectar; si se corta sin despedirse, el\n"
          f"  broker publica OFFLINE en su nombre (Last Will).{RESET}")

    # --- Lista de nodos: lo publicado contra lo que dice la base ----------
    seccion("Lista de nodos para el gateway")
    with base_de_datos() as conn:
        en_base = [f["direccion_lora"] for f in conn.execute(
            "SELECT direccion_lora FROM estaciones "
            "WHERE activa = 1 AND direccion_lora IS NOT NULL ORDER BY direccion_lora"
        )]
    publicada = mensajes.get(TOPIC_NODOS)
    nodos = None
    if publicada is None:
        print(f"  Retenida en el broker: {AMARILLO}ninguna{RESET}")
    else:
        try:
            datos = json.loads(publicada)
            nodos = datos.get("nodos")
            cuando = hora_local(a_utc(datos.get("actualizado")))
        except (ValueError, AttributeError):
            cuando = "—"
        print(f"  Retenida en el broker: {nodos}  (publicada {cuando})")
    print(f"  Según la base:         {en_base}")
    if isinstance(nodos, list) and sorted(nodos) == en_base:
        print(f"  {VERDE}✓ Coinciden.{RESET}")
    else:
        print(f"  {ROJO}✗ No coinciden.{RESET} Reenviala con «Reenviar al gateway» en el panel.")

    # --- Metricas del broker ($SYS) ---------------------------------------
    seccion("Broker")
    if not any(t.startswith("$SYS/") for t in mensajes):
        print(f"  {AMARILLO}El broker no entregó métricas.{RESET} Falta permiso de lectura:")
        print(f"  agregar 'topic read $SYS/#' al usuario {ENV.get('MQTT_USER')} en "
              f"mqtt/config/acl\n  y recargar con: docker kill -s HUP {CONTENEDOR_MQTT}")
        return

    uptime = _sys(mensajes, "uptime")
    print(f"  {mensajes.get('$SYS/broker/version', 'mosquitto')} · en marcha hace "
          f"{_duracion(uptime) if uptime is not None else '—'}")
    print(f"  Clientes conectados    {_num(_sys(mensajes, 'clients/connected'), 0)}")
    print(f"  Suscripciones          {_num(_sys(mensajes, 'subscriptions/count'), 0)}")
    print(f"  Mensajes retenidos     {_num(_sys(mensajes, 'retained messages/count'), 0)}")
    print(f"  Publicaciones por min  recibidas "
          f"{_num(_sys(mensajes, 'load/publish/received/1min'))} (1 min) · "
          f"{_num(_sys(mensajes, 'load/publish/received/15min'))} (15 min)")
    print(f"                         enviadas  "
          f"{_num(_sys(mensajes, 'load/publish/sent/1min'))} (1 min) · "
          f"{_num(_sys(mensajes, 'load/publish/sent/15min'))} (15 min)")
    salida = _sys(mensajes, "load/bytes/sent/15min")
    if salida is not None:
        print(f"  Tráfico saliente       {salida / 1000:.1f} KB/min "
              f"≈ {salida * 1440 / 1e6:.1f} MB/día (promedio 15 min)")
    memoria = _sys(mensajes, "heap/current")
    if memoria is not None:
        print(f"  Memoria del broker     {memoria / 1e6:.1f} MB")

    print(f"\n  {GRIS}Recibidas ≈ lo que publica el gateway: una por nodo que respondió.\n"
          "  Enviadas = cada publicación × sus suscriptores (la ingesta y cada\n"
          "  navegador con el dashboard abierto). El tráfico saliente incluye la\n"
          "  ingesta, que es local: lo que sale a internet es menos.\n"
          "  Los clientes conectados pueden incluir esta misma consulta, y el\n"
          f"  broker actualiza estas cifras cada 10 s.{RESET}")


VISTAS = {
    "4": ver_cuentas,
    "5": ver_estaciones,
    "6": ver_broker,
}


def mostrar_vista(vista):
    limpiar()
    try:
        vista()
    except (RuntimeError, sqlite3.Error, OSError) as error:
        print(f"\n{ROJO}No se pudo mostrar: {error}{RESET}")
    input(f"\n{GRIS}Enter para volver al menú…{RESET}")


def main():
    if os.geteuid() != 0:
        sys.exit(f"{ROJO}Error: Ejecutar como root (sudo).{RESET}")

    while True:
        mostrar_dashboard()
        print(f"{BOLD}OPCIONES:{RESET}")
        print(" 1. ACTIVAR todo (Demo/Trabajo)")
        print(" 2. APAGAR todo (Ahorro Costos)")
        print(" 3. Reiniciar solo Web (Nginx)")
        print(f"{GRIS} ── consultas (solo lectura) ──{RESET}")
        print(" 4. Cuentas activas y roles")
        print(" 5. Estaciones activas")
        print(" 6. Broker: gateway, lista de nodos y métricas")
        print(" 0. Salir")
        
        opc = input("\nSeleccione: ")
        
        if opc == '1': iniciar_todo()
        elif opc == '2': detener_todo()
        elif opc == '3': reiniciar_nginx()
        elif opc in VISTAS: mostrar_vista(VISTAS[opc])
        elif opc == '0': sys.exit()

if __name__ == '__main__':
    main()
