"""
servidor_mqtt.py
==================
Cliente MQTT para Dom Home: desbloquea la cerradura de forma remota desde
CUALQUIER lugar con internet (datos móviles incluidos), usando un broker
MQTT en la nube (HiveMQ Cloud, capa gratuita hasta 100 conexiones).

Este es el patrón real que usan las cerraduras inteligentes comerciales:
el PC nunca recibe conexiones entrantes de internet (no hay que abrir
puertos ni exponer tu red). El PC y el celular se conectan cada uno,
por su cuenta, "hacia afuera" al broker.

Requisitos:
    pip install "paho-mqtt<2"

Uso desde dome home.py:
    from servidor_mqtt import iniciar_mqtt
    iniciar_mqtt(callback_desbloqueo=self.desbloqueo_remoto)
"""

import json
import threading
import paho.mqtt.client as mqtt

# ------------------------------------------------------------------
# CONFIGURA ESTOS DATOS CON LOS DE TU CLUSTER DE HIVEMQ CLOUD
# (pestaña "Overview" -> Host, y las credenciales que creaste en
#  "Access Management")
# ------------------------------------------------------------------
MQTT_HOST = "89adb44839c84d17a152b7654ce1f1f3.s1.eu.hivemq.cloud"   # <-- reemplaza
MQTT_PORT = 8883                                # puerto TLS estándar
MQTT_USUARIO = "Dom_home.pc"                     # <-- reemplaza
MQTT_PASSWORD = "0123456789"                   # <-- reemplaza
MQTT_TOPIC = "domhome/cerradura/desbloqueo"
PIN_SECRETO = "1010"                            # cámbialo por algo tuyo

_callback_desbloqueo = None


def _al_conectar(cliente, userdata, flags, rc):
    if rc == 0:
        cliente.subscribe(MQTT_TOPIC)
        print(f"📡 Conectado al broker MQTT. Escuchando: {MQTT_TOPIC}")
    else:
        print(f"❌ Error al conectar al broker MQTT (código {rc})")


def _al_recibir_mensaje(cliente, userdata, mensaje):
    try:
        datos = json.loads(mensaje.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    pin_recibido = str(datos.get("pin", ""))
    if pin_recibido != PIN_SECRETO:
        print("⚠️ Intento de desbloqueo remoto con PIN incorrecto.")
        return

    if _callback_desbloqueo:
        _callback_desbloqueo("Celular (remoto - MQTT)")


def iniciar_mqtt(callback_desbloqueo, pin_secreto=None):
    """
    Conecta al broker MQTT en un hilo aparte para no bloquear la interfaz
    de Tkinter. callback_desbloqueo(nombre_metodo) se llama cuando llega
    un comando válido desde el celular, sin importar dónde esté conectado.
    """
    global _callback_desbloqueo, PIN_SECRETO
    _callback_desbloqueo = callback_desbloqueo
    if pin_secreto:
        PIN_SECRETO = pin_secreto

    cliente = mqtt.Client()
    cliente.username_pw_set(MQTT_USUARIO, MQTT_PASSWORD)
    cliente.tls_set()  # conexión cifrada (TLS), estándar en HiveMQ Cloud
    cliente.on_connect = _al_conectar
    cliente.on_message = _al_recibir_mensaje

    cliente.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    hilo = threading.Thread(target=cliente.loop_forever, daemon=True)
    hilo.start()
    return hilo
