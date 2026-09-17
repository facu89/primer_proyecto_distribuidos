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

# --- Zona operativa marítima ---
# Caja de coordenadas (lat/lon) en aguas abiertas del Atlántico Sur, lejos
# de la costa de Sudamérica (~-35° de longitud) y de África (~-10° a +15°).
# Se usa únicamente para que el movimiento aleatorio de los barcos no los
# termine llevando a navegar sobre tierra firme.
ZONA_LAT_MIN = -28.0
ZONA_LAT_MAX = -8.0
ZONA_LON_MIN = -28.0
ZONA_LON_MAX = -12.0

def limitar_a_zona_oceanica(lat, lon, rumbo):
    """
    Discrimina si (lat, lon) cae fuera de la zona operativa marítima.
    Si se sale, recorta la posición al borde de la caja y "refleja" el
    rumbo (como un rebote) para que el próximo paso aleje al barco de
    tierra firme. Si está dentro de la zona, devuelve todo sin cambios.
    """
    nuevo_rumbo = rumbo

    if lon < ZONA_LON_MIN or lon > ZONA_LON_MAX:
        lon = max(ZONA_LON_MIN, min(ZONA_LON_MAX, lon))
        nuevo_rumbo = (360 - nuevo_rumbo) % 360  # rebote este-oeste

    if lat < ZONA_LAT_MIN or lat > ZONA_LAT_MAX:
        lat = max(ZONA_LAT_MIN, min(ZONA_LAT_MAX, lat))
        nuevo_rumbo = (180 - nuevo_rumbo) % 360  # rebote norte-sur

    return lat, lon, nuevo_rumbo

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
        self.ultimos_contactos = {} # id -> timestamp del último mensaje/posición recibido
        self.solicitudes_en_vuelo = {} # {solicitud_id: barco_origen} tracking de pedidos a Central

    # --- Métodos de ciclo de vida e inicialización ---
    
    def registrar_en_central(self, mi_uri):
        """Se conecta a la central para registrarse al inicio."""
        try:
            ns_host, ns_port = self.central_uri.split(":")
            ns = Pyro5.api.locate_ns(host=ns_host, port=int(ns_port))
            ns.register(f"flota.barco.{self.id}", mi_uri)
            
            # Buscar a la central
            central_uri = ns.lookup("flota.central")
            central = Pyro5.api.Proxy(central_uri)
            central._pyroTimeout = 5.0
            resultado = central.registrar_barco(self.id, self.nombre, self.host, self.port, self.latitud, self.longitud, self.rumbo, self.velocidad)
            logging.info(f"Registrado exitosamente en la Central {self.central_uri}")

            # Si la flota ya estaba en marcha, reincorporarse de inmediato al anillo
            if isinstance(resultado, dict) and resultado.get("flota_formada"):
                topologia = resultado["topologia"]
                primario_id = resultado["primario_id"]
                self.configurar_flota(topologia, primario_id)
                logging.info(f"Reincorporado exitosamente a la flota activa. Primario actual: Barco {primario_id}")
                
                # Si mi ID es mayor que el primario actual, iniciar elección para restablecer liderazgo legítimo
                if self.id > primario_id:
                    logging.info(f"Mi ID ({self.id}) es mayor que el primario actual ({primario_id}). Iniciando elección...")
                    time.sleep(1)
                    threading.Thread(target=self._iniciar_eleccion, args=("Reincorporación de nodo líder con mayor ID",), daemon=True).start()
        except Exception as e:
            logging.error(f"Error al registrarse en la Central: {e}")
            sys.exit(1)

    def configurar_flota(self, topologia, primario_id):
        """Invocado por la Central al hacer 'formar flota' o al reincorporarse."""
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
                    self.estado_flota[p_id] = {
                        "id": p_id,
                        "latitud": self.latitud if p_id == self.id else 0.0,
                        "longitud": self.longitud if p_id == self.id else 0.0,
                        "rumbo": self.rumbo if p_id == self.id else 0.0,
                        "velocidad": self.velocidad if p_id == self.id else 0.0,
                        "activo": True,
                        "es_primario": (p_id == self.id),
                        "lamport": reloj_logico
                    }
                    self.ultimos_contactos[p_id] = time.time()
            
        logging.info(f"Flota configurada. Primario actual: {primario_id}. Peers: {len(topologia)}")
        registrar_evento("Flota configurada por la Central", reloj_logico)
        return True

    def actualizar_topologia(self, nueva_topologia, reincorporado_id=None):
        """Invocado por la Central cuando un nodo se reincorpora al anillo."""
        with lock:
            self.topologia = nueva_topologia
            for p_id, p_uri in nueva_topologia:
                self.peers_uris[p_id] = p_uri
                if p_id not in self.estado_flota:
                    # Nunca lo había visto: lo doy de alta como activo (recién se une al anillo)
                    self.estado_flota[p_id] = {
                        "id": p_id,
                        "latitud": 0.0,
                        "longitud": 0.0,
                        "rumbo": 0.0,
                        "velocidad": 0.0,
                        "activo": True,
                        "es_primario": (p_id == self.primario_id),
                        "lamport": reloj_logico
                    }
                    self.ultimos_contactos[p_id] = time.time()
                elif p_id == reincorporado_id:
                    # Es el barco que Central avisa que se reincorporó de verdad
                    self.estado_flota[p_id]["activo"] = True
                    self.ultimos_contactos[p_id] = time.time()
                # A los demás barcos ya conocidos no les toco su estado de actividad acá:
                # que lo decida únicamente el heartbeat real de cada uno, no el solo hecho
                # de aparecer en esta lista de topología (si no, "resucita" a barcos muertos
                # cada vez que otro distinto se reincorpora).

        logging.info(f"Topología del anillo actualizada: Barco {reincorporado_id} se reincorporó. Nodos: {len(nueva_topologia)}")
        if self.es_primario:
            self._replicar_estado(reloj_logico)
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
            raise Exception(f"Barco {self.id} no es el primario")
        with lock:
            for b_id, b_data in self.estado_flota.items():
                b_data["es_primario"] = (b_id == self.id)
            return dict(self.estado_flota)

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
            ns_host, ns_port = self.central_uri.split(":")
            ns = Pyro5.api.locate_ns(host=ns_host, port=int(ns_port))
            central_uri = ns.lookup("flota.central")
            central = Pyro5.api.Proxy(central_uri)
            central._pyroTimeout = 3.0
            resp = central.recibir_solicitud(accion, datos, barco_origen, lamport)
            sol_id = resp.get("id") if isinstance(resp, dict) else None
            if sol_id is not None:
                with lock:
                    self.solicitudes_en_vuelo[sol_id] = barco_origen
                logging.info(f"Solicitud #{sol_id} '{accion}' (origen: Barco {barco_origen}) registrada en Central. Esperando resolución...")
                registrar_evento(f"Acción #{sol_id} '{accion}' elevada a la Central", lamport)
            else:
                registrar_evento(f"Acción '{accion}' elevada a la Central", lamport)
        except Exception as e:
            logging.error(f"Error elevando acción a la central: {e}")

    def recibir_resolucion_solicitud(self, solicitud_id, accion, barco_origen, decision):
        """Invocado por la Central sobre el Primario cuando el operador resuelve una solicitud."""
        global reloj_logico
        with lock:
            reloj_logico += 1
            l_actual = reloj_logico
            self.solicitudes_en_vuelo.pop(solicitud_id, None)

        msg = f"Resolución de Central para Solicitud #{solicitud_id} '{accion}' (Barco {barco_origen}): [{decision.upper()}]"
        registrar_evento(msg, l_actual)

        if barco_origen == self.id:
            self.notificar_resolucion(accion, decision, solicitud_id)
            return {"entregado": True}
        else:
            destino_uri = self.peers_uris.get(barco_origen)
            if not destino_uri:
                try:
                    ns_host, ns_port = self.central_uri.split(":")
                    ns = Pyro5.api.locate_ns(host=ns_host, port=int(ns_port))
                    destino_uri = ns.lookup(f"flota.barco.{barco_origen}")
                except Exception:
                    destino_uri = None

            if destino_uri:
                try:
                    proxy = Pyro5.api.Proxy(destino_uri)
                    proxy._pyroTimeout = 3.0
                    proxy.notificar_resolucion(accion, decision, solicitud_id)
                    logging.info(f"Resolución de #{solicitud_id} notificada al Barco {barco_origen}")
                    return {"entregado": True}
                except Exception as e:
                    logging.error(f"No se pudo notificar resolución al Barco {barco_origen}: {e}")
                    registrar_evento(f"Fallo de entrega de Solicitud #{solicitud_id} a Barco {barco_origen}: {e}", l_actual)
                    return {"entregado": False, "motivo": f"nodo no disponible ({e})"}
            else:
                logging.warning(f"No se encontró URI para notificar al Barco {barco_origen}")
                registrar_evento(f"Fallo de entrega de Solicitud #{solicitud_id} a Barco {barco_origen}: URI no encontrada", l_actual)
                return {"entregado": False, "motivo": "nodo no registrado o URI no encontrada"}

    def notificar_resolucion(self, accion, decision, solicitud_id=None):
        """Invocado por el Primario hacia este barco cuando la Central resolvió su solicitud."""
        global reloj_logico
        with lock:
            reloj_logico += 1
            l_actual = reloj_logico

        sol_str = f" #{solicitud_id}" if solicitud_id else ""
        estado_str = decision.upper()
        msg = f"Respuesta de Central para solicitud{sol_str} '{accion}': [{estado_str}]"

        registrar_evento(msg, l_actual)
        print(f"\n>>> [{estado_str}] Solicitud{sol_str} '{accion}' ha sido {decision.lower()} por la Central <<<\n> ", end="", flush=True)
        return True

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
            self.ultimos_contactos[barco_id] = time.time()
            if barco_id not in self.estado_flota:
                self.estado_flota[barco_id] = {}
            
            estaba_inactivo = (self.estado_flota[barco_id].get("activo") is False)

            self.estado_flota[barco_id].update({
                "id": barco_id,
                "latitud": lat,
                "longitud": lon,
                "rumbo": rumbo,
                "velocidad": vel,
                "activo": True,
                "es_primario": (barco_id == self.id),
                "lamport": l_actual
            })
            if estaba_inactivo:
                logging.info(f"Barco {barco_id} ha restablecido contacto. Estado: ACTIVO.")
                registrar_evento(f"Barco {barco_id} reconectado / activo", l_actual)

            # Actualizo mi propio estado si soy yo
            if barco_id == self.id:
                self.latitud = lat
                self.longitud = lon
                self.rumbo = rumbo
                self.velocidad = vel
        
        self._replicar_estado(l_actual)

    # --- Heartbeat y Elección en Anillo ---
    
    def heartbeat(self, barco_origen=None):
        """Responde True si está vivo y actualiza último contacto."""
        if barco_origen is not None:
            with lock:
                self.ultimos_contactos[barco_origen] = time.time()
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
                nodo._pyroTimeout = 3.0
                getattr(nodo, metodo)(*args)
                return p_id
            except Exception as e:
                logging.debug(f"Nodo {p_id} no responde al anillo ({e}), saltando...")
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
            if self.es_primario:
                self._asumir_como_primario()
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
                threading.Thread(target=self._mandar_al_siguiente, args=("recibir_mensaje_eleccion", "COORDINADOR", [ganador], origen, numero), daemon=True).start()
            else:
                with lock:
                    self.en_eleccion = True
                participantes.append(self.id)
                threading.Thread(target=self._mandar_al_siguiente, args=("recibir_mensaje_eleccion", "ELECCION", participantes, origen, numero), daemon=True).start()
                
        elif tipo == "COORDINADOR":
            ganador = participantes[0]
            with lock:
                repetido = (self.ultimo_anuncio == (ganador, origen, numero))
                self.ultimo_anuncio = (ganador, origen, numero)
                
            if repetido: return True # Dio la vuelta
            
            if self._proclamar(ganador):
                if ganador == self.id:
                    self._asumir_como_primario()
                else:
                    logging.info(f"Nuevo primario establecido: {ganador}")
                    
            if self.id != origen:
                threading.Thread(target=self._mandar_al_siguiente, args=("recibir_mensaje_eleccion", "COORDINADOR", [ganador], origen, numero), daemon=True).start()
        return True

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
        with lock:
            global reloj_logico
            reloj_logico += 1
            l_act = reloj_logico
            self.es_primario = True
            self.primario_id = self.id
            
            # Reconstruir estado_flota si estuviera incompleto (por ejemplo, si no era
            # primario cuando me reincorporé y nunca llegué a tener la tabla completa).
            # Uso la topología (que siempre conozco) para no perder de la tabla a barcos
            # que en realidad siguen vivos, solo porque yo no tenía guardado su estado.
            if self.id not in self.estado_flota:
                self.estado_flota[self.id] = self.obtener_estado()
            for p_id, _ in self.topologia:
                if p_id not in self.estado_flota:
                    self.estado_flota[p_id] = {
                        "id": p_id,
                        "latitud": 0.0,
                        "longitud": 0.0,
                        "rumbo": 0.0,
                        "velocidad": 0.0,
                        "activo": True,
                        "es_primario": False,
                        "lamport": reloj_logico,
                    }
                    self.ultimos_contactos[p_id] = time.time()  # todavía sin evidencia de que esté muerto

            ahora = time.time()
            # Marcar al primario caído y nodos que no respondieron como inactivos
            for p_id in list(self.estado_flota.keys()):
                if p_id != self.id:
                    ultimo = self.ultimos_contactos.get(p_id, 0)
                    if ahora - ultimo > 6.0:
                        self.estado_flota[p_id]["activo"] = False
                    self.estado_flota[p_id]["es_primario"] = False
            
            self.estado_flota[self.id]["activo"] = True
            self.estado_flota[self.id]["es_primario"] = True
            self.estado_flota[self.id]["lamport"] = l_act
            self.ultimos_contactos[self.id] = ahora
            
        registrar_evento(f"Asumido rol de PRIMARIO / Coordinador", l_act)
        self._replicar_estado(l_act)

        # Actualizar en NS y notificar a Central
        try:
            ns_host, ns_port = self.central_uri.split(":")
            ns = Pyro5.api.locate_ns(host=ns_host, port=int(ns_port))
            try:
                ns.remove("flota.primario")
            except Exception: pass
            
            mi_uri = self.peers_uris.get(self.id)
            if mi_uri:
                ns.register("flota.primario", mi_uri)
                
            central_uri = ns.lookup("flota.central")
            central = Pyro5.api.Proxy(central_uri)
            central._pyroTimeout = 2.0
            central.notificar_nuevo_primario(self.id)
            logging.info(f"Central notificada exitosamente del nuevo primario {self.id}")
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
    """Vigila heartbeats y latidos del primario y de los backups."""
    TIMEOUT_PRIMARIO = 6.0
    TIMEOUT_BACKUP = 9.0
    while True:
        time.sleep(1)
        if not barco.topologia: continue
        
        if barco.es_primario:
            ahora = time.time()
            hubo_cambio = False
            with lock:
                global reloj_logico
                for p_id, p_uri in barco.topologia:
                    if p_id == barco.id: continue
                    ultimo = barco.ultimos_contactos.get(p_id, ahora)
                    if ahora - ultimo > TIMEOUT_BACKUP:
                        if p_id in barco.estado_flota and barco.estado_flota[p_id].get("activo", True):
                            barco.estado_flota[p_id]["activo"] = False
                            reloj_logico += 1
                            barco.estado_flota[p_id]["lamport"] = reloj_logico
                            l_act = reloj_logico
                            hubo_cambio = True
                            logging.warning(f"TIMEOUT: Barco {p_id} no responde hace {ahora - ultimo:.1f}s. Marcado como INACTIVO/HUNDIDO.")
                            registrar_evento(f"Barco {p_id} sin senal, marcado como INACTIVO", l_act)
            if hubo_cambio:
                barco._replicar_estado(reloj_logico)
        else:
            # Backup vigila al primario
            with lock:
                silencio = time.time() - barco.ultimo_latido
                en_eleccion = barco.en_eleccion
                
            if en_eleccion: continue
            
            if silencio > TIMEOUT_PRIMARIO:
                barco._iniciar_eleccion("El primario no responde")
            else:
                # Enviar heartbeat al primario informando origen
                if barco.primario_id and barco.primario_id in barco.peers_uris:
                    try:
                        prim = Pyro5.api.Proxy(barco.peers_uris[barco.primario_id])
                        prim._pyroTimeout = 1.0
                        prim.heartbeat(barco.id)
                    except Exception:
                        pass # El silencio se acumulará y disparará elección

# --- CLI y Main ---

def cmd_loop(barco):
    time.sleep(2)
    print("\nComandos disponibles: ataque_aereo, solicitar_suministros, reportar_avistamiento, evacuacion_medica, solicitar_refuerzos, ubicaciones")
    while True:
        try:
            cmd = input("> ").strip().lower()
            if not cmd: continue

            if cmd in ["ataque_aereo", "solicitar_suministros", "reportar_avistamiento", "evacuacion_medica", "solicitar_refuerzos"]:
                # Generamos datos dummy para el ejemplo
                datos = {"coordenadas": (barco.latitud, barco.longitud), "info": "Urgent"}
                barco.solicitar_accion_local(cmd, datos)
            elif cmd in ["ubicaciones", "flota"]: #Ubicaciones desde los barcos
                if not barco.estado_flota:
                    print("Aún no hay datos de la flota.")
                else:
                    print(f"\n{'ID':<5}{'ACTIVO':<8}{'LAT':<10}{'LON':<10}{'RUMBO':<8}{'VEL':<6}")
                    for b_id, b in sorted(barco.estado_flota.items()):
                        estado = "SI" if b.get("activo") else "NO"
                        marca = " (YO)" if b_id == barco.id else (" (PRIMARIO)" if b.get("es_primario") else "")
                        print(f"{b_id:<5}{estado:<8}{b.get('latitud', 0):<10.4f}{b.get('longitud', 0):<10.4f}{b.get('rumbo', 0):<8.1f}{b.get('velocidad', 0):<6.1f}{marca}")
            else:
                print("Comando no reconocido.")
        except (EOFError, KeyboardInterrupt):
            break

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("id", type=int)
    parser.add_argument("--nombre", required=True)
    parser.add_argument("--host", default=obtener_ip_local(),
                        help="IP en la que escucha este barco (default: IP de red local)")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--central", required=True, help="IP:Port del Name Server en la Central")
    parser.add_argument("--lat", type=float, default=0.0)
    parser.add_argument("--lon", type=float, default=0.0)
    parser.add_argument("--rumbo", type=float, default=0.0)
    parser.add_argument("--vel", type=float, default=0.0)
    args = parser.parse_args()

    if args.host in ("0.0.0.0", ""):
        args.host = obtener_ip_local()

    ns_host, ns_port = args.central.split(":")
    Pyro5.config.NS_HOST = ns_host
    Pyro5.config.NS_PORT = int(ns_port)

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
