import subprocess
import json
import threading
import time
import os


class ConectorAutoCAD:
    def __init__(self, mcp_path: str, on_log=None):
        self.mcp_path = mcp_path
        self.proceso = None
        self.conectado = False
        self.on_log = on_log or (lambda msg: None)
        self._lock = threading.Lock()

    def _log(self, msg: str):
        self.on_log(msg)

    def iniciar_mcp(self) -> bool:
        if self.proceso and self.proceso.poll() is None:
            self._log("MCP ya esta corriendo")
            return True

        if not os.path.exists(self.mcp_path):
            self._log(f"No se encontro: {self.mcp_path}")
            return False

        try:
            mcp_dir = os.path.dirname(self.mcp_path)
            self.proceso = subprocess.Popen(
                ["python", self.mcp_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=mcp_dir,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(1)
            if self.proceso.poll() is not None:
                err = self.proceso.stderr.read().decode("utf-8", errors="replace")
                self._log(f"MCP fallo al iniciar: {err[:200]}")
                return False

            self.conectado = True
            self._log("MCP AutoCAD iniciado correctamente")
            return True
        except Exception as e:
            self._log(f"Error al iniciar MCP: {e}")
            return False

    def detener_mcp(self):
        if self.proceso and self.proceso.poll() is None:
            self.proceso.terminate()
            self.proceso.wait(timeout=5)
            self._log("MCP detenido")
        self.conectado = False

    def enviar_comando(self, metodo: str, params: dict) -> dict:
        with self._lock:
            if not self.proceso or self.proceso.poll() is not None:
                return {"error": "MCP no esta corriendo"}
            try:
                mensaje = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": f"tools/{metodo}",
                    "params": params,
                }
                payload = json.dumps(mensaje) + "\n"
                self.proceso.stdin.write(payload.encode("utf-8"))
                self.proceso.stdin.flush()

                linea = self.proceso.stdout.readline().decode("utf-8", errors="replace")
                if linea.strip():
                    return json.loads(linea)
                return {"error": "Sin respuesta del MCP"}
            except Exception as e:
                return {"error": str(e)}

    def verificar_autocad(self) -> bool:
        try:
            import win32com.client
            win32com.client.GetActiveObject("AutoCAD.Application")
            return True
        except Exception:
            return False

    def obtener_plano_activo(self) -> str:
        try:
            import win32com.client
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
            return acad.ActiveDocument.Name
        except Exception:
            return ""

    @property
    def estado(self) -> str:
        if not self.conectado:
            return "Desconectado"
        if self.proceso and self.proceso.poll() is None:
            return "Conectado"
        self.conectado = False
        return "Desconectado"

    def get_tools_schema(self) -> list:
        return [
            {"name": "generar_planta_desde_descripcion", "label": "Generar Planta"},
            {"name": "unir_muros", "label": "Limpiar Esquinas"},
            {"name": "agregar_puerta", "label": "Insertar Puerta"},
            {"name": "agregar_ventana", "label": "Insertar Ventana"},
            {"name": "cotar_planta", "label": "Agregar Cotas"},
            {"name": "aplicar_estandares_capas", "label": "Aplicar Estandares"},
            {"name": "dibujar_muro", "label": "Dibujar Muro"},
            {"name": "insertar_norte", "label": "Insertar Norte"},
            {"name": "insertar_escala", "label": "Insertar Escala"},
            {"name": "exportar_pdf", "label": "Exportar PDF"},
        ]
