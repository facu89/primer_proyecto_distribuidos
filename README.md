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

## Guía Paso a Paso para la Ejecución

Abre 4 terminales independientes.

### Paso 1: Iniciar la Central Naval (Terminal 1)

Inicia la Central. Esto levantará el Name Server embebido en el puerto 9090, el servidor HTTP en el puerto 5000 y la interfaz en consola.

```bash
python central.py --host 0.0.0.0 --port 8080 --ns-port 9090 --http-port 5000
```

### Paso 2: Iniciar los Barcos (Terminales 2, 3 y 4)

Los barcos se conectarán a la central para registrarse.

- **Terminal 2 (Barco 1):**
  ```bash
  python barco.py 1 --nombre "Destructor Alfa" --port 9001 --central 127.0.0.1:9090 --lat -34.6 --lon -58.4 --rumbo 45 --vel 12.5
  ```

- **Terminal 3 (Barco 2):**
  ```bash
  python barco.py 2 --nombre "Fragata Beta" --port 9002 --central 127.0.0.1:9090 --lat -25.3 --lon -10.2 --rumbo 175 --vel 9.1
  ```

- **Terminal 4 (Barco 3):**
  ```bash
  python barco.py 3 --nombre "Corbeta Gamma" --port 9003 --central 127.0.0.1:9090 --lat 20.0 --lon 125.0 --rumbo 160 --vel 4.1
  ```

### Paso 3: Formar la Flota

En la consola de la Central (Terminal 1), tipea:

```bash
> formar flota
```
Esto informará a los barcos de la topología y se iniciarán los mecanismos de replicación y heartbeats.

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
