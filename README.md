# Sistema Distribuido de Comunicación de Flotas Navales

Sistema distribuido con arquitectura Primario-Backup (Remote-Write) usando Pyro5, replicación sincrónica, relojes de Lamport, elección en anillo y un dashboard web estilo radar militar.

## Arquitectura y Requerimientos Implementados

1. **Temática**: Sistema de comunicación de flotas navales.
2. **Nodos**: Clúster de N barcos, N Centrales (Principal + Respaldos) y un Name Server independiente.
3. **Arquitectura Primario-Backup (Remote-Write)**: aplicada dos veces:
   - Entre los **barcos**: uno es Primario y atiende a la Central; los demás son Backups.
   - Entre las **Centrales**: una es Principal (activa) y las demás son de Respaldo (pasivas, solo clonan el estado).
4. **RPC exclusivo con Pyro5**: Comunicación entre nodos y cliente resuelta vía invocaciones remotas de Pyro5.
5. **Replicación Sincrónica**: El Barco Primario envía los datos actualizados a los Backups; la Central Principal hace lo mismo con las Centrales de Respaldo.
6. **Relojes Lógicos de Lamport**: Cada interacción remota transporta y actualiza un reloj de Lamport.
7. **Algoritmo de Elección en Anillo (Ring Election)**: Topología lógica circular, tanto entre barcos como entre Centrales. Gana el nodo de mayor ID.
8. **Name Server independiente**: corre en su propio proceso (`nameserver.py`), no depende de ninguna Central.
9. **Manejo de Excepciones**: Detección de caída por heartbeats/timeouts, tanto de barcos como de Centrales.
10. **Dashboard Web**: Radar de estilo militar (Leaflet). Si la Central que lo sirve se cae, el dashboard detecta la falta de respuesta, muestra un aviso y redirige solo al navegador hacia el dashboard de la nueva Central Principal.

## Requisitos de Instalación

Asegúrate de contar con Python 3.8+ instalado.

```bash
pip install Pyro5
```

## Guía de Ejecución

El sistema puede ejecutarse en una **única máquina** (usando múltiples terminales) o **distribuido entre varias computadoras** en la misma red LAN/Wi-Fi.

> **Importante para ejecución multi-máquina (Red Local):**
> 1. Asegúrate de que las máquinas estén en la misma red y puedan hacerse `ping`.
> 2. En Windows, verifica que el **Firewall de Windows** no bloquee Python (o permite conexiones entrantes en el perfil de red Privada para el Name Server: `9090`, las Centrales: `8080`, `8081`, `5000`, `5001`, y los barcos: `9001`, `9002`, etc.).
> 3. `nameserver.py`, `central.py` y `barco.py` auto-detectan la IP de red local de la máquina de forma automática.
> 4. El **Name Server siempre arranca primero**, y debe seguir corriendo mientras existan Centrales o barcos activos (si el Name Server muere, hay que reiniciarlo y volver a levantar los demás procesos).

---

### Opción A: Ejecución en Múltiples Computadoras (Distribuida Real)

> **Forma rápida:** usar los scripts `maquina_nameserver.bat`, `maquina_central1.bat`,
> `maquina_central2.bat`, `maquina_barco1.bat`, `maquina_barco2.bat` y `maquina_barco3.bat`.
> Cada uno se corre en la máquina que le corresponde; solo hay que abrir el archivo y
> cambiar las 2-3 líneas de IP marcadas arriba de todo (la de `NS_HOST` tiene que ser
> la **misma en los seis** archivos: la IP de la máquina que corre el Name Server).

#### 1. Name Server (en cualquier máquina, arranca primero):
```bash
python nameserver.py --host 192.168.1.53
```
**Importante:** pasar siempre `--host` con la IP real de la máquina (`ipconfig`), explícita.
Si se corre sin `--host` y la auto-detección falla o devuelve `127.0.0.1`, el Name Server
queda escuchando solo en loopback y **nadie en otra máquina lo va a poder encontrar**,
aunque estén en la misma red.

#### 2. Centrales (Computadora 1 = Principal, Computadora 2 = Respaldo):
```bash
# Computadora 1 (IP 192.168.1.53):
python central.py 1 --port 8080 --ns-host 192.168.1.53 --ns-port 9090 --http-port 5000 --peers-http 2:192.168.1.54:5000

# Computadora 2 (IP 192.168.1.54):
python central.py 2 --port 8080 --ns-host 192.168.1.53 --ns-port 9090 --http-port 5000 --peers-http 1:192.168.1.53:5000
```
*(La primera Central que arranca se proclama Principal; la que arranca después detecta que ya hay una activa y queda como Respaldo. `--peers-http` es solo para que el dashboard sepa a qué otra Central redirigir si la actual se cae. `--ns-host` tiene que ser la misma IP en las dos.)*

#### 3. Barcos (una o más computadoras, apuntando al Name Server):
```bash
# Barco 1:
python barco.py 1 --nombre "Destructor Alfa" --port 9001 --central 192.168.1.53:9090 --lat -34.6 --lon -58.4 --rumbo 45 --vel 14.5

# Barco 2:
python barco.py 2 --nombre "Fragata Beta" --port 9002 --central 192.168.1.53:9090 --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0

# Barco 3:
python barco.py 3 --nombre "Corbeta Gamma" --port 9003 --central 192.168.1.53:9090 --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0
```

#### 4. Formar la Flota:
Una vez que todos los barcos figuren registrados, en la consola de la **Central Principal** escribí:
```bash
> formar flota
```

---

### Opción B: Ejecución Rápida en una Sola Computadora (Localhost)

Puedes usar el script automatizado:
```bash
iniciar_demo.bat
```
O abrir 6 terminales manualmente:

- **Terminal 1 (Name Server):**
  ```bash
  python nameserver.py --host 127.0.0.1
  ```
- **Terminal 2 (Central 1 - Principal):**
  ```bash
  python central.py 1 --host 127.0.0.1 --port 8080 --ns-host 127.0.0.1 --ns-port 9090 --http-port 5000 --peers-http 2:127.0.0.1:5001
  ```
- **Terminal 3 (Central 2 - Respaldo):**
  ```bash
  python central.py 2 --host 127.0.0.1 --port 8081 --ns-host 127.0.0.1 --ns-port 9090 --http-port 5001 --peers-http 1:127.0.0.1:5000
  ```
- **Terminal 4 (Barco 1):**
  ```bash
  python barco.py 1 --nombre "Destructor Alfa" --host 127.0.0.1 --port 9001 --central 127.0.0.1:9090 --lat -34.6 --lon -58.4 --rumbo 45 --vel 14.5
  ```
- **Terminal 5 (Barco 2):**
  ```bash
  python barco.py 2 --nombre "Fragata Beta" --host 127.0.0.1 --port 9002 --central 127.0.0.1:9090 --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0
  ```
- **Terminal 6 (Barco 3):**
  ```bash
  python barco.py 3 --nombre "Corbeta Gamma" --host 127.0.0.1 --port 9003 --central 127.0.0.1:9090 --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0
  ```

---

### Paso Siguiente: Dashboard y Monitoreo

### Paso 4: Visualizar el Dashboard

Abre un navegador y dirígete al dashboard de la Central Principal (Central 1 en el ejemplo):

```
http://localhost:5000
```

(La Central de Respaldo también sirve su propio dashboard, de solo lectura, en `http://localhost:5001`.)

### Paso 5: Probar Comandos

En cualquier terminal de barco, prueba enviar solicitudes:

```bash
> ataque_aereo
> solicitar_suministros
```
Verifica que los mensajes de Lamport avanzan y que la solicitud llega a la Central a través del Primario.

En la terminal de cualquier Central, escribí `rol` para ver si es PRINCIPAL o RESPALDO.

### Prueba de Caída de un Barco (Failover del Primario)

1. Observa en la consola del dashboard/central qué barco es el primario.
2. En la terminal de ese barco, presiona `Ctrl + C` para cerrarlo.
3. Observa cómo los barcos restantes detectan la caída mediante heartbeats.
4. Se iniciará un proceso de elección (mensajes ELECCION / COORDINADOR).
5. El ganador se proclamará como nuevo primario, actualizará su rol y el sistema se recuperará automáticamente sin que la central caiga.

### Prueba de Caída de la Central Principal (Failover de Central)

1. Con el dashboard de la Central 1 abierto en el navegador (`http://localhost:5000`), presiona `Ctrl + C` en la terminal de la Central 1.
2. A los pocos segundos, la Central 2 detecta el silencio, arranca una elección en anillo y se proclama nueva Principal (queda registrada en el Name Server como `flota.central`).
3. El dashboard que tenías abierto deja de recibir respuesta, muestra el cartel **"CENTRAL CAÍDA"** y te redirige automáticamente a `http://localhost:5001` (Central 2, ahora Principal) apenas la detecta activa.
4. Los barcos, que buscan a la Central dinámicamente en el Name Server, siguen elevando sus solicitudes sin ningún cambio de configuración de su lado.
5. Si volvés a levantar la Central 1, se reincorpora como Respaldo (clona el estado de la Central 2) sin duplicar el rol de Principal.
