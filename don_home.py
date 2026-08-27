"""
DOM HOME - Sistema Inteligente de Seguridad Biométrica
========================================================
Aplicación de escritorio para gestionar el acceso a una vivienda mediante
reconocimiento facial (con motor LBPH de OpenCV) y un ID de "huella"
simulado. Incluye registro de residentes, verificación de entrada y
bitácora de accesos con auditoría.

Requisitos (ver requirements.txt):
    customtkinter
    opencv-contrib-python
    numpy
    paho-mqtt<2
"""

import os
import logging
import sqlite3
import tkinter.messagebox as messagebox
from datetime import datetime

import cv2
import numpy as np
import customtkinter as ctk

from servidor_mqtt import iniciar_mqtt

# ------------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_DB = os.path.join(BASE_DIR, "dome_home.db")
RUTA_LOG = os.path.join(BASE_DIR, "dome_home.log")

TAMANO_ROSTRO = (200, 200)          # Tamaño estándar de normalización facial
UMBRAL_CONFIANZA_LBPH = 65          # Menor valor = coincidencia más estricta
CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"

logging.basicConfig(
    filename=RUTA_LOG,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8",
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def inicializar_base_datos():
    """Crea las tablas necesarias si no existen (despliegue en equipo nuevo)."""
    conexion = sqlite3.connect(RUTA_DB)
    cursor = conexion.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            huella_id INTEGER UNIQUE,
            rostro_data BLOB,
            fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accesos (
            id_registro INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER,
            fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP,
            metodo_acceso TEXT NOT NULL,
            resultado TEXT NOT NULL DEFAULT 'Concedido',
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)
    conexion.commit()
    conexion.close()


class DomHomeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Dom Home - Sistema Inteligente de Seguridad Biométrica")
        self.geometry("950x600")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)

        self.detector_rostros = cv2.CascadeClassifier(CASCADE_PATH)
        if self.detector_rostros.empty():
            messagebox.showerror(
                "Error de inicialización",
                "No se pudo cargar el clasificador facial de OpenCV.",
            )

        # --- PANEL LATERAL ---
        self.sidebar = ctk.CTkFrame(self, width=240, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(
            self.sidebar, text="DOM HOME 🏠", font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=30)

        self.entry_nombre = ctk.CTkEntry(self.sidebar, placeholder_text="Nombre", width=200)
        self.entry_nombre.grid(row=1, column=0, padx=20, pady=10)
        self.entry_huella = ctk.CTkEntry(self.sidebar, placeholder_text="ID Huella (numérico)", width=200)
        self.entry_huella.grid(row=2, column=0, padx=20, pady=10)

        ctk.CTkButton(
            self.sidebar, text="📸 Registrar", command=self.registrar_usuario_interfaz
        ).grid(row=3, column=0, padx=20, pady=10)
        ctk.CTkButton(
            self.sidebar, text="🔐 Escanear Entrada", fg_color="#d35400",
            command=self.verificar_entrada_facial
        ).grid(row=4, column=0, padx=20, pady=10)
        ctk.CTkButton(
            self.sidebar, text="📊 Ver Bitácora", fg_color="#248a3d",
            command=self.mostrar_historial_interfaz
        ).grid(row=5, column=0, padx=20, pady=10)
        ctk.CTkButton(
            self.sidebar, text="🧹 Limpiar Log", fg_color="#555555",
            command=self.limpiar_log
        ).grid(row=6, column=0, padx=20, pady=10)
        ctk.CTkButton(
            self.sidebar, text="🚪 Salir", fg_color="#8b0000",
            command=self.cerrar_aplicacion
        ).grid(row=7, column=0, padx=20, pady=10)

        # --- CUERPO ---
        self.main_view = ctk.CTkFrame(self, corner_radius=15, fg_color="#1a1c1e")
        self.main_view.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

        self.log_text = ctk.CTkTextbox(self.main_view, width=640, height=340)
        self.log_text.pack(padx=20, pady=10)

        self.status_frame = ctk.CTkFrame(self.main_view, height=60, fg_color="#2c3e50")
        self.status_frame.pack(padx=20, pady=10, fill="x")
        self.status_label = ctk.CTkLabel(
            self.status_frame, text="🔒 CERRADURA BLOQUEADA",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.status_label.pack(pady=15)

        self._log(f"Sistema iniciado. Base de datos: {RUTA_DB}")

        # --- CONTROL REMOTO (MQTT) ---
        iniciar_mqtt(callback_desbloqueo=self.desbloqueo_remoto)
        self._log("📡 Conectado al broker MQTT — control remoto activo.")

    # ------------------------------------------------------------------
    # UTILIDADES
    # ------------------------------------------------------------------
    def _log(self, mensaje: str):
        """Escribe en el panel visual y en el archivo de auditoría."""
        self.log_text.insert("end", mensaje + "\n")
        self.log_text.see("end")
        logging.info(mensaje)

    def limpiar_log(self):
        self.log_text.delete("1.0", "end")

    def cerrar_aplicacion(self):
        cv2.destroyAllWindows()
        self.destroy()

    def _conectar_db(self):
        return sqlite3.connect(RUTA_DB)

    # ------------------------------------------------------------------
    # PROCESAMIENTO DE IMAGEN / BIOMETRÍA
    # ------------------------------------------------------------------
    def detectar_rostro(self, fotograma):
        """Devuelve (x, y, w, h) del rostro más grande detectado, o None."""
        gris = cv2.cvtColor(fotograma, cv2.COLOR_BGR2GRAY)
        rostros = self.detector_rostros.detectMultiScale(
            gris, scaleFactor=1.1, minNeighbors=6, minSize=(90, 90)
        )
        if len(rostros) == 0:
            return None
        # Nos quedamos con el rostro de mayor área (el más cercano a la cámara)
        return max(rostros, key=lambda r: r[2] * r[3])

    def procesar_rostro(self, fotograma, caja):
        """Recorta, normaliza y codifica el rostro detectado en un JPG en escala de grises."""
        x, y, w, h = caja
        gris = cv2.cvtColor(fotograma, cv2.COLOR_BGR2GRAY)
        recorte = gris[y:y + h, x:x + w]
        recorte = cv2.resize(recorte, TAMANO_ROSTRO)
        recorte = cv2.equalizeHist(recorte)  # normaliza iluminación
        _, buffer = cv2.imencode(".jpg", recorte)
        return buffer.tobytes()

    def capturar_rostro(self, titulo_ventana: str):
        """
        Abre la cámara y espera a que el usuario encuadre su rostro.
        ENTER captura (solo si hay un rostro detectado), ESC cancela.
        Devuelve los bytes del rostro procesado, o None si se canceló/falló.
        """
        camara = cv2.VideoCapture(0)
        if not camara.isOpened():
            self._log("❌ ERROR: No se pudo acceder a la cámara.")
            messagebox.showerror("Cámara no disponible", "Verifica que la cámara esté conectada y libre.")
            return None

        resultado = None
        try:
            while True:
                valido, fotograma = camara.read()
                if not valido:
                    self._log("❌ ERROR: Fallo al leer el fotograma de la cámara.")
                    break

                caja = self.detectar_rostro(fotograma)
                vista = fotograma.copy()
                if caja is not None:
                    x, y, w, h = caja
                    cv2.rectangle(vista, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    texto, color = "Rostro detectado - ENTER para capturar", (0, 255, 0)
                else:
                    texto, color = "Buscando rostro... (ESC para cancelar)", (0, 0, 255)
                cv2.putText(vista, texto, (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                cv2.imshow(titulo_ventana, vista)

                tecla = cv2.waitKey(1) & 0xFF
                if tecla == 13 and caja is not None:      # ENTER
                    resultado = self.procesar_rostro(fotograma, caja)
                    break
                if tecla == 27:                             # ESC
                    self._log("⚠️ Captura cancelada por el usuario.")
                    break
        finally:
            camara.release()
            cv2.destroyAllWindows()

        return resultado

    def entrenar_reconocedor(self):
        """
        Entrena un modelo LBPH con todos los rostros almacenados en la BD.
        Devuelve (recognizer, {id_usuario: nombre}) o (None, {}) si no hay datos.
        """
        conexion = self._conectar_db()
        cursor = conexion.cursor()
        cursor.execute("SELECT id, nombre, rostro_data FROM usuarios WHERE rostro_data IS NOT NULL")
        filas = cursor.fetchall()
        conexion.close()

        if not filas:
            return None, {}

        imagenes, etiquetas, nombres = [], [], {}
        for id_usuario, nombre, datos in filas:
            arreglo = np.frombuffer(datos, np.uint8)
            imagen = cv2.imdecode(arreglo, cv2.IMREAD_GRAYSCALE)
            if imagen is None:
                continue
            imagenes.append(imagen)
            etiquetas.append(id_usuario)
            nombres[id_usuario] = nombre

        if not imagenes:
            return None, {}

        reconocedor = cv2.face.LBPHFaceRecognizer_create()
        reconocedor.train(imagenes, np.array(etiquetas))
        return reconocedor, nombres

    # ------------------------------------------------------------------
    # REGISTRO
    # ------------------------------------------------------------------
    def registrar_usuario_interfaz(self):
        nombre = self.entry_nombre.get().strip()
        huella = self.entry_huella.get().strip()

        if not nombre or not huella:
            self._log("⚠️ ERROR: Falta nombre o ID de huella.")
            return
        if not huella.isdigit():
            self._log("⚠️ ERROR: El ID de huella debe ser numérico.")
            return

        conexion = self._conectar_db()
        cursor = conexion.cursor()
        cursor.execute("SELECT id FROM usuarios WHERE huella_id = ?", (huella,))
        if cursor.fetchone():
            self._log(f"⚠️ ERROR: El ID de huella {huella} ya está registrado.")
            conexion.close()
            return
        conexion.close()

        self._log(f"[REGISTRO] Preparando captura facial para {nombre}...")
        self.update()
        foto_bytes = self.capturar_rostro("Registro - ENTER para capturar, ESC para cancelar")

        if foto_bytes is None:
            self._log("❌ Registro no completado: no se capturó el rostro.")
            return

        try:
            conexion = self._conectar_db()
            cursor = conexion.cursor()
            cursor.execute(
                "INSERT INTO usuarios (nombre, huella_id, rostro_data) VALUES (?, ?, ?)",
                (nombre, huella, foto_bytes),
            )
            conexion.commit()
            conexion.close()
            self._log(f"✅ ÉXITO: {nombre} registrado en la base de datos.")
            self.entry_nombre.delete(0, "end")
            self.entry_huella.delete(0, "end")
        except sqlite3.Error as e:
            self._log(f"❌ ERROR CRÍTICO AL GUARDAR: {e}")

    # ------------------------------------------------------------------
    # VERIFICACIÓN DE ENTRADA
    # ------------------------------------------------------------------
    def verificar_entrada_facial(self):
        self._log("\n[ESCÁNER] Iniciando escaneo de entrada...")
        self.update()

        foto_intento = self.capturar_rostro("Escaneo - ENTER para verificar, ESC para cancelar")
        if foto_intento is None:
            self._log("⚠️ Escaneo cancelado.")
            return

        reconocedor, nombres = self.entrenar_reconocedor()
        conexion = self._conectar_db()
        cursor = conexion.cursor()

        if reconocedor is None:
            self._log("❌ No hay usuarios registrados con foto en el sistema.")
            self._denegar_acceso(cursor, conexion, usuario_id=None)
            conexion.close()
            return

        arreglo = np.frombuffer(foto_intento, np.uint8)
        imagen_intento = cv2.imdecode(arreglo, cv2.IMREAD_GRAYSCALE)

        id_predicho, confianza = reconocedor.predict(imagen_intento)
        self._log(f"> Coincidencia más cercana: {nombres.get(id_predicho, '¿?')} "
                   f"(distancia LBPH: {confianza:.2f}, umbral: {UMBRAL_CONFIANZA_LBPH})")

        if confianza < UMBRAL_CONFIANZA_LBPH:
            nombre = nombres[id_predicho]
            cursor.execute(
                "INSERT INTO accesos (usuario_id, metodo_acceso, resultado, fecha_hora) VALUES (?, ?, ?, ?)",
                (id_predicho, "Face ID", "Concedido", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            conexion.commit()
            self._log(f"✅ Acceso concedido a {nombre}.")
            self.status_label.configure(text=f"🔓 BIENVENIDO: {nombre.upper()}")
            self.status_frame.configure(fg_color="#27ae60")
        else:
            self._denegar_acceso(cursor, conexion, usuario_id=None)

        conexion.close()

    def _denegar_acceso(self, cursor, conexion, usuario_id):
        cursor.execute(
            "INSERT INTO accesos (usuario_id, metodo_acceso, resultado, fecha_hora) VALUES (?, ?, ?, ?)",
            (usuario_id, "Face ID", "Denegado", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conexion.commit()
        self._log("❌ Acceso denegado: ninguna coincidencia confiable.")
        self.status_label.configure(text="❌ ACCESO DENEGADO")
        self.status_frame.configure(fg_color="#c0392b")

    # ------------------------------------------------------------------
    # ACCESO REMOTO (MQTT)
    # ------------------------------------------------------------------
    def desbloqueo_remoto(self, metodo):
        """Se llama cuando el celular envía el PIN correcto desde MQTT."""
        conexion = self._conectar_db()
        cursor = conexion.cursor()
        cursor.execute(
            "INSERT INTO accesos (usuario_id, metodo_acceso, resultado, fecha_hora) VALUES (?, ?, ?, ?)",
            (None, metodo, "Concedido", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conexion.commit()
        conexion.close()
        self._log(f"✅ Acceso concedido vía {metodo}.")
        self.status_label.configure(text="🔓 DESBLOQUEADO DESDE CELULAR")
        self.status_frame.configure(fg_color="#27ae60")

    # ------------------------------------------------------------------
    # BITÁCORA
    # ------------------------------------------------------------------
    def mostrar_historial_interfaz(self):
        self.log_text.delete("1.0", "end")
        conexion = self._conectar_db()
        cursor = conexion.cursor()
        cursor.execute("""
            SELECT COALESCE(u.nombre, '(usuario eliminado)'), a.metodo_acceso, a.resultado, a.fecha_hora
            FROM accesos a
            LEFT JOIN usuarios u ON a.usuario_id = u.id
            ORDER BY a.fecha_hora DESC
            LIMIT 100
        """)
        filas = cursor.fetchall()
        conexion.close()

        if not filas:
            self.log_text.insert("end", "No hay registros de acceso todavía.\n")
            return

        for nombre, metodo, resultado, fecha in filas:
            icono = "✅" if resultado == "Concedido" else "❌"
            self.log_text.insert("end", f"{icono} {nombre} | {metodo} | {resultado} | {fecha}\n")


if __name__ == "__main__":
    inicializar_base_datos()
    app = DomHomeApp()
    app.mainloop()
