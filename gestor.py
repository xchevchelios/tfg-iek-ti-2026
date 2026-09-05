#!/usr/bin/env python3
import subprocess
import os
import sys
import time

# CONFIGURACIÓN
# Lista exacta de los servicios systemd a controlar
SERVICIOS = [
    "nginx",
    "docker",                 # Contenedor de Mosquitto
    "tfg-api",                # Tu servicio Python/Gunicorn
    "google-cloud-ops-agent"  # Agente de monitoreo (consumo de ancho de banda)
]

# COLORES ANSI
VERDE = "\033[92m"
ROJO = "\033[91m"
AMARILLO = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

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

def main():
    if os.geteuid() != 0:
        sys.exit(f"{ROJO}Error: Ejecutar como root (sudo).{RESET}")

    while True:
        mostrar_dashboard()
        print(f"{BOLD}OPCIONES:{RESET}")
        print(" 1. ACTIVAR todo (Demo/Trabajo)")
        print(" 2. APAGAR todo (Ahorro Costos)")
        print(" 3. Reiniciar solo Web (Nginx)")
        print(" 0. Salir")
        
        opc = input("\nSeleccione: ")
        
        if opc == '1': iniciar_todo()
        elif opc == '2': detener_todo()
        elif opc == '3': reiniciar_nginx()
        elif opc == '0': sys.exit()

if __name__ == '__main__':
    main()
