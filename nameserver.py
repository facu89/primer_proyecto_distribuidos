import argparse
import logging
import socket
import threading
import time
import traceback
import Pyro5.nameserver

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [NAMESERVER] %(message)s', datefmt='%H:%M:%S')

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

def hilo_latido(ns_daemon):
    """Loguea periódicamente que el Name Server sigue vivo y cuántos nombres tiene
    registrados, para poder distinguir de un vistazo 'sigue corriendo sin actividad'
    de 'el proceso murió' (la ventana, si el proceso murió, deja de imprimir esto)."""
    while True:
        time.sleep(20)
        try:
            cantidad = len(ns_daemon.nameserver.list())
            logging.info(f"Name Server activo. {cantidad} nombres registrados.")
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser(description="Name Server independiente para la flota naval.")
    parser.add_argument("--host", default=obtener_ip_local(),
                        help="IP en la que escucha el Name Server (default: IP de red local)")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    if args.host in ("0.0.0.0", ""):
        args.host = obtener_ip_local()

    logging.info(f"Iniciando Name Server independiente en {args.host}:{args.port}")
    logging.info("Este proceso debe quedar corriendo mientras existan barcos o centrales activos.")

    while True:
        try:
            uri, ns_daemon, bc_server = Pyro5.nameserver.start_ns(host=args.host, port=args.port)
            threading.Thread(target=hilo_latido, args=(ns_daemon,), daemon=True).start()
            ns_daemon.requestLoop()
            break  # requestLoop terminó solo (shutdown limpio), no reintentar
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"El Name Server se cayó por un error inesperado, reiniciándolo en 2s: {e}")
            logging.error(traceback.format_exc())
            time.sleep(2)

if __name__ == "__main__":
    main()
