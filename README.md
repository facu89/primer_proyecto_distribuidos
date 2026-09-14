# Sistema Distribuido de Comunicación de Flotas Navales

Sistema distribuido con arquitectura Primario-Backup (Remote-Write) usando Pyro5, replicación sincrónica, relojes de Lamport, elección en anillo y un dashboard web estilo radar militar.

## Arquitectura y Requerimientos Implementados

1. **Temática**: Sistema de comunicación de flotas navales.
2. **Nodos**: Clúster de N barcos y 1 Central (cliente/bootstrap).
3. **Arquitectura Primario-Backup (Remote-Write)**: La Central siempre realiza las peticiones (lectura y escritura remota) al Barco Primario.
4. **RPC exclusivo con Pyro5**: Comunicación entre nodos y cliente resuelta vía invocaciones remotas de Pyro5.
5. **Replicación Sincrónica**: El Barco Primario envía los datos actualizados a los nodos Backups.
6. **Relojes Lógicos de Lamport**: Cada interacción remota transporta y actualiza un reloj de Lamport.
7. **Algoritmo de Elección en Anillo (Ring Election)**: Topología lógica circular.
8. **Name Server Embebido**: La Central corre el NS en un hilo separado.
9. **Manejo de Excepciones**: Detección de caída por heartbeats.
10. **Dashboard Web**: Radar de estilo militar renderizado con HTML5 Canvas.

## Requisitos de Instalación

Asegúrate de contar con Python 3.8+ instalado.

```bash
pip install Pyro5
```

## Guía de Ejecución

El sistema puede ejecutarse en una **única máquina** (usando múltiples terminales) o **distribuido entre varias computadoras** en la misma red LAN/Wi-Fi.

> **Importante para ejecución multi-máquina (Red Local):**
> 1. Asegúrate de que las máquinas estén en la misma red y puedan hacerse `ping`.
> 2. En Windows, verifica que el **Firewall de Windows** no bloquee Python (o permite conexiones entrantes en el perfil de red Privada para los puertos de la Central: `9090`, `8080`, `5000` y de los barcos: `9001`, `9002`, etc.).
> 3. Tanto `central.py` como `barco.py` auto-detectan la IP de red local de la máquina de forma automática.

---

### Opción A: Ejecución en Múltiples Computadoras (Distribuida Real)

#### 1. En la Computadora 1 (Servidor / Central):
Ejecuta la Central sin parámetros o con los puertos deseados:
```bash
python central.py
```
*(Al iniciar, la Central imprimirá su IP detectada en la LAN, por ejemplo `10.15.3.106`, el Name Server en el puerto 9090 y el Dashboard en el puerto 5000).*

#### 2. En la Computadora 2 (y/o Computadora 3, Barcos remotos):
Ejecuta cada barco indicando la IP de la Computadora 1 en `--central`:
```bash
# En Computadora 2 (Barco 2):
python barco.py 2 --nombre "Fragata Beta" --port 9002 --central 10.15.3.106:9090 --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0

# En Computadora 3 (Barco 3):
python barco.py 3 --nombre "Corbeta Gamma" --port 9003 --central 10.15.3.106:9090 --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0
```
*(El barco auto-detectará la IP de la máquina donde corre y registrará su URI accesible hacia los demás).*

#### 3. Formar la Flota:
Una vez que veas en la consola de la Central que todos los barcos se registraron, en la consola de la Central escribe:
```bash
> formar flota
```

---

### Opción B: Ejecución Rápida en una Sola Computadora (Localhost)

Puedes usar el script automatizado:
```bash
iniciar_demo.bat
```
O abrir 4 terminales manualmente:

- **Terminal 1 (Central):**
  ```bash
  python central.py --host 127.0.0.1
  ```
- **Terminal 2 (Barco 1):**
  ```bash
  python barco.py 1 --nombre "Destructor Alfa" --host 127.0.0.1 --port 9001 --central 127.0.0.1:9090 --lat -34.6 --lon -58.4 --rumbo 45 --vel 14.5
  ```
- **Terminal 3 (Barco 2):**
  ```bash
  python barco.py 2 --nombre "Fragata Beta" --host 127.0.0.1 --port 9002 --central 127.0.0.1:9090 --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0
  ```
- **Terminal 4 (Barco 3):**
  ```bash
  python barco.py 3 --nombre "Corbeta Gamma" --host 127.0.0.1 --port 9003 --central 127.0.0.1:9090 --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0
  ```

---

### Paso Siguiente: Dashboard y Monitoreo

### Paso 4: Visualizar el Dashboard

Abre un navegador y dirígete a:

```
http://localhost:5000
```

### Paso 5: Probar Comandos

En cualquier terminal de barco, prueba enviar solicitudes:

```bash
> ataque_aereo
> solicitar_suministros
```
Verifica que los mensajes de Lamport avanzan y que la solicitud llega a la Central a través del Primario.

### Prueba de Caída (Failover)

1. Observa en la consola del dashboard/central qué barco es el primario.
2. En la terminal de ese barco, presiona `Ctrl + C` para cerrarlo.
3. Observa cómo los barcos restantes detectan la caída mediante heartbeats.
4. Se iniciará un proceso de elección (mensajes ELECCION / COORDINADOR).
5. El ganador se proclamará como nuevo primario, actualizará su rol y el sistema se recuperará automáticamente sin que la central caiga.
