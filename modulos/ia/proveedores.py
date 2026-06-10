import json
from dataclasses import dataclass


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class RespuestaIA:
    texto: str
    tool_calls: list  # list[ToolCall]
    terminado: bool


PROVEEDORES = {
    "anthropic": {
        "nombre": "Anthropic (Claude)",
        "modelos": [
            "claude-sonnet-4-20250514",
            "claude-haiku-4-5-20251001",
        ],
        "requiere_key": True,
        "base_url": None,
        "tipo": "anthropic",
        "soporta_vision": True,
    },
    "openai": {
        "nombre": "OpenAI",
        "modelos": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "o3-mini",
        ],
        "requiere_key": True,
        "base_url": None,
        "tipo": "openai",
        "soporta_vision": True,
    },
    "groq": {
        "nombre": "Groq (gratis)",
        "modelos": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
        "requiere_key": True,
        "base_url": "https://api.groq.com/openai/v1",
        "tipo": "openai",
        "soporta_vision": False,
        "max_resultado_chars": 500,
        "max_historial_msgs": 10,
        "tools_compacto": True,
        "tools_mini": True,
    },
    "openrouter": {
        "nombre": "OpenRouter (muchos modelos)",
        "modelos": [
            "google/gemini-2.5-flash",
            "meta-llama/llama-4-maverick",
            "deepseek/deepseek-chat-v3",
            "qwen/qwen3-235b-a22b",
            "mistralai/mistral-large",
        ],
        "requiere_key": True,
        "base_url": "https://openrouter.ai/api/v1",
        "tipo": "openai",
        "soporta_vision": True,
    },
    "deepseek": {
        "nombre": "DeepSeek",
        "modelos": [
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        "requiere_key": True,
        "base_url": "https://api.deepseek.com",
        "tipo": "openai",
        "soporta_vision": False,
    },
    "ollama": {
        "nombre": "Ollama (local, gratis)",
        "modelos": [
            "llama3.1:8b",       # RECOMENDADO — tool calling nativo
            "llama3.2:3b",       # Más rápido, tool calling OK
            "qwen2.5:7b",        # Excelente tool calling
            "llama3:latest",     # Instalado — tool calling parcial
            "mixtral:latest",    # Instalado — bueno pero 26GB
            "mistral:latest",    # Instalado — tools limitado
        ],
        "requiere_key": False,
        "base_url": "http://localhost:11434/v1",
        "tipo": "openai",
        "soporta_vision": False,
        "tool_choice": "required",
        "tools_mini": True,
        "max_historial_msgs": 12,
        "modo_planner": True,        # genera JSON plan → sistema ejecuta tools
    },
}


def tools_para_anthropic(tools_schema: list) -> list:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in tools_schema
    ]


def tools_para_openai(tools_schema: list) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools_schema
    ]


def llamar_anthropic(cliente, modelo: str, system: str, messages: list, tools_schema: list) -> RespuestaIA:
    tools_api = tools_para_anthropic(tools_schema)

    response = cliente.messages.create(
        model=modelo,
        max_tokens=4096,
        system=system,
        tools=tools_api,
        messages=messages,
    )

    texto = ""
    tool_calls = []
    for block in response.content:
        if block.type == "text" and block.text.strip():
            texto += block.text
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

    terminado = response.stop_reason == "end_turn" or not tool_calls
    return RespuestaIA(texto=texto, tool_calls=tool_calls, terminado=terminado)


def mensaje_tools_anthropic(assistant_content, tool_results: list) -> list:
    result_blocks = []
    for tr in tool_results:
        result_blocks.append({
            "type": "tool_result",
            "tool_use_id": tr["id"],
            "content": tr["content"],
        })
    return [
        {"role": "assistant", "content": assistant_content},
        {"role": "user", "content": result_blocks},
    ]


def llamar_openai(cliente, modelo: str, system: str, messages: list, tools_schema: list) -> RespuestaIA:
    tools_api = tools_para_openai(tools_schema)

    msgs_openai = [{"role": "system", "content": system}]
    for m in messages:
        msgs_openai.append(m)

    kwargs = {"model": modelo, "messages": msgs_openai, "max_tokens": 4096}
    if tools_api:
        kwargs["tools"] = tools_api
        kwargs["tool_choice"] = "auto"

    response = cliente.chat.completions.create(**kwargs)
    msg = response.choices[0].message

    texto = msg.content or ""
    tool_calls = []

    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

    terminado = response.choices[0].finish_reason == "stop" or not tool_calls
    return RespuestaIA(texto=texto, tool_calls=tool_calls, terminado=terminado)


def mensaje_tools_openai(msg_original, tool_results: list) -> list:
    assistant_msg = {"role": "assistant", "content": msg_original.content}
    if hasattr(msg_original, "tool_calls") and msg_original.tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg_original.tool_calls
        ]

    result_msgs = [assistant_msg]
    for tr in tool_results:
        result_msgs.append({
            "role": "tool",
            "tool_call_id": tr["id"],
            "content": tr["content"],
        })
    return result_msgs


class ClienteIA:
    def __init__(self, proveedor_id: str, api_key: str, modelo: str = None):
        self.proveedor_id = proveedor_id
        self.info = PROVEEDORES[proveedor_id]
        self.tipo = self.info["tipo"]
        self.modelo = modelo or self.info["modelos"][0]
        self._cliente = None
        self._ultimo_response = None

        if self.tipo == "anthropic":
            import anthropic
            self._cliente = anthropic.Anthropic(api_key=api_key)
        else:
            import openai
            kwargs = {"api_key": api_key or "ollama"}
            if self.info["base_url"]:
                kwargs["base_url"] = self.info["base_url"]
            self._cliente = openai.OpenAI(**kwargs)

    def llamar(self, system: str, messages: list, tools_schema: list) -> RespuestaIA:
        if self.tipo == "anthropic":
            resp = llamar_anthropic(self._cliente, self.modelo, system, messages, tools_schema)
            self._ultimo_response = None
            return resp
        else:
            resp_raw = self._llamar_openai_raw(system, messages, tools_schema)
            msg = resp_raw.choices[0].message
            self._ultimo_response = msg

            texto = msg.content or ""
            tool_calls = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

            finish_reason = resp_raw.choices[0].finish_reason
            # Con tool_choice=required: solo terminar si finish_reason es "stop" Y hay texto final
            # Evita que el modelo "termine" sin haber llamado ninguna herramienta
            if self.info.get("tool_choice") == "required":
                terminado = finish_reason == "stop" and not tool_calls
            else:
                terminado = finish_reason == "stop" or not tool_calls
            return RespuestaIA(texto=texto, tool_calls=tool_calls, terminado=terminado)

    def _llamar_openai_raw(self, system, messages, tools_schema):
        tools_api = tools_para_openai(tools_schema)
        msgs = [{"role": "system", "content": system}] + messages
        kwargs = {"model": self.modelo, "messages": msgs, "max_tokens": 4096}
        if tools_api:
            kwargs["tools"] = tools_api
            kwargs["tool_choice"] = self.info.get("tool_choice", "auto")
        return self._cliente.chat.completions.create(**kwargs)

    @property
    def modo_planner(self) -> bool:
        return self.info.get("modo_planner", False)

    @property
    def soporta_vision(self) -> bool:
        return self.info.get("soporta_vision", False)

    @property
    def max_resultado_chars(self) -> int:
        return self.info.get("max_resultado_chars", 4000)

    @property
    def max_historial_msgs(self) -> int:
        return self.info.get("max_historial_msgs", 0)

    @property
    def tools_compacto(self) -> bool:
        return self.info.get("tools_compacto", False)

    @property
    def tools_mini(self) -> bool:
        return self.info.get("tools_mini", False)

    def construir_mensajes_resultado(self, respuesta_ia: RespuestaIA, tool_results: list) -> list:
        """tool_results: lista de dicts con id, content (str), y opcional image_base64."""
        if self.tipo == "anthropic":
            assistant_content = []
            if respuesta_ia.texto:
                assistant_content.append({"type": "text", "text": respuesta_ia.texto})
            for tc in respuesta_ia.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })

            result_blocks = []
            for tr in tool_results:
                if tr.get("image_base64"):
                    contenido = [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": tr["image_base64"],
                            },
                        },
                        {"type": "text", "text": tr["content"]},
                    ]
                    result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tr["id"],
                        "content": contenido,
                    })
                else:
                    result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tr["id"],
                        "content": tr["content"],
                    })
            return [
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": result_blocks},
            ]
        else:
            assistant_msg = {"role": "assistant", "content": respuesta_ia.texto or ""}
            if respuesta_ia.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input, ensure_ascii=False),
                        },
                    }
                    for tc in respuesta_ia.tool_calls
                ]

            result_msgs = [assistant_msg]
            for tr in tool_results:
                result_msgs.append({
                    "role": "tool",
                    "tool_call_id": tr["id"],
                    "content": tr["content"],
                })
                if tr.get("image_base64") and self.soporta_vision:
                    result_msgs.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Imagen del preview generado:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tr['image_base64']}"}},
                        ],
                    })
            return result_msgs
