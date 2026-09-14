import argparse
import logging
import math
import random
import sys
import threading
import time
import Pyro5.api
import Pyro5.errors
import socket

# Configuración de logs
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')

# Reloj de Lamport y eventos
reloj_logico = 0
eventos = []
lock = threading.Lock()

def registrar_evento(descripcion, logico):
    timestamp_fisico = time.time()
    eventos.append((logico, timestamp_fisico, descripcion))
    logging.info(f"[L:{logico}] {descripcion}")

@Pyro5.api.expose
class BarcoServicio:
    def __init__(self, barco_id, nombre, host, port, lat, lon, rumbo, vel, central_uri):
        self.id = barco_id
        self.nombre = nombre
        self.host = host
        self.port = port
        self.latitud = lat
        self.longitud = lon
        self.rumbo = rumbo
        self.velocidad = vel
        self.activo = True
        self.es_primario = False
        
        self.central_uri = central_uri
        self.topologia = []  # Lista de peers (ID, URI) ordenada
        self.peers_uris = {} # ID -> URI directo
        self.primario_id = None

        self.ultimo_latido = time.time()
        self.en_eleccion = False
        self.ultimo_anuncio = None
        
        # Estado de toda la flota (solo el primario lo mantiene completo)
        self.estado_flota = {}

    # --- Métodos de ciclo de vida e inicialización ---
    
    def registrar_en_central(self, mi_uri):
        """Se conecta a la central para registrarse al inicio."""
        try:
            ns = Pyro5.api.locate_ns(host=self.central_uri.split(":")[0], port=int(self.central_uri.split(":")[1]))
            ns.register(f"flota.barco.{self.id}", mi_uri)
            
            # Buscar a la central
            central = Pyro5.api.Proxy("PYN:flota.central")
            central.registrar_barco(self.id, self.nombre, self.host, self.port, self.latitud, self.longitud, self.rumbo, self.velocidad)
            logging.info(f"Registrado exitosamente en la Central {self.central_uri}")
        except Exception as e:
            logging.error(f"Error al registrarse en la Central: {e}")
            sys.exit(1)

    def configurar_flota(self, topologia, primario_id):
        """Invocado por la Central al hacer 'formar flota'."""
        with lock:
            global reloj_logico
            reloj_logico += 1
            self.topologia = topologia
            for p_id, p_uri in topologia:
                self.peers_uris[p_id] = p_uri
            self.primario_id = primario_id
            self.es_primario = (self.id == primario_id)
            self.ultimo_latido = time.time()
            
            # Inicializar estado_flota si soy el primario
            if self.es_primario:
                for p_id, p_uri in topologia:
                    self.estado_flota[p_id] = {"activo": True, "lamport": reloj_logico}
            
        logging.info(f"Flota configurada. Primario actual: {primario_id}. Peers: {len(topologia)}")
        registrar_evento("Flota configurada por la Central", reloj_logico)
        return True

    # --- Operaciones de Estado y Lamport ---
    
    def _actualizar_lamport(self, remoto_lamport):
        global reloj_logico
        with lock:
            reloj_logico = max(reloj_logico, remoto_lamport) + 1
            return reloj_logico

    def obtener_estado(self):
        """Devuelve el estado local del barco."""
        global reloj_logico
        return {
            "id": self.id,
            "nombre": self.nombre,
            "latitud": self.latitud,
            "longitud": self.longitud,
            "rumbo": self.rumbo,
            "velocidad": self.velocidad,
            "activo": self.activo,
            "es_primario": self.es_primario,
            "lamport": reloj_logico
        }

    def obtener_estado_flota(self):
        """Invocado por la Central para leer todo el estado (solo si es primario)."""
        if not self.es_primario:
            raise Exception("No soy el primario")
        return self.estado_flota

    def obtener_log_eventos(self):
        return eventos

    def obtener_primario_actual(self):
        return self.primario_id

    # --- Acciones (Barco -> Primario -> Central) ---
    
    def solicitar_accion_local(self, accion, datos):
        """El operador local pide una acción."""
        global reloj_logico
        with lock:
            reloj_logico += 1
            l_actual = reloj_logico
        registrar_evento(f"Solicitando acción '{accion}'", l_actual)
        
        if self.es_primario:
            self._elevar_solicitud_central(accion, datos, self.id, l_actual)
        else:
            if not self.primario_id or self.primario_id not in self.peers_uris:
                logging.error("No hay primario conocido para enviar la solicitud.")
                return
            
            try:
                primario_uri = self.peers_uris[self.primario_id]
                primario = Pyro5.api.Proxy(primario_uri)
                primario._pyroTimeout = 2.0
                primario.solicitar_accion(accion, datos, self.id, l_actual)
            except Exception as e:
                logging.error(f"Error enviando acción al primario: {e}")
                
    def solicitar_accion(self, accion, datos, barco_origen, remoto_lamport):
        """Recibe una acción de otro barco (solo si soy primario)."""
        l_actual = self._actualizar_lamport(remoto_lamport)
        registrar_evento(f"Recibida solicitud de acción '{accion}' del barco {barco_origen}", l_actual)
        if self.es_primario:
            self._elevar_solicitud_central(accion, datos, barco_origen, l_actual)
            
    def _elevar_solicitud_central(self, accion, datos, barco_origen, lamport):
        """El primario envía la solicitud a la central."""
        try:
            central = Pyro5.api.Proxy("PYN:flota.central")
            central._pyroTimeout = 2.0
            central.recibir_solicitud(accion, datos, barco_origen, lamport)
            registrar_evento(f"Acción '{accion}' elevada a la Central", lamport)
        except Exception as e:
            logging.error(f"Error elevando acción a la central: {e}")

    # --- Replicación (Remote-Write) ---
    
    def _replicar_estado(self, l_actual):
        """Envía el estado_flota actual a todos los backups."""
        for p_id, p_uri in self.topologia:
            if p_id == self.id: continue
            
            try:
                backup = Pyro5.api.Proxy(p_uri)
                backup._pyroTimeout = 1.0
                backup.replicar(self.estado_flota, l_actual)
            except Exception:
                pass # Si no contesta, lo ignoramos por ahora (el heartbeat se encarga)

    def replicar(self, estado_flota, remoto_lamport):
        """Invocado por el primario para actualizar a este backup."""
        l_actual = self._actualizar_lamport(remoto_lamport)
        with lock:
            self.estado_flota = estado_flota
            self.ultimo_latido = time.time() # Cuenta como latido
        # registrar_evento(f"Estado replicado recibido del primario", l_actual) # Mucho spam
        return True

    def actualizar_posicion(self, barco_id, lat, lon, rumbo, vel, remoto_lamport):
        """Invocado por un barco hacia el primario para notificar su movimiento."""
        if not self.es_primario: return
        
        l_actual = self._actualizar_lamport(remoto_lamport)
        with lock:
            if barco_id not in self.estado_flota:
                self.estado_flota[barco_id] = {}
            self.estado_flota[barco_id].update({
                "latitud": lat,
                "longitud": lon,
                "rumbo": rumbo,
                "velocidad": vel,
                "activo": True,
                "lamport": l_actual
            })
            # Actualizo mi propio estado si soy yo
            if barco_id == self.id:
                self.latitud = lat
                self.longitud = lon
                self.rumbo = rumbo
                self.velocidad = vel
        
        self._replicar_estado(l_actual)

    # --- Heartbeat y Elección en Anillo ---
    
    def heartbeat(self):
        """Responde True si está vivo."""
        return True

    def _mandar_al_siguiente(self, metodo, *args):
        """Recorre el anillo enviando un mensaje al primer nodo que responda."""
        if not self.topologia: return None
        
        mi_pos = 0
        for i, (p_id, _) in enumerate(self.topologia):
            if p_id == self.id:
                mi_pos = i
                break
                
        for salto in range(1, len(self.topologia)):
            p_id, p_uri = self.topologia[(mi_pos + salto) % len(self.topologia)]
            try:
                nodo = Pyro5.api.Proxy(p_uri)
                nodo._pyroTimeout = 1.5
                getattr(nodo, metodo)(*args)
                return p_id
            except Exception:
                logging.debug(f"Nodo {p_id} no responde al anillo, saltando...")
        return None

    def _iniciar_eleccion(self, motivo):
        with lock:
            if self.en_eleccion: return
            self.en_eleccion = True
            
        numero = random.randint(1000, 9999)
        logging.info(f"Iniciando elección ({motivo}).")
        a_quien = self._mandar_al_siguiente("recibir_mensaje_eleccion", "ELECCION", [self.id], self.id, numero)
        
        if a_quien is None:
            self._proclamar(self.id)
            logging.info("No hay más nodos en el anillo, soy el primario absoluto.")

    def recibir_mensaje_eleccion(self, tipo, participantes, origen, numero):
        if tipo == "ELECCION":
            if self.id in participantes:
                ganador = max(participantes)
                logging.info(f"Elección {numero} completada. Gana {ganador}.")
                if self._proclamar(ganador) and ganador == self.id:
                    self._asumir_como_primario()
                with lock:
                    self.ultimo_anuncio = (ganador, origen, numero)
                self._mandar_al_siguiente("recibir_mensaje_eleccion", "COORDINADOR", [ganador], origen, numero)
            else:
                with lock:
                    self.en_eleccion = True
                participantes.append(self.id)
                self._mandar_al_siguiente("recibir_mensaje_eleccion", "ELECCION", participantes, origen, numero)
                
        elif tipo == "COORDINADOR":
            ganador = participantes[0]
            with lock:
                repetido = (self.ultimo_anuncio == (ganador, origen, numero))
                self.ultimo_anuncio = (ganador, origen, numero)
                
            if repetido: return # Dio la vuelta
            
            if self._proclamar(ganador):
                if ganador == self.id:
                    self._asumir_como_primario()
                else:
                    logging.info(f"Nuevo primario establecido: {ganador}")
                    
            if self.id != origen:
                self._mandar_al_siguiente("recibir_mensaje_eleccion", "COORDINADOR", [ganador], origen, numero)

    def _proclamar(self, ganador):
        with lock:
            cambio = (self.primario_id != ganador)
            self.primario_id = ganador
            self.es_primario = (self.id == ganador)
            self.en_eleccion = False
            self.ultimo_latido = time.time()
        return cambio

    def _asumir_como_primario(self):
        logging.info("Asumiendo el rol de PRIMARIO.")
        # Reconstruir estado_flota si no lo tengo completo
        if not self.estado_flota:
             self.estado_flota = {self.id: self.obtener_estado()}
        # Actualizar en NS y notificar a Central
        try:
            ns = Pyro5.api.locate_ns(host=self.central_uri.split(":")[0], port=int(self.central_uri.split(":")[1]))
            try:
                ns.remove("flota.primario")
            except Exception: pass
            
            mi_uri = self.peers_uris.get(self.id)
            if mi_uri:
                ns.register("flota.primario", mi_uri)
                
            central = Pyro5.api.Proxy("PYN:flota.central")
            central._pyroTimeout = 2.0
            central.notificar_nuevo_primario(self.id)
        except Exception as e:
            logging.error(f"Error al notificar nuevo rol a la Central: {e}")

# --- Hilos en segundo plano ---

def hilo_movimiento(barco):
    """Simula el movimiento del barco cada 5 segundos."""
    while True:
        time.sleep(5)
        if not barco.topologia: continue # Aún no inició
        
        # Variación aleatoria de rumbo y velocidad
        barco.rumbo = (barco.rumbo + random.uniform(-5, 5)) % 360
        barco.velocidad = max(0.0, min(30.0, barco.velocidad + random.uniform(-1, 1)))
        
        # Actualizar lat/lon basado en velocidad (knots) y rumbo
        # 1 knot = 1 milla náutica por hora = 1/60 grados por hora aprox
        distancia_grados = (barco.velocidad / 3600) * 5 * (1/60)
        rad = math.radians(barco.rumbo)
        barco.latitud += math.cos(rad) * distancia_grados
        barco.longitud += math.sin(rad) * distancia_grados
        
        global reloj_logico
        with lock:
            reloj_logico += 1
            l_actual = reloj_logico
            
        if barco.es_primario:
            barco.actualizar_posicion(barco.id, barco.latitud, barco.longitud, barco.rumbo, barco.velocidad, l_actual)
        else:
            if barco.primario_id and barco.primario_id in barco.peers_uris:
                try:
                    prim = Pyro5.api.Proxy(barco.peers_uris[barco.primario_id])
                    prim._pyroTimeout = 1.0
                    prim.actualizar_posicion(barco.id, barco.latitud, barco.longitud, barco.rumbo, barco.velocidad, l_actual)
                except Exception:
                    pass

def hilo_vigilante(barco):
    """Vigila heartbeats y latidos del primario."""
    TIMEOUT = 6.0
    while True:
        time.sleep(1)
        if not barco.topologia: continue
        
        if barco.es_primario:
            # Primario verifica a los demás para marcarlos inactivos (opcional)
            pass
        else:
            # Backup vigila al primario
            with lock:
                silencio = time.time() - barco.ultimo_latido
                en_eleccion = barco.en_eleccion
                
            if en_eleccion: continue
            
            if silencio > TIMEOUT:
                barco._iniciar_eleccion("El primario no responde")
            else:
                # Enviar heartbeat al primario
                if barco.primario_id and barco.primario_id in barco.peers_uris:
                    try:
                        prim = Pyro5.api.Proxy(barco.peers_uris[barco.primario_id])
                        prim._pyroTimeout = 1.0
                        prim.heartbeat()
                    except Exception:
                        pass # El silencio se acumulará y disparará elección

# --- CLI y Main ---

def cmd_loop(barco):
    time.sleep(2)
    print("\nComandos disponibles: ataque_aereo, solicitar_suministros, reportar_avistamiento, evacuacion_medica, solicitar_refuerzos")
    while True:
        try:
            cmd = input("> ").strip().lower()
            if not cmd: continue
            
            if cmd in ["ataque_aereo", "solicitar_suministros", "reportar_avistamiento", "evacuacion_medica", "solicitar_refuerzos"]:
                # Generamos datos dummy para el ejemplo
                datos = {"coordenadas": (barco.latitud, barco.longitud), "info": "Urgent"}
                barco.solicitar_accion_local(cmd, datos)
            else:
                print("Comando no reconocido.")
        except (EOFError, KeyboardInterrupt):
            break

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("id", type=int)
    parser.add_argument("--nombre", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--central", required=True, help="IP:Port del Name Server en la Central")
    parser.add_argument("--lat", type=float, default=0.0)
    parser.add_argument("--lon", type=float, default=0.0)
    parser.add_argument("--rumbo", type=float, default=0.0)
    parser.add_argument("--vel", type=float, default=0.0)
    args = parser.parse_args()

    daemon = Pyro5.api.Daemon(host=args.host, port=args.port)
    barco = BarcoServicio(args.id, args.nombre, args.host, args.port, args.lat, args.lon, args.rumbo, args.vel, args.central)
    uri = daemon.register(barco, f"barco_{args.id}")
    
    # Iniciar hilos
    threading.Thread(target=hilo_movimiento, args=(barco,), daemon=True).start()
    threading.Thread(target=hilo_vigilante, args=(barco,), daemon=True).start()
    threading.Thread(target=cmd_loop, args=(barco,), daemon=True).start()
    
    # Registrarse en la central
    threading.Thread(target=barco.registrar_en_central, args=(uri,), daemon=True).start()

    logging.info(f"Barco {args.id} '{args.nombre}' escuchando en {uri}")
    try:
        daemon.requestLoop()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
