# Sistema Distribuido de Comunicación de Flotas Navales

Esqueleto base para un proyecto de sistemas distribuidos en Python utilizando **Pyro5**.

## Arquitectura y Requerimientos Implementados

1. **Temática**: Sistema de comunicación de flotas navales.
2. **Nodos**: Clúster de 3 barcos (servidores de servicio) y 1 Central (cliente).
3. **Arquitectura Primario-Backup (Remote-Write)**: La Central siempre realiza las peticiones (lectura y escritura remota) al Barco Primario.
4. **RPC exclusivo con Pyro5**: Comunicación entre nodos y cliente resuelta vía invocaciones remotas de Pyro5.
5. **Replicación Sincrónica**: El Barco Primario envía los datos actualizados a los nodos Backups y espera su confirmación (`True`) antes de responder a la Central.
6. **Relojes Lógicos de Lamport**: Cada interacción remota transporta y actualiza un reloj de Lamport según $L_{local} = \max(L_{local}, L_{remoto}) + 1$.
7. **Algoritmo de Elección en Anillo (Ring Election)**: Topología lógica circular ($1 \rightarrow 2 \rightarrow 3 \rightarrow 1$). Cuando el servidor primario cae, los backups lo detectan mediante monitor de salud, inician el proceso de elección y promueven al nodo de mayor ID como nuevo primario en el Name Server (`flota.primario`).
8. **Manejo de Excepciones en Cliente**: La `Central` ataja errores de conexión (`CommunicationError`) y se reconecta automáticamente al nuevo primario tras consultar al Name Server de Pyro5.

---

## Estructura del Proyecto

```text
primer_proyecto_distribuidos/
│── barco.py           # Código del nodo del clúster (Barco Servidor / Backup / Primario)
│── central.py         # Código del cliente (Central Naval)
│── requirements.txt   # Dependencias de Python (Pyro5)
└── README.md          # Guía de ejecución y prueba
```

---

## Requisitos de Instalación

Asegúrate de contar con Python 3.8+ instalado. Luego, instala la dependencia Pyro5:

```bash
pip install -r requirements.txt
```

---

## Guía Paso a Paso para la Ejecución

Para probar el sistema de manera limpia, abre **5 terminales** independientes:

### Paso 1: Iniciar el Name Server de Pyro5 (Terminal 1)
```bash
pyro5-ns
```
*(Debe quedarse ejecutando en segundo plano)*.

### Paso 2: Iniciar el Clúster de Barcos (Terminales 2, 3 y 4)

- **Terminal 2 (Barco 1 - Primario Inicial):**
  ```bash
  python barco.py 1 --total 3 --primario
  ```

- **Terminal 3 (Barco 2 - Backup):**
  ```bash
  python barco.py 2 --total 3
  ```

- **Terminal 4 (Barco 3 - Backup):**
  ```bash
  python barco.py 3 --total 3
  ```

### Paso 3: Iniciar la Central Naval (Terminal 5)

Por defecto, la Central consulta cada 30 segundos. Para hacer pruebas más rápidas, se puede pasar el parámetro `--intervalo 5` (5 segundos):

```bash
python central.py --intervalo 5
```

---

## Prueba de Caída del Servidor Primario (Failover)

1. Con los 3 barcos y la Central en ejecución, observar cómo la Central realiza peticiones al **Barco 1** (Primario) y cómo el Barco 1 replica sincrónicamente en los Barcos 2 y 3.
2. En la **Terminal 2** (Barco 1), presiona `Ctrl + C` para simular la caída o hundimiento del primario.
3. Observa los logs en las terminales de los Barcos 2 y 3:
   - El monitor de salud detectará la falta de respuesta.
   - Se iniciará el algoritmo de **Elección en Anillo** (`recibir_mensaje_eleccion`).
   - El **Barco 3** (por ser el de mayor ID activo) será proclamado nuevo primario y se registrará automáticamente en Pyro5 como `flota.primario`.
4. Observa los logs en la **Terminal 5** (Central):
   - Atajará la excepción de red `CommunicationError`.
   - Esperará unos segundos y consultará al Name Server por el nuevo líder.
   - Reanudará las operaciones RPC sin interrumpir el programa, hablando directamente con el **Barco 3**.
