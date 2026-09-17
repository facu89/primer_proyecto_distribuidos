import argparse
import logging
import random
import threading
import time
import sys
import socket
import Pyro5.api
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
solicitudes_recibidas = {}
solicitud_id_counter = 0
historial_comunicacion = []  # [{barco_id, lamport, timestamp, direccion, mensaje}, ...] en orden de llegada
NS_HOST = "127.0.0.1"
NS_PORT = 9090

# --- Estado de replicación / elección entre Centrales (Principal-Respaldo) ---
# Mismo esquema de anillo + elección por mayor ID que ya usan los barcos,
# pero aplicado a las instancias de Central. Solo la PRINCIPAL atiende
# pedidos activamente (polling al barco primario, "formar flota"); las
# de RESPALDO solo reciben el estado replicado y vigilan a la principal.
lock_central = threading.Lock()
CENTRAL_ID = None
soy_principal = False
en_eleccion_central = False
ultimo_latido_principal = 0.0
ultimo_anuncio_central = None
reloj_logico_central = 0
topologia_centrales = []   # [(id, pyro_uri), ...] armado desde el Name Server
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 5000
peers_http = []            # [{"id":.., "host":.., "http_port":..}, ...] centrales hermanas (para el dashboard)
TIMEOUT_PRINCIPAL_CENTRAL = 5.0

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

        if flota_formada:
            logging.info(f"Flota ya en marcha. Reincorporando Barco {barco_id} al anillo...")
            ns = Pyro5.api.locate_ns(host=NS_HOST, port=NS_PORT)
            ids_ordenados = sorted(list(barcos_registrados.keys()))
            topologia = []
            for b_id in ids_ordenados:
                try:
                    uri = ns.lookup(f"flota.barco.{b_id}")
                    topologia.append((b_id, uri))
                except Exception as e:
                    logging.warning(f"No se pudo resolver URI de Barco {b_id} en NS: {e}")

            primario_id = None
            try:
                prim_uri = ns.lookup("flota.primario")
                for b_id, uri in topologia:
                    if uri == prim_uri:
                        primario_id = b_id
                        break
            except Exception:
                pass

            if not primario_id:
                candidatos = [b_id for b_id in ids_ordenados if b_id != barco_id and barcos_registrados[b_id].get("activo")]
                primario_id = max(candidatos) if candidatos else barco_id

            # Actualizar topología en todos los demás barcos
            for b_id, uri in topologia:
                if b_id == barco_id: continue
                try:
                    p = Pyro5.api.Proxy(uri)
                    p._pyroTimeout = 2.0
                    p.actualizar_topologia(topologia, barco_id)
                except Exception as e:
                    logging.warning(f"No se pudo notificar topología a Barco {b_id}: {e}")

            barcos_registrados[barco_id]["activo"] = True
            barcos_registrados[barco_id]["es_primario"] = (barco_id == primario_id)
            estado_flota.update(barcos_registrados)
            eventos_lamport.append((0, time.time(), f"Barco {barco_id} reincorporado a la flota activa"))

            return {
                "flota_formada": True,
                "topologia": topologia,
                "primario_id": primario_id
            }

        return {"flota_formada": False}

    def recibir_solicitud(self, accion, datos, barco_origen, lamport):
        global solicitud_id_counter, reloj_logico_central
        with lock_central:
            solicitud_id_counter += 1
            sol_id = solicitud_id_counter
            solicitudes_recibidas[sol_id] = {
                "id": sol_id,
                "accion": accion,
                "datos": datos,
                "origen": barco_origen,
                "lamport": lamport,
                "timestamp": time.time(),
                "estado": "pendiente"
            }
            reloj_logico_central = max(reloj_logico_central, lamport) + 1
            logging.info(f"[L:{lamport}] Recibida solicitud #{sol_id} '{accion}' del barco {barco_origen}")
            eventos_lamport.append((lamport, time.time(), f"Solicitud #{sol_id} '{accion}' recibida del Barco {barco_origen}"))

            historial_comunicacion.append({
                "solicitud_id": sol_id,
                "barco_id": barco_origen,
                "lamport": lamport,
                "timestamp": time.time(),
                "direccion": "barco->central",
                "mensaje": f"Solicitud #{sol_id} '{accion}' ({datos})"
            })

        return {"id": sol_id, "recibido": True}

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

    # --- Réplica Principal-Respaldo entre Centrales ---

    def obtener_rol(self):
        """Usado por el dashboard para saber si esta instancia es la principal."""
        return {"id": CENTRAL_ID, "soy_principal": soy_principal}

    def obtener_estado_para_backup(self):
        """Invocado por una Central que arranca/se reincorpora para clonar el estado actual."""
        if not soy_principal:
            raise Exception(f"Central {CENTRAL_ID} no es la principal")
        with lock_central:
            return {
                "barcos_registrados": dict(barcos_registrados),
                "flota_formada": flota_formada,
                "estado_flota": dict(estado_flota),
                "eventos_lamport": list(eventos_lamport),
                "solicitudes_recibidas": dict(solicitudes_recibidas),
                "solicitud_id_counter": solicitud_id_counter,
                "historial_comunicacion": list(historial_comunicacion),
            }

    def replicar_estado_central(self, payload, lamport, origen_id):
        """Invocado por la Central Principal para actualizar a esta Central de Respaldo.

        También sirve para resolver un split-brain (dos Centrales creyéndose Principal a
        la vez, por ejemplo si arrancaron juntas y el aviso de la elección se perdió):
        si YO también me creía Principal pero quien me está replicando tiene mayor ID,
        le cedo el rol. Si tengo mayor ID, ignoro su réplica (la mía es la legítima).
        """
        global flota_formada, ultimo_latido_principal, reloj_logico_central, soy_principal, solicitud_id_counter
        with lock_central:
            if soy_principal:
                if origen_id > CENTRAL_ID:
                    soy_principal = False
                    logging.warning(f"[CENTRAL {CENTRAL_ID}] Split-brain detectado: Central {origen_id} "
                                     f"tiene mayor ID. Paso a RESPALDO.")
                else:
                    return True  # Tengo mayor ID: sigo siendo la Principal legítima, ignoro esta réplica

            reloj_logico_central = max(reloj_logico_central, lamport) + 1
            barcos_registrados.clear()
            barcos_registrados.update(payload["barcos_registrados"])
            flota_formada = payload["flota_formada"]
            estado_flota.clear()
            estado_flota.update(payload["estado_flota"])
            eventos_lamport[:] = payload["eventos_lamport"]
            solicitud_id_counter = max(solicitud_id_counter, payload.get("solicitud_id_counter", 0))
            solicitudes_recibidas.clear()
            for k, v in payload.get("solicitudes_recibidas", {}).items():
                k_int = int(k) if str(k).isdigit() else k
                solicitudes_recibidas[k_int] = v
            historial_comunicacion[:] = payload["historial_comunicacion"]
            ultimo_latido_principal = time.time()
        return True

    def recibir_mensaje_eleccion_central(self, tipo, participantes, origen, numero):
        """Mensaje del anillo de Centrales (mismo esquema que la elección de barcos)."""
        global en_eleccion_central, ultimo_anuncio_central
        if tipo == "ELECCION":
            if CENTRAL_ID in participantes:
                ganador = max(participantes)
                logging.warning(f"[CENTRAL-ELECCION] Elección {numero} completada. Gana Central {ganador}.")
                if _proclamar_central(ganador) and ganador == CENTRAL_ID:
                    _asumir_como_principal()
                with lock_central:
                    ultimo_anuncio_central = (ganador, origen, numero)
                threading.Thread(target=_mandar_al_siguiente_central,
                                  args=("recibir_mensaje_eleccion_central", "COORDINADOR", [ganador], origen, numero),
                                  daemon=True).start()
            else:
                with lock_central:
                    en_eleccion_central = True
                participantes.append(CENTRAL_ID)
                threading.Thread(target=_mandar_al_siguiente_central,
                                  args=("recibir_mensaje_eleccion_central", "ELECCION", participantes, origen, numero),
                                  daemon=True).start()
        elif tipo == "COORDINADOR":
            ganador = participantes[0]
            with lock_central:
                repetido = (ultimo_anuncio_central == (ganador, origen, numero))
                ultimo_anuncio_central = (ganador, origen, numero)
            if repetido:
                return True
            if _proclamar_central(ganador) and ganador == CENTRAL_ID:
                _asumir_como_principal()
            if CENTRAL_ID != origen:
                threading.Thread(target=_mandar_al_siguiente_central,
                                  args=("recibir_mensaje_eleccion_central", "COORDINADOR", [ganador], origen, numero),
                                  daemon=True).start()
        return True

# --- Funciones del anillo Principal-Respaldo entre Centrales ---
# (mismo esquema que el anillo de barcos, aplicado a instancias de Central)

def _refrescar_topologia_centrales():
    """Reconstruye la lista de Centrales conocidas consultando el Name Server."""
    global topologia_centrales
    try:
        ns = Pyro5.api.locate_ns(host=NS_HOST, port=NS_PORT)
        entradas = ns.list(prefix="flota.central.")
        nuevos = []
        for nombre, uri in entradas.items():
            try:
                cid = int(nombre.split(".")[-1])
                nuevos.append((cid, uri))
            except ValueError:
                continue
        topologia_centrales = sorted(nuevos, key=lambda x: x[0])
    except Exception as e:
        logging.warning(f"No se pudo refrescar la topología de centrales: {e}")

def _mandar_al_siguiente_central(metodo, *args):
    """Recorre el anillo de Centrales enviando un mensaje al primer nodo que responda."""
    if not topologia_centrales:
        return None
    mi_pos = 0
    for i, (cid, _) in enumerate(topologia_centrales):
        if cid == CENTRAL_ID:
            mi_pos = i
            break
    for salto in range(1, len(topologia_centrales)):
        cid, curi = topologia_centrales[(mi_pos + salto) % len(topologia_centrales)]
        try:
            nodo = Pyro5.api.Proxy(curi)
            nodo._pyroTimeout = 2.0
            getattr(nodo, metodo)(*args)
            return cid
        except Exception:
            continue
    return None

def _iniciar_eleccion_central(motivo):
    global en_eleccion_central
    with lock_central:
        if en_eleccion_central:
            return
        en_eleccion_central = True

    _refrescar_topologia_centrales()
    numero = random.randint(1000, 9999)
    logging.warning(f"[CENTRAL-ELECCION] Iniciando elección de Central Principal ({motivo}).")
    destino = _mandar_al_siguiente_central("recibir_mensaje_eleccion_central", "ELECCION", [CENTRAL_ID], CENTRAL_ID, numero)

    if destino is None:
        # No hay otras centrales en el anillo: me proclamo principal directamente.
        _proclamar_central(CENTRAL_ID)
        _asumir_como_principal()

def _proclamar_central(ganador):
    global soy_principal, en_eleccion_central
    with lock_central:
        cambio = (soy_principal != (ganador == CENTRAL_ID))
        soy_principal = (ganador == CENTRAL_ID)
        en_eleccion_central = False
    return cambio

def _registrar_como_principal_en_ns():
    """Registra a ESTA Central como 'flota.central' (el alias que todos buscan) en el NS.
    Se llama al asumir el rol, y también periódicamente mientras se es Principal: así,
    si dos Centrales se auto-proclamaron casi al mismo tiempo y una pisó el registro de
    la otra en el NS, la Principal legítima (mayor ID) lo vuelve a corregir sola en
    unos segundos, sin depender de que un único aviso de elección haya llegado bien."""
    try:
        ns = Pyro5.api.locate_ns(host=NS_HOST, port=NS_PORT)
        mi_uri = None
        for cid, uri in topologia_centrales:
            if cid == CENTRAL_ID:
                mi_uri = uri
        if not mi_uri:
            return
        try:
            actual = str(ns.lookup("flota.central"))
        except Exception:
            actual = None
        if actual != str(mi_uri):
            try:
                ns.remove("flota.central")
            except Exception:
                pass
            ns.register("flota.central", mi_uri)
            logging.info(f"Central {CENTRAL_ID} registrada como 'flota.central' (principal) en el Name Server.")
    except Exception as e:
        logging.error(f"Error registrando esta Central como principal en el Name Server: {e}")

def _asumir_como_principal():
    """Esta instancia pasa a ser la Central Principal: se registra como tal en el NS
    y le empuja su copia del estado a las demás (por si eran ellas las desactualizadas)."""
    global soy_principal
    soy_principal = True
    logging.warning(f"[CENTRAL {CENTRAL_ID}] Asumiendo rol de CENTRAL PRINCIPAL.")
    _registrar_como_principal_en_ns()
    _replicar_a_backups()

def _replicar_a_backups():
    """La Central Principal empuja su estado completo a todas las de respaldo.
    También funciona como heartbeat: cada Respaldo actualiza 'ultimo_latido_principal'
    al recibir esta llamada, así detecta si la principal deja de responder. Manda el
    propio ID para que el receptor pueda resolver un split-brain por mayor ID si hiciera falta."""
    global reloj_logico_central
    with lock_central:
        reloj_logico_central += 1
        l_actual = reloj_logico_central
        payload = {
            "barcos_registrados": dict(barcos_registrados),
            "flota_formada": flota_formada,
            "estado_flota": dict(estado_flota),
            "eventos_lamport": list(eventos_lamport),
            "solicitudes_recibidas": dict(solicitudes_recibidas),
            "solicitud_id_counter": solicitud_id_counter,
            "historial_comunicacion": list(historial_comunicacion),
        }

    for cid, curi in topologia_centrales:
        if cid == CENTRAL_ID:
            continue
        try:
            backup = Pyro5.api.Proxy(curi)
            backup._pyroTimeout = 1.5
            backup.replicar_estado_central(payload, l_actual, CENTRAL_ID)
        except Exception:
            pass  # El backup no respondió; el heartbeat se encarga de detectarlo si sigue así

def hilo_vigilante_central():
    """Si soy principal: reafirmo mi registro en el NS y replico periódicamente a las
    de respaldo (heartbeat incluido). Si soy respaldo: si la principal deja de
    replicarme, arranco una elección."""
    while True:
        time.sleep(1.5)
        if soy_principal:
            _refrescar_topologia_centrales()
            _registrar_como_principal_en_ns()
            _replicar_a_backups()
        else:
            if en_eleccion_central:
                continue
            silencio = time.time() - ultimo_latido_principal
            if silencio > TIMEOUT_PRINCIPAL_CENTRAL:
                _iniciar_eleccion_central("La Central Principal no responde")

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
        if not soy_principal: continue # Solo la Central Principal hace polling activo
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
    def _send_json(self, obj):
        try:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            # Permite que el dashboard de OTRA central (otro puerto) pregunte acá
            # durante la búsqueda de la nueva principal tras una caída.
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # El navegador cerró/canceló la conexión a mitad de la respuesta; no es un error real

    def do_GET(self):
        if self.path == '/':
            try:
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                with open(os.path.join(os.path.dirname(__file__), 'dashboard.html'), 'rb') as f:
                    self.wfile.write(f.read())
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            except Exception:
                self.wfile.write(b"Error cargando dashboard.html")

        elif self.path == '/api/estado':
            self._send_json(estado_flota)

        elif self.path == '/api/eventos':
            self._send_json(eventos_lamport)

        elif self.path == '/api/rol':
            # Le permite al dashboard saber si ESTA central es la principal
            self._send_json({"id": CENTRAL_ID, "soy_principal": soy_principal})

        elif self.path == '/api/centrales':
            # Direcciones de dashboard conocidas, para que el frontend pueda
            # buscar la nueva principal si esta central se cae
            mi_info = {"id": CENTRAL_ID, "host": HTTP_HOST, "http_port": HTTP_PORT}
            self._send_json([mi_info] + peers_http)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass # Silenciar logs HTTP

def iniciar_http(port):
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    logging.info(f"Dashboard HTTP escuchando en http://localhost:{port}")
    server.serve_forever()

ultimas_ubicaciones = {}
ultima_actualizacion_ubicaciones = None
#Nuevo Impresion de las tablas
def _imprimir_tabla_ubicaciones(estado):
    if not estado:
        print("No hay datos de ubicaciones todavía.")
        return
    print(f"\n{'ID':<5}{'ACTIVO':<8}{'LAT':<10}{'LON':<10}{'RUMBO':<8}{'VEL':<6}")
    for b_id, b in sorted(estado.items()):
        activo = "SI" if b.get("activo") else "NO"
        marca = " (PRIMARIO)" if b.get("es_primario") else ""
        print(f"{b_id:<5}{activo:<8}{b.get('latitud', 0):<10.4f}{b.get('longitud', 0):<10.4f}{b.get('rumbo', 0):<8.1f}{b.get('velocidad', 0):<6.1f}{marca}")
#Nuevo Central le pide primario ubicaciones
def solicitar_ubicaciones(ns_host, ns_port):
    """Le pide al primario el estado actualizado de la flota, lo guarda y lo muestra en tabla."""
    global ultimas_ubicaciones, ultima_actualizacion_ubicaciones
    try:
        ns = Pyro5.api.locate_ns(host=ns_host, port=ns_port)
        primario_uri = ns.lookup("flota.primario")
        primario = Pyro5.api.Proxy(primario_uri)
        primario._pyroTimeout = 3.0
        estado = primario.obtener_estado_flota()
    except Exception as e:
        print(f"No se pudo contactar al primario: {e}")
        return

    ultimas_ubicaciones = estado
    ultima_actualizacion_ubicaciones = time.time()
    _imprimir_tabla_ubicaciones(estado)
#Nuevo muestra las ubicaciones guardadas
def ver_ubicaciones():
    """Muestra la última foto de ubicaciones guardada, sin contactar al primario."""
    if ultima_actualizacion_ubicaciones:
        print(f"(Última actualización hace {time.time() - ultima_actualizacion_ubicaciones:.0f}s)")
    _imprimir_tabla_ubicaciones(ultimas_ubicaciones)

def ver_solicitudes_pendientes():
    """Muestra la tabla de solicitudes pendientes de resolución."""
    with lock_central:
        pendientes = [s for s in solicitudes_recibidas.values() if s.get("estado") == "pendiente"]
    if not pendientes:
        print("No hay solicitudes pendientes.")
        return
    print(f"\n{'ID':<5}{'ORIGEN':<10}{'ACCIÓN':<25}{'ESTADO':<12}{'HORA'}")
    for s in sorted(pendientes, key=lambda x: x.get("id", 0)):
        hora = time.strftime("%H:%M:%S", time.localtime(s.get("timestamp", time.time())))
        print(f"{s.get('id', '?'):<5}Barco {s.get('origen', '?'):<4} {s.get('accion', ''):<25}{s.get('estado', ''):<12}{hora}")

def _resolver_solicitud(solicitud_id, decision):
    """Resuelve una solicitud ('aceptada' o 'rechazada'), registra en historial y notifica al Primario."""
    global reloj_logico_central
    with lock_central:
        sol_id_key = None
        for k in (solicitud_id, int(solicitud_id) if str(solicitud_id).isdigit() else None, str(solicitud_id)):
            if k in solicitudes_recibidas:
                sol_id_key = k
                break
        if sol_id_key is None:
            print(f"Error: La solicitud #{solicitud_id} no existe.")
            return False
        
        sol = solicitudes_recibidas[sol_id_key]
        if sol.get("estado") != "pendiente":
            print(f"Error: La solicitud #{solicitud_id} ya fue resuelta ({sol.get('estado')}).")
            return False

        sol["estado"] = decision
        reloj_logico_central += 1
        l_actual = reloj_logico_central

        accion = sol["accion"]
        barco_origen = sol["origen"]
        sol_real_id = sol.get("id", solicitud_id)
        desc = f"Solicitud #{sol_real_id} '{accion}' {decision.upper()} para Barco {barco_origen}"
        logging.info(f"[L:{l_actual}] {desc}")
        eventos_lamport.append((l_actual, time.time(), desc))

        historial_comunicacion.append({
            "solicitud_id": sol_real_id,
            "barco_id": barco_origen,
            "lamport": l_actual,
            "timestamp": time.time(),
            "direccion": "central->barco",
            "mensaje": f"{decision.upper()}: {accion} (Solicitud #{sol_real_id})"
        })

    # Replicar a respaldos de la central
    _replicar_a_backups()

    # Notificar al Primario de la flota vía RPC
    try:
        ns = Pyro5.api.locate_ns(host=NS_HOST, port=NS_PORT)
        primario_uri = ns.lookup("flota.primario")
        primario = Pyro5.api.Proxy(primario_uri)
        primario._pyroTimeout = 3.0
        primario.recibir_resolucion_solicitud(sol_real_id, accion, barco_origen, decision)
        print(f"Resolución enviada con éxito al Primario para Barco {barco_origen}: [{decision.upper()}] {accion}")
        return True
    except Exception as e:
        logging.error(f"Error notificando resolución al Primario: {e}")
        print(f"Advertencia: No se pudo contactar al Primario para notificar resolución: {e}")
        return False

def cmd_loop(ns_host, ns_port):
    time.sleep(2)
    while True:
        try:
            print("\nComandos: formar flota, estado, solicitar ubicaciones, ver ubicaciones, pendientes, aceptar <id>, rechazar <id>, log, rol, historial, q")
            cmd = input("> ").strip().lower()
            if not cmd: continue

            if cmd == "formar flota":
                if not soy_principal:
                    print("Esta Central es de RESPALDO. Solo la CENTRAL PRINCIPAL puede formar la flota.")
                else:
                    formar_flota(ns_host, ns_port)
            elif cmd == "estado":
                print(json.dumps(estado_flota, indent=2))
            elif cmd == "solicitar ubicaciones": #Nuevo se agregaron las opciones nuevas
                if not soy_principal:
                    print("Esta Central es de RESPALDO, no consulta al primario directamente.")
                else:
                    solicitar_ubicaciones(ns_host, ns_port)
            elif cmd == "ver ubicaciones":
                ver_ubicaciones()
            elif cmd == "pendientes":
                ver_solicitudes_pendientes()
            elif cmd.startswith("aceptar "):
                if not soy_principal:
                    print("Esta Central es de RESPALDO. Solo la CENTRAL PRINCIPAL puede resolver solicitudes.")
                else:
                    partes = cmd.split()
                    if len(partes) == 2 and partes[1].isdigit():
                        _resolver_solicitud(int(partes[1]), "aceptada")
                    else:
                        print("Uso: aceptar <id>")
            elif cmd.startswith("rechazar "):
                if not soy_principal:
                    print("Esta Central es de RESPALDO. Solo la CENTRAL PRINCIPAL puede resolver solicitudes.")
                else:
                    partes = cmd.split()
                    if len(partes) == 2 and partes[1].isdigit():
                        _resolver_solicitud(int(partes[1]), "rechazada")
                    else:
                        print("Uso: rechazar <id>")
            elif cmd == "log":
                for l, t, desc in eventos_lamport:
                    print(f"L:{l} | {desc}")
            elif cmd == "rol":
                print(f"Central ID {CENTRAL_ID} -> {'PRINCIPAL' if soy_principal else 'RESPALDO'}")
            elif cmd == "historial":
                if not historial_comunicacion:
                    print("No hay historial de comunicación todavía.")
                else:
                    print("\nHistorial de comunicación con la flota:")
                    for e in historial_comunicacion:
                        hora = time.strftime("%H:%M:%S", time.localtime(e["timestamp"]))
                        etiqueta = "principal" if e["direccion"] == "barco->central" else "central"
                        print(f"  {hora} {etiqueta} (barco {e['barco_id']}): {e['mensaje']}")
            elif cmd == "q":
                break
            else:
                print("Comando no reconocido.")
        except (EOFError, KeyboardInterrupt):
            break
    os._exit(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("id", type=int, help="ID de esta instancia de Central (ej: 1, 2)")
    parser.add_argument("--host", default=obtener_ip_local(),
                        help="IP en la que escucha esta Central (default: IP de red local)")
    parser.add_argument("--port", type=int, default=8080, help="Puerto del daemon Pyro5 de esta Central")
    parser.add_argument("--ns-host", default=obtener_ip_local(),
                        help="IP donde corre el Name Server independiente (nameserver.py)")
    parser.add_argument("--ns-port", type=int, default=9090)
    parser.add_argument("--http-port", type=int, default=5000)
    parser.add_argument("--peers-http", default="",
                        help="Otras Centrales, para que el dashboard sepa a dónde redirigir si esta cae. "
                             "Formato: id:host:puerto,id:host:puerto (ej: 2:127.0.0.1:5001)")
    args = parser.parse_args()

    if args.host in ("0.0.0.0", ""):
        args.host = obtener_ip_local()

    global NS_HOST, NS_PORT, CENTRAL_ID, HTTP_HOST, HTTP_PORT
    global soy_principal, ultimo_latido_principal, flota_formada
    NS_HOST = args.ns_host
    NS_PORT = args.ns_port
    CENTRAL_ID = args.id
    HTTP_HOST = args.host
    HTTP_PORT = args.http_port

    for item in args.peers_http.split(","):
        item = item.strip()
        if not item:
            continue
        pid, phost, pport = item.split(":")
        peers_http.append({"id": int(pid), "host": phost, "http_port": int(pport)})

    # Conectarse al Name Server independiente (ya debe estar corriendo: nameserver.py)
    # Reintenta unos segundos por si esta Central arrancó apenas antes que el NS terminara de levantar.
    ns = None
    for intento in range(10):
        try:
            ns = Pyro5.api.locate_ns(host=NS_HOST, port=NS_PORT)
            break
        except Exception as e:
            logging.warning(f"Name Server no disponible todavía en {NS_HOST}:{NS_PORT} "
                             f"(intento {intento + 1}/10): {e}")
            time.sleep(1)
    if ns is None:
        logging.error(f"No se pudo localizar el Name Server en {NS_HOST}:{NS_PORT} tras varios intentos. "
                       f"¿Está corriendo 'python nameserver.py'?")
        sys.exit(1)

    # Iniciar Daemon Pyro5 propio de esta Central y registrarse como flota.central.<id>
    daemon = Pyro5.api.Daemon(host=args.host, port=args.port)
    central = CentralServicio()
    uri = daemon.register(central, "central")
    ns.register(f"flota.central.{CENTRAL_ID}", uri)

    _refrescar_topologia_centrales()

    # Determinar rol inicial: si hay una Principal activa y CONFIRMADA (le pregunto su rol,
    # no alcanza con que el NS tenga una URI registrada), me sumo como Respaldo y clono su estado.
    # Si no puedo confirmar que hay una Principal viva -disputo el rol por ELECCIÓN de anillo
    # (misma lógica que los barcos) en vez de auto-proclamarme: así, si TODAS las centrales
    # estaban caídas y se levantan varias casi al mismo tiempo, no terminan dos creyéndose
    # Principal a la vez (split-brain) -la elección desempata sola por mayor ID.
    principal_encontrada = False
    try:
        principal_uri = ns.lookup("flota.central")
        principal = Pyro5.api.Proxy(principal_uri)
        principal._pyroTimeout = 3.0
        rol_remoto = principal.obtener_rol()
        if rol_remoto.get("soy_principal"):
            estado = principal.obtener_estado_para_backup()
            barcos_registrados.update(estado["barcos_registrados"])
            flota_formada = estado["flota_formada"]
            estado_flota.update(estado["estado_flota"])
            eventos_lamport.extend(estado["eventos_lamport"])
            solicitudes_recibidas.clear()
            for k, v in estado.get("solicitudes_recibidas", {}).items():
                k_int = int(k) if str(k).isdigit() else k
                solicitudes_recibidas[k_int] = v
            solicitud_id_counter = max(solicitud_id_counter, estado.get("solicitud_id_counter", 0))
            historial_comunicacion.extend(estado["historial_comunicacion"])
            soy_principal = False
            ultimo_latido_principal = time.time()
            principal_encontrada = True
            logging.info(f"Central {CENTRAL_ID}: Central {rol_remoto.get('id')} es Principal activa. "
                         f"Arrancando como RESPALDO.")
    except Exception:
        pass

    if not principal_encontrada:
        logging.info(f"Central {CENTRAL_ID}: no pude confirmar una Principal activa. "
                     f"Disputo el rol por elección de anillo.")
        ultimo_latido_principal = time.time()  # margen antes de que el vigilante dispare otra elección
        _iniciar_eleccion_central("Arranque sin Principal activa confirmada")

    # Iniciar hilos
    threading.Thread(target=hilo_vigilante_central, daemon=True).start()
    threading.Thread(target=hilo_polling, args=(NS_HOST, NS_PORT), daemon=True).start()
    threading.Thread(target=iniciar_http, args=(args.http_port,), daemon=True).start()
    threading.Thread(target=cmd_loop, args=(NS_HOST, NS_PORT), daemon=True).start()

    logging.info(f"Central {CENTRAL_ID} Daemon escuchando en {uri} | Rol inicial: "
                 f"{'PRINCIPAL' if soy_principal else 'RESPALDO'}")
    try:
        daemon.requestLoop()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
