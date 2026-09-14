import argparse
import logging
import threading
import time
import sys
import socket
import Pyro5.api
import Pyro5.nameserver
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [CENTRAL] %(message)s', datefmt='%H:%M:%S')

def obtener_ip_local():
    """Detecta la IP de red local (LAN) de esta máquina."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Estado global de la Central
barcos_registrados = {}
flota_formada = False
estado_flota = {}
eventos_lamport = []
solicitudes_recibidas = []

@Pyro5.api.expose
class CentralServicio:
    def registrar_barco(self, barco_id, nombre, host, port, lat, lon, rumbo, vel):
        barcos_registrados[barco_id] = {
            "id": barco_id,
            "nombre": nombre,
            "host": host,
            "port": port,
            "lat": lat,
            "lon": lon,
            "rumbo": rumbo,
            "vel": vel,
            "activo": True,
            "es_primario": False
        }
        logging.info(f"Barco {barco_id} '{nombre}' registrado desde {host}:{port}")
        return True

    def recibir_solicitud(self, accion, datos, barco_origen, lamport):
        solicitudes_recibidas.append({
            "accion": accion,
            "datos": datos,
            "origen": barco_origen,
            "lamport": lamport,
            "timestamp": time.time()
        })
        logging.info(f"[L:{lamport}] Recibida solicitud '{accion}' del barco {barco_origen}")
        eventos_lamport.append((lamport, time.time(), f"Solicitud '{accion}' recibida del Barco {barco_origen}"))
        return {"recibido": True}

    def notificar_nuevo_primario(self, primario_id):
        global estado_flota
        viejo_primario = None
        for b_id, b_data in barcos_registrados.items():
            if b_data.get("es_primario") and b_id != primario_id:
                viejo_primario = b_id
                b_data["es_primario"] = False
                b_data["activo"] = False # El primario anterior cayó, lo que motivó la elección

        if primario_id in barcos_registrados:
            barcos_registrados[primario_id]["es_primario"] = True
            barcos_registrados[primario_id]["activo"] = True

        estado_flota = barcos_registrados.copy()
        
        msg = f"Cambio de mando: Nuevo Coordinador electo -> Barco {primario_id}"
        if viejo_primario:
            msg += f" (Barco {viejo_primario} desconectado / inactivo)"
            logging.warning(f"Primario anterior {viejo_primario} marcado como INACTIVO/HUNDIDO")
        
        logging.info(f"Nuevo primario registrado en Central: Barco {primario_id}")
        eventos_lamport.append((0, time.time(), msg))
        return True

    def obtener_barcos_registrados(self):
        return barcos_registrados

def formar_flota(ns_host, ns_port):
    global flota_formada, estado_flota
    if flota_formada:
        print("La flota ya está formada.")
        return
    
    if not barcos_registrados:
        print("No hay barcos registrados.")
        return

    # 1. Determinar topología y primario (el de mayor ID)
    ids_ordenados = sorted(list(barcos_registrados.keys()))
    primario_id = ids_ordenados[-1]
    
    topologia = []
    ns = Pyro5.api.locate_ns(host=ns_host, port=ns_port)
    
    for b_id in ids_ordenados:
        try:
            uri = ns.lookup(f"flota.barco.{b_id}")
            topologia.append((b_id, uri))
        except Exception as e:
            logging.error(f"No se encontró la URI del barco {b_id} en el NS: {e}")
            return

    # Registrar el primario inicial en el NS
    try:
        ns.register("flota.primario", topologia[-1][1])
    except Exception as e:
        logging.error(f"Error registrando flota.primario: {e}")

    # 2. Distribuir a los barcos
    for b_id, uri in topologia:
        try:
            barco = Pyro5.api.Proxy(uri)
            barco._pyroTimeout = 2.0
            barco.configurar_flota(topologia, primario_id)
            logging.info(f"Topología enviada al barco {b_id}")
        except Exception as e:
            logging.error(f"Error configurando barco {b_id}: {e}")

    # Marcar estados en la central
    for b_id in ids_ordenados:
        barcos_registrados[b_id]["es_primario"] = (b_id == primario_id)
        barcos_registrados[b_id]["activo"] = True
    estado_flota = barcos_registrados.copy()
    eventos_lamport.append((0, time.time(), f"Flota formada. Primario inicial: Barco {primario_id}"))

    flota_formada = True
    logging.info(f"Flota formada exitosamente. Primario inicial: Barco {primario_id}")

def hilo_polling(ns_host, ns_port):
    """Consulta periódicamente al primario para obtener el estado de la flota."""
    global estado_flota
    fallos_primario = 0
    while True:
        time.sleep(3) # Polling cada 3 segundos
        if not flota_formada: continue
        
        try:
            # Buscar el primario actual en el NS
            ns = Pyro5.api.locate_ns(host=ns_host, port=ns_port)
            try:
                primario_uri = ns.lookup("flota.primario")
            except Exception:
                fallos_primario += 1
                if fallos_primario >= 2:
                    for b_id, b_data in barcos_registrados.items():
                        if b_data.get("es_primario") and b_data.get("activo"):
                            b_data["activo"] = False
                            logging.warning(f"Primario {b_id} no localizado en NS. Marcado como INACTIVO")
                            eventos_lamport.append((0, time.time(), f"Alerta: Primario Barco {b_id} no responde en NS"))
                    estado_flota = barcos_registrados.copy()
                continue
                
            primario = Pyro5.api.Proxy(primario_uri)
            primario._pyroTimeout = 2.0
            
            nuevo_estado = primario.obtener_estado_flota()
            fallos_primario = 0
            
            # Actualizamos barcos_registrados con el estado en vivo
            for b_id, estado in nuevo_estado.items():
                b_id_int = int(b_id) if str(b_id).isdigit() else b_id
                if b_id_int in barcos_registrados:
                    barcos_registrados[b_id_int].update(estado)
                elif b_id in barcos_registrados:
                    barcos_registrados[b_id].update(estado)
            estado_flota = barcos_registrados.copy()
            
        except Exception as e:
            fallos_primario += 1
            if fallos_primario >= 2:
                for b_id, b_data in barcos_registrados.items():
                    if b_data.get("es_primario") and b_data.get("activo"):
                        b_data["activo"] = False
                        logging.warning(f"Primario {b_id} no responde en polling. Marcado como INACTIVO")
                        eventos_lamport.append((0, time.time(), f"Alerta: Primario Barco {b_id} desconectado"))
                estado_flota = barcos_registrados.copy()

# --- Servidor HTTP para el Dashboard ---

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            try:
                with open(os.path.join(os.path.dirname(__file__), 'dashboard.html'), 'rb') as f:
                    self.wfile.write(f.read())
            except Exception as e:
                self.wfile.write(b"Error cargando dashboard.html")
        
        elif self.path == '/api/estado':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(estado_flota).encode())
            
        elif self.path == '/api/eventos':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(eventos_lamport).encode())
            
        else:
            self.send_response(404)
            self.end_headers()
            
    def log_message(self, format, *args):
        pass # Silenciar logs HTTP

def iniciar_http(port):
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    logging.info(f"Dashboard HTTP escuchando en http://localhost:{port}")
    server.serve_forever()

def cmd_loop(ns_host, ns_port):
    time.sleep(2)
    while True:
        try:
            print("\nComandos: formar flota, estado, log, q")
            cmd = input("> ").strip().lower()
            if not cmd: continue
            
            if cmd == "formar flota":
                formar_flota(ns_host, ns_port)
            elif cmd == "estado":
                print(json.dumps(estado_flota, indent=2))
            elif cmd == "log":
                for l, t, desc in eventos_lamport:
                    print(f"L:{l} | {desc}")
            elif cmd == "q":
                break
            else:
                print("Comando no reconocido.")
        except (EOFError, KeyboardInterrupt):
            break
    os._exit(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=obtener_ip_local(),
                        help="IP en la que escucha la Central (default: IP de red local)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--ns-port", type=int, default=9090)
    parser.add_argument("--http-port", type=int, default=5000)
    args = parser.parse_args()

    if args.host in ("0.0.0.0", ""):
        args.host = obtener_ip_local()

    # Iniciar Name Server embebido en hilo secundario
    ns_uri, ns_daemon, ns_bc_server = Pyro5.nameserver.start_ns(host=args.host, port=args.ns_port)
    threading.Thread(target=ns_daemon.requestLoop, daemon=True).start()
    logging.info(f"Name Server iniciado en {args.host}:{args.ns_port}")

    # Iniciar Daemon de la Central
    daemon = Pyro5.api.Daemon(host=args.host, port=args.port)
    central = CentralServicio()
    uri = daemon.register(central, "central")
    
    # Registrar la Central en el NS
    ns_daemon.nameserver.register("flota.central", uri)

    # Iniciar hilos
    threading.Thread(target=hilo_polling, args=(args.host, args.ns_port), daemon=True).start()
    threading.Thread(target=iniciar_http, args=(args.http_port,), daemon=True).start()
    threading.Thread(target=cmd_loop, args=(args.host, args.ns_port), daemon=True).start()

    logging.info(f"Central Daemon escuchando en {uri}")
    try:
        daemon.requestLoop()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
