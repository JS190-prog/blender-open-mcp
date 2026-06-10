# server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Optional
import httpx
from io import BytesIO
import base64
import argparse
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Socket read timeout (seconds). Synchronous calls on large scenes block
# Blender's main thread for a long time, so the default is generous and can be
# overridden via BLENDER_MCP_TIMEOUT. For unbounded work prefer the async job
# tools (start_blender_job) which return immediately and are polled.
_DEFAULT_TIMEOUT = float(os.environ.get("BLENDER_MCP_TIMEOUT", "180"))

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: Optional[socket.socket] = None
    timeout: float = _DEFAULT_TIMEOUT  # override via BLENDER_MCP_TIMEOUT

    def __post_init__(self):
         if not isinstance(self.host, str):
             raise ValueError("Host must be a string")
         if not isinstance(self.port, int):
             raise ValueError("Port must be an int")

    def connect(self) -> bool:
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            self.sock.settimeout(self.timeout) # Set timeout on socket
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {e!s}")
            self.sock = None
            return False

    def disconnect(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting: {e!s}")
            finally:
                self.sock = None

    def _receive_full_response(self, buffer_size: int = 8192) -> bytes:
        """Receive data with timeout using a loop."""
        chunks: List[bytes] = []
        timed_out = False
        try:
            while True:
                try:
                    chunk = self.sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            # Requirement 1b
                            raise Exception("Connection closed by Blender before any data was sent in this response")
                        else:
                            # Requirement 1a
                            raise Exception("Connection closed by Blender mid-stream with incomplete JSON data")
                    chunks.append(chunk)
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))  # Check if it is valid json
                        logger.debug(f"Received response ({len(data)} bytes)")
                        return data # Complete JSON received
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during receive")
                    timed_out = True # Set flag
                    break # Stop listening to socket
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error: {e!s}")
                    self.sock = None
                    raise # re-raise to outer error handler
            
            # This part is reached if loop is broken by 'break' (only timeout case now)
            if timed_out:
                if chunks:
                    data = b''.join(chunks)
                    # Check if the partial data is valid JSON (it shouldn't be if timeout happened mid-stream)
                    try:
                        json.loads(data.decode('utf-8'))
                        # This case should ideally not be hit if JSON was incomplete,
                        # but if it's somehow valid, return it.
                        logger.warning("Timeout occurred, but received data forms valid JSON.")
                        return data
                    except json.JSONDecodeError:
                        # Requirement 2a
                        raise Exception(f"Incomplete JSON data received before timeout. Received: {data[:200]}")
                else:
                    # Requirement 2b
                    raise Exception("Timeout waiting for response, no data received.")
            
            # Fallback if loop exited for a reason not covered by explicit raises inside or by timeout logic
            # This should ideally not be reached with the current logic.
            if chunks: # Should have been handled by "Connection closed by Blender mid-stream..."
                data = b''.join(chunks)
                logger.warning(f"Exited receive loop unexpectedly with data: {data[:200]}")
                raise Exception("Receive loop ended unexpectedly with partial data.")
            else: # Should have been handled by "Connection closed by Blender before any data..." or timeout
                logger.warning("Exited receive loop unexpectedly with no data.")
                raise Exception("Receive loop ended unexpectedly with no data.")

        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            # This handles connection errors raised from within the loop or if self.sock.recv fails
            logger.error(f"Connection error during receive: {e!s}")
            self.sock = None # Ensure socket is reset
            # Re-raise with a more specific message if needed, or just re-raise
            raise Exception(f"Connection to Blender lost during receive: {e!s}")
        except Exception as e: 
            # Catch other exceptions, including our custom ones, and log them
            logger.error(f"Error during _receive_full_response: {e!s}")
            # If it's not one of the specific connection errors, it might be one of our custom messages
            # or another unexpected issue. Re-raise to be handled by send_command.
            raise


    def send_command(self, command_type: str, params: Optional[Dict[str, Any]] = None,
                     timeout: Optional[float] = None) -> Dict[str, Any]:
         if not self.sock and not self.connect():
            raise ConnectionError("Not connected")
         command = {"type": command_type, "params": params or {}}
         # Per-command timeout override (e.g. short for fast job polling). The
         # persistent socket's default timeout is restored in finally.
         if timeout is not None and self.sock:
             self.sock.settimeout(timeout)
         try:
              logger.info(f"Sending command: {command_type} with params: {params}")
              self.sock.sendall(json.dumps(command).encode('utf-8'))
              logger.info(f"Command sent, waiting for response...")
              response_data = self._receive_full_response()
              logger.debug(f"Received response ({len(response_data)} bytes)")
              response = json.loads(response_data.decode('utf-8'))
              logger.info(f"Response status: {response.get('status', 'unknown')}")
              if response.get("status") == "error":
                 logger.error(f"Blender error: {response.get('message')}")
                 raise Exception(response.get("message", "Unknown Blender error"))
              return response.get("result", {})

         except socket.timeout:
             logger.error("Socket timeout from Blender")
             self.sock = None # reset socket connection
             raise Exception("Timeout waiting for Blender - simplify request")
         except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
             logger.error(f"Socket connection error: {e!s}")
             self.sock = None # reset socket connection
             raise Exception(f"Connection to Blender lost: {e!s}")
         except json.JSONDecodeError as e:
             logger.error(f"Invalid JSON response: {e!s}")
             if 'response_data' in locals() and response_data:
                logger.error(f"Raw (first 200): {response_data[:200]}")
             raise Exception(f"Invalid response from Blender: {e!s}")
         except Exception as e:
              logger.error(f"Error communicating with Blender: {e!s}")
              self.sock = None # reset socket connection
              raise Exception(f"Communication error: {e!s}")
         finally:
              # Restore the persistent socket's default read timeout.
              if timeout is not None and self.sock:
                  try:
                      self.sock.settimeout(self.timeout)
                  except Exception:
                      pass


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    logger.info("BlenderMCP server starting up")
    try:
        blender = get_blender_connection()
        logger.info("Connected to Blender on startup")
    except Exception as e:
        logger.warning(f"Could not connect to Blender on startup: {e!s}")
        logger.warning("Ensure Blender addon is running before using resources")
    yield {}
    global _blender_connection
    if _blender_connection:
        logger.info("Disconnecting from Blender on shutdown")
        _blender_connection.disconnect()
        _blender_connection = None
    logger.info("BlenderMCP server shut down")

# Initialize MCP server instance globally
mcp = FastMCP(
    "BlenderOpenMCP",
    lifespan=server_lifespan
)

_blender_connection = None
_polyhaven_enabled = False
# Default values (will be overridden by command-line arguments)
_ollama_model = ""
_ollama_url = "http://localhost:11434"
_opencrab_repo = Path(os.getenv("OPENCRAB_REPO", r"C:\scratch\OpenCrab"))
_opencrab_api_url = os.getenv("OPENCRAB_API_URL", "http://localhost:8001")

def _json(data: Any) -> str:
    return json.dumps(data, indent=2)

def _format_error(error: Exception) -> str:
    return f"Error: {error!s}"

def _opencrab_status() -> Dict[str, Any]:
    repo = _opencrab_repo
    db_path = repo / "opencrab.db"
    cli_path = repo / ".venv" / "Scripts" / "opencrab.exe"
    status: Dict[str, Any] = {
        "repo": str(repo),
        "repo_exists": repo.exists(),
        "api_url": _opencrab_api_url,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else None,
        "cli_path": str(cli_path),
        "cli_exists": cli_path.exists(),
        "blender_pack_searchable": None,
        "notes": [],
    }
    if status["db_size_bytes"] == 0:
        status["notes"].append("opencrab.db is empty; Blender manual pack may not be indexed locally.")
    if cli_path.exists():
        try:
            proc = subprocess.run(
                [str(cli_path), "--help"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            status["cli_exit_code"] = proc.returncode
            status["cli_error"] = (proc.stderr or "").strip()[:500] or None
            if proc.returncode != 0:
                status["notes"].append("opencrab CLI is present but not runnable in this environment.")
        except Exception as e:
            status["cli_exit_code"] = None
            status["cli_error"] = str(e)
            status["notes"].append("opencrab CLI check failed.")
    return status

def _send_blender_command(command: str, params: Optional[Dict[str, Any]] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command(command, params or {})
        return _json(result)
    except Exception as e:
        return _format_error(e)

def get_blender_connection(check: bool = True) -> BlenderConnection:
    global _blender_connection, _polyhaven_enabled
    if _blender_connection:
        if not check:
            # Skip the polyhaven health-check (which is dispatched on Blender's
            # main thread) so job-status polling stays responsive while a long
            # async job is blocking the main thread.
            return _blender_connection
        try:
            result = _blender_connection.send_command("get_polyhaven_status")
            _polyhaven_enabled = result.get("enabled", False)
            return _blender_connection
        except Exception as e:
            logger.warning(f"Existing connection invalid: {e!s}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None
    if _blender_connection is None:
        _blender_connection = BlenderConnection(host="localhost", port=9876)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Addon running?")
        logger.info("Created new persistent connection to Blender")
    return _blender_connection

@mcp.tool()
def get_blender_mcp_status(ctx: Context) -> str:
    """Return a detailed health snapshot for the Blender addon bridge."""
    global _blender_connection
    status: Dict[str, Any] = {
        "mcp_server": {
            "name": "BlenderOpenMCP",
            "ollama_url": _ollama_url,
            "ollama_model": _ollama_model or None,
        },
        "opencrab": _opencrab_status(),
        "blender_bridge": {
            "host": "localhost",
            "port": 9876,
            "connected": False,
        },
    }

    try:
        if _blender_connection:
            result = _blender_connection.send_command("get_blender_mcp_status")
        else:
            probe = BlenderConnection(host="localhost", port=9876, timeout=3.0)
            try:
                if not probe.connect():
                    status["blender_bridge"]["error"] = "Could not open TCP connection. Start Blender and click 'Start MCP Server' in the BlenderMCP panel."
                    return _json(status)
                result = probe.send_command("get_blender_mcp_status")
            finally:
                probe.disconnect()

        status["blender_bridge"]["connected"] = True
        status["blender_bridge"].update(result)
        return _json(status)
    except Exception as e:
        if _blender_connection:
            try:
                _blender_connection.disconnect()
            finally:
                _blender_connection = None
        status["blender_bridge"]["error"] = str(e)
        return _json(status)


@mcp.tool()
def get_opencrab_blender_status(ctx: Context) -> str:
    """Return the local OpenCrab/LocalCrab status relevant to Blender workflow knowledge."""
    return _json(_opencrab_status())

@mcp.tool()
def suggest_blender_workflow(ctx: Context, intent: str) -> str:
    """Suggest a safe Blender MCP command sequence for a natural-language task."""
    text = intent.lower()
    steps: List[str] = []
    topics: List[str] = []

    def add(step: str) -> None:
        if step not in steps:
            steps.append(step)

    if any(word in text for word in ["import", "gltf", "glb", "fbx", "obj", "stl", "usd", "asset", "model"]):
        add("import_model")
        topics.extend(["Importing models", "File formats"])
    if any(word in text for word in ["product", "render", "렌더", "camera", "카메라", "shot", "image"]):
        add("list_objects")
        add("create_camera")
        add("setup_three_point_lighting")
        add("set_render_engine")
        add("set_render_resolution")
        add("render_image")
        topics.extend(["Camera", "Lighting", "Render settings", "Cycles", "Eevee"])
    if any(word in text for word in ["material", "재질", "texture", "pbr", "polyhaven"]):
        add("list_materials")
        add("create_principled_material")
        add("assign_material")
        topics.extend(["Materials", "Shader nodes", "PBR textures"])
    if any(word in text for word in ["modifier", "모디파이어", "bevel", "subdivision", "array", "mirror", "boolean"]):
        add("list_modifiers")
        add("add_modifier")
        add("set_modifier_property")
        topics.extend(["Modifiers", "Bevel", "Subdivision Surface", "Array", "Boolean"])
    if any(word in text for word in ["geometry nodes", "지오메트리", "scatter", "procedural"]):
        add("create_geometry_nodes_modifier")
        add("scatter_objects_on_surface")
        topics.extend(["Geometry Nodes", "Instancing", "Procedural modeling"])
    if any(word in text for word in ["animation", "animate", "애니메이션", "turntable", "keyframe"]):
        add("set_frame_range")
        add("set_keyframe")
        add("create_turntable_animation")
        topics.extend(["Animation", "Keyframes", "Interpolation"])
    if any(word in text for word in ["export", "save", "내보내", "저장"]):
        add("validate_scene")
        add("export_model")
        topics.extend(["Exporting", "Scene validation"])

    if not steps:
        steps = ["get_blender_mcp_status", "get_scene_info", "list_objects", "validate_scene"]
        topics = ["Scene basics", "Object management"]

    add("validate_scene")
    return _json({
        "intent": intent,
        "recommended_steps": steps,
        "related_manual_topics": sorted(set(topics)),
        "safe_mcp_commands": steps,
        "opencrab_status": _opencrab_status(),
        "note": "OpenCrab status is diagnostic here; commands are safe Blender MCP tools even when OpenCrab indexing is unavailable.",
    })

async def query_ollama(prompt: str, context: Optional[List[Dict]] = None, image: Optional[Image] = None) -> str:
    global _ollama_model, _ollama_url

    payload = {"prompt": prompt, "model": _ollama_model, "format": "json", "stream": False}
    if context:
        payload["context"] = context
    if image:
        if image.data:
            payload["images"] = [image.data]
        elif image.path:
            try:
                with open(image.path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                payload["images"] = [encoded_string]
            except FileNotFoundError:
                logger.error(f"Image file not found: {image.path}")
                return "Error: Image file not found."
        else:
            logger.warning("Image without data or path. Ignoring.")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{_ollama_url}/api/generate", json=payload, timeout=60.0)
            response.raise_for_status()  # Raise HTTPStatusError for bad status
            response_data = response.json()
            logger.debug(f"Raw Ollama response: {response_data}")
            if "response" in response_data:
                return response_data["response"]
            else:
                logger.error(f"Unexpected response format: {response_data}")
                return "Error: Unexpected response format from Ollama."

    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama API error: {e.response.status_code} - {e.response.text}")
        return f"Error: Ollama API returned: {e.response.status_code}"
    except httpx.RequestError as e:
        logger.error(f"Ollama API request failed: {e}")
        return "Error: Failed to connect to Ollama API."
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e!s}")
        return f"Error: An unexpected error occurred: {e!s}"

@mcp.prompt()
async def base_prompt(context: Context, user_message: str) -> str:
    system_message = f"""You are a helpful assistant that controls Blender.
    You can use the following tools. Respond in well-formatted, valid JSON:
    {mcp.tools_schema()}"""
    full_prompt = f"{system_message}\n\n{user_message}"
    response = await query_ollama(full_prompt, context.history(), context.get_image())
    return response

@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def list_objects(ctx: Context, include_hidden: bool = True, limit: int = 100) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_objects", {
            "include_hidden": include_hidden,
            "limit": limit,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def select_object(ctx: Context, name: str, extend: bool = False, make_active: bool = True) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("select_object", {
            "name": name,
            "extend": extend,
            "make_active": make_active,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def rename_object(ctx: Context, old_name: str, new_name: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("rename_object", {
            "old_name": old_name,
            "new_name": new_name,
        })
        return f"Renamed object: {result['old_name']} -> {result['new_name']}"
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def duplicate_object(ctx: Context, name: str, new_name: Optional[str] = None,
                     linked: bool = False, offset: Optional[List[float]] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("duplicate_object", {
            "name": name,
            "new_name": new_name,
            "linked": linked,
            "offset": offset or [0, 0, 0],
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def hide_object(ctx: Context, name: str, hide_render: bool = True) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("hide_object", {"name": name, "hide_render": hide_render})
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def show_object(ctx: Context, name: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("show_object", {"name": name})
        return _json(result)
    except Exception as e:
        return _format_error(e)
    
@mcp.tool()
def create_object(
    ctx: Context,
    type: str = "CUBE",
    name: Optional[str] = None,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None
) -> str:
    try:
        blender = get_blender_connection()
        loc, rot, sc = location or [0, 0, 0], rotation or [0, 0, 0], scale or [1, 1, 1]
        params = {"type": type, "location": loc, "rotation": rot, "scale": sc}
        if name: params["name"] = name
        result = blender.send_command("create_object", params)
        return f"Created {type} object: {result['name']}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def create_camera(ctx: Context, name: Optional[str] = None,
                  location: Optional[List[float]] = None,
                  rotation: Optional[List[float]] = None,
                  lens: Optional[float] = None,
                  make_active: bool = True) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_camera", {
            "name": name,
            "location": location or [0, -6, 3],
            "rotation": rotation or [1.109319, 0, 0],
            "lens": lens,
            "make_active": make_active,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def create_light(ctx: Context, type: str = "AREA", name: Optional[str] = None,
                 location: Optional[List[float]] = None,
                 rotation: Optional[List[float]] = None,
                 power: float = 500.0,
                 color: Optional[List[float]] = None,
                 size: Optional[float] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_light", {
            "type": type,
            "name": name,
            "location": location or [0, -3, 4],
            "rotation": rotation or [0, 0, 0],
            "power": power,
            "color": color,
            "size": size,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def setup_three_point_lighting(ctx: Context, target: Optional[List[float]] = None,
                               key_power: float = 600.0,
                               fill_power: float = 180.0,
                               back_power: float = 350.0) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("setup_three_point_lighting", {
            "target": target or [0, 0, 0],
            "key_power": key_power,
            "fill_power": fill_power,
            "back_power": back_power,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def set_render_engine(ctx: Context, engine: str = "CYCLES", samples: Optional[int] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_render_engine", {
            "engine": engine,
            "samples": samples,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def set_render_resolution(ctx: Context, width: int = 1920, height: int = 1080,
                          percentage: int = 100) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_render_resolution", {
            "width": width,
            "height": height,
            "percentage": percentage,
        })
        return _json(result)
    except Exception as e:
        return _format_error(e)

@mcp.tool()
def list_modifiers(ctx: Context, object_name: str) -> str:
    return _send_blender_command("list_modifiers", {"object_name": object_name})

@mcp.tool()
def add_modifier(ctx: Context, object_name: str, modifier_type: str,
                 modifier_name: Optional[str] = None,
                 properties: Optional[Dict[str, Any]] = None) -> str:
    return _send_blender_command("add_modifier", {
        "object_name": object_name,
        "modifier_type": modifier_type,
        "modifier_name": modifier_name,
        "properties": properties or {},
    })

@mcp.tool()
def set_modifier_property(ctx: Context, object_name: str, modifier_name: str,
                          property_name: str, value: Any) -> str:
    return _send_blender_command("set_modifier_property", {
        "object_name": object_name,
        "modifier_name": modifier_name,
        "property_name": property_name,
        "value": value,
    })

@mcp.tool()
def apply_modifier(ctx: Context, object_name: str, modifier_name: str) -> str:
    return _send_blender_command("apply_modifier", {
        "object_name": object_name,
        "modifier_name": modifier_name,
    })

@mcp.tool()
def remove_modifier(ctx: Context, object_name: str, modifier_name: str) -> str:
    return _send_blender_command("remove_modifier", {
        "object_name": object_name,
        "modifier_name": modifier_name,
    })

@mcp.tool()
def create_geometry_nodes_modifier(ctx: Context, object_name: str,
                                   modifier_name: str = "Geometry Nodes") -> str:
    return _send_blender_command("create_geometry_nodes_modifier", {
        "object_name": object_name,
        "modifier_name": modifier_name,
    })

@mcp.tool()
def scatter_objects_on_surface(ctx: Context, surface_object: str, instance_object: str,
                               count: int = 100, seed: int = 1) -> str:
    return _send_blender_command("scatter_objects_on_surface", {
        "surface_object": surface_object,
        "instance_object": instance_object,
        "count": count,
        "seed": seed,
    })

@mcp.tool()
def list_materials(ctx: Context, limit: int = 100) -> str:
    return _send_blender_command("list_materials", {"limit": limit})

@mcp.tool()
def create_principled_material(ctx: Context, material_name: str,
                               base_color: Optional[List[float]] = None,
                               roughness: Optional[float] = None,
                               metallic: Optional[float] = None) -> str:
    return _send_blender_command("create_principled_material", {
        "material_name": material_name,
        "base_color": base_color,
        "roughness": roughness,
        "metallic": metallic,
    })

@mcp.tool()
def assign_material(ctx: Context, object_name: str, material_name: str,
                    slot_index: int = 0) -> str:
    return _send_blender_command("assign_material", {
        "object_name": object_name,
        "material_name": material_name,
        "slot_index": slot_index,
    })

@mcp.tool()
def import_model(ctx: Context, file_path: str, file_format: Optional[str] = None) -> str:
    return _send_blender_command("import_model", {
        "file_path": file_path,
        "file_format": file_format,
    })

@mcp.tool()
def export_model(ctx: Context, file_path: str, file_format: Optional[str] = None,
                 selected_only: bool = False) -> str:
    return _send_blender_command("export_model", {
        "file_path": file_path,
        "file_format": file_format,
        "selected_only": selected_only,
    })

@mcp.tool()
def save_blend_file(ctx: Context, file_path: Optional[str] = None) -> str:
    return _send_blender_command("save_blend_file", {"file_path": file_path})

@mcp.tool()
def open_blend_file(ctx: Context, file_path: str) -> str:
    return _send_blender_command("open_blend_file", {"file_path": file_path})

@mcp.tool()
def validate_scene(ctx: Context) -> str:
    return _send_blender_command("validate_scene")

@mcp.tool()
def cleanup_scene(ctx: Context, remove_unused_data: bool = True,
                  remove_empty_collections: bool = True) -> str:
    return _send_blender_command("cleanup_scene", {
        "remove_unused_data": remove_unused_data,
        "remove_empty_collections": remove_empty_collections,
    })

@mcp.tool()
def set_frame_range(ctx: Context, start: int = 1, end: int = 120,
                    current: Optional[int] = None) -> str:
    return _send_blender_command("set_frame_range", {
        "start": start,
        "end": end,
        "current": current,
    })

@mcp.tool()
def set_keyframe(ctx: Context, object_name: str, frame: int,
                 data_path: str = "location") -> str:
    return _send_blender_command("set_keyframe", {
        "object_name": object_name,
        "frame": frame,
        "data_path": data_path,
    })

@mcp.tool()
def create_turntable_animation(ctx: Context, object_name: str, start: int = 1,
                               end: int = 120, axis: str = "Z") -> str:
    return _send_blender_command("create_turntable_animation", {
        "object_name": object_name,
        "start": start,
        "end": end,
        "axis": axis,
    })

@mcp.tool()
def modify_object(
    ctx: Context,
    name: str,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
    visible: Optional[bool] = None
) -> str:
    try:
        blender = get_blender_connection()
        params = {"name": name}
        if location is not None: params["location"] = location
        if rotation is not None: params["rotation"] = rotation
        if scale is not None: params["scale"] = scale
        if visible is not None: params["visible"] = visible
        result = blender.send_command("modify_object", params)
        return f"Modified object: {result['name']}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def delete_object(ctx: Context, name: str) -> str:
    try:
        blender = get_blender_connection()
        blender.send_command("delete_object", {"name": name})
        return f"Deleted object: {name}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def set_material(
    ctx: Context,
    object_name: str,
    material_name: Optional[str] = None,
    color: Optional[List[float]] = None
) -> str:
    try:
        blender = get_blender_connection()
        params = {"object_name": object_name}
        if material_name: params["material_name"] = material_name
        if color: params["color"] = color
        result = blender.send_command("set_material", params)
        return f"Applied material to {object_name}: {result.get('material_name', 'unknown')}"
    except Exception as e:
        return f"Error: {e!s}"
    
@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """Execute Python code in Blender synchronously and return its stdout.

    Best for quick operations. For large or long-running work (e.g. creating
    hundreds of objects, which can exceed the request/response timeout) use
    start_blender_job() instead — it returns immediately and is polled.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed: {result.get('result', '')}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def start_blender_job(ctx: Context, code: str) -> str:
    """Start long-running Blender Python code as a background job.

    Returns a job_id immediately (no timeout, even for very long work), because
    the code is scheduled on Blender's main loop and the socket call returns at
    once. Then poll get_blender_job_status(job_id) for progress and
    get_blender_job_result(job_id) for the final result + scene validation.
    The executed code receives a JOB dict and may set JOB["objects_created"]
    and JOB["stage"] for cooperative progress reporting.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("execute_code_async", {"code": code}, timeout=15)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def get_blender_job_status(ctx: Context, job_id: str) -> str:
    """Poll the status of a background Blender job (responsive even mid-run).

    Returns status (queued/running/done/failed), elapsed_sec, stage,
    objects_created and a stdout tail.
    """
    try:
        blender = get_blender_connection(check=False)
        result = blender.send_command("get_job_status", {"job_id": job_id}, timeout=15)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def get_blender_job_result(ctx: Context, job_id: str) -> str:
    """Fetch the final result of a background Blender job.

    Before completion returns ready=false; after completion returns the full
    stdout plus a validation snapshot (object_count, objects_created,
    materials_count, camera_exists, saved_path, engine).
    """
    try:
        blender = get_blender_connection(check=False)
        result = blender.send_command("get_job_result", {"job_id": job_id}, timeout=15)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def get_polyhaven_categories(ctx: Context, asset_type: str = "hdris") -> str:
    try:
        blender = get_blender_connection()
        if not _polyhaven_enabled: return "PolyHaven disabled."
        result = blender.send_command("get_polyhaven_categories", {"asset_type": asset_type})
        if "error" in result: return f"Error: {result['error']}"
        categories = result["categories"]
        formatted = f"Categories for {asset_type}:\n" + \
                    "\n".join(f"- {cat}: {count}" for cat, count in
                      sorted(categories.items(), key=lambda x: x[1], reverse=True))
        return formatted
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def search_polyhaven_assets(ctx: Context, asset_type: str = "all", categories: Optional[str] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("search_polyhaven_assets",
                {"asset_type": asset_type, "categories": categories})
        if "error" in result: return f"Error: {result['error']}"
        assets, total, returned = result["assets"], result["total_count"], result["returned_count"]
        formatted = f"Found {total} assets" + (f" in: {categories}" if categories else "") + \
                    f"\nShowing {returned}:\n" + "".join(
            f"- {data.get('name', asset_id)} (ID: {asset_id})\n"
            f"  Type: {['HDRI', 'Texture', 'Model'][data.get('type', 0)]}\n"
            f"  Categories: {', '.join(data.get('categories', []))}\n"
            f"  Downloads: {data.get('download_count', 'Unknown')}\n"
            for asset_id, data in sorted(assets.items(),
                                        key=lambda x: x[1].get("download_count", 0),
                                        reverse=True))
        return formatted
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def download_polyhaven_asset(ctx: Context, asset_id: str, asset_type: str,
                             resolution: str = "1k", file_format: Optional[str] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("download_polyhaven_asset", {
            "asset_id": asset_id, "asset_type": asset_type,
            "resolution": resolution, "file_format": file_format})
        if "error" in result: return f"Error: {result['error']}"
        if result.get("success"):
            message = result.get("message", "Success")
            if asset_type == "hdris": return f"{message}. HDRI set as world."
            elif asset_type == "textures":
                mat_name, maps = result.get("material", ""), ", ".join(result.get("maps", []))
                return f"{message}. Material '{mat_name}' with: {maps}."
            elif asset_type == "models": return f"{message}. Model imported."
            return message
        return f"Failed: {result.get('message', 'Unknown')}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def set_texture(ctx: Context, object_name: str, texture_id: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_texture",
                                     {"object_name": object_name, "texture_id": texture_id})
        if "error" in result: return f"Error: {result['error']}"
        if result.get("success"):
            mat_name, maps = result.get("material", ""), ", ".join(result.get("maps", []))
            info, nodes = result.get("material_info", {}), result.get("material_info", {}).get("texture_nodes", [])
            output = (f"Applied '{texture_id}' to {object_name}.\nMaterial '{mat_name}': {maps}.\n"
                      f"Nodes: {info.get('has_nodes', False)}\nCount: {info.get('node_count', 0)}\n")
            if nodes:
                output += "Texture nodes:\n" + "".join(
                    f"- {node['name']} ({node['image']})\n" +
                    ("  Connections:\n" + "".join(f"    {conn}\n" for conn in node['connections'])
                     if node['connections'] else "")
                    for node in nodes)
            return output
        return f"Failed: {result.get('message', 'Unknown')}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def get_polyhaven_status(ctx: Context) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_polyhaven_status")
        return result.get("message", "")  # Return the message directly
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
async def set_ollama_model(ctx: Context, model_name: str) -> str:
    global _ollama_model
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{_ollama_url}/api/show",
                                         json={"name": model_name}, timeout=10.0)
            if response.status_code == 200:
                _ollama_model = model_name
                return f"Ollama model set to: {_ollama_model}"
            else: return f"Error: Could not find model '{model_name}'."
    except Exception as e:
        return f"Error: Failed to communicate: {e!s}"

@mcp.tool()
async def set_ollama_url(ctx: Context, url: str) -> str:
    global _ollama_url
    if not (url.startswith("http://") or url.startswith("https://")):
        return "Error: Invalid URL format. Must start with http:// or https://."
    _ollama_url = url
    return f"Ollama URL set to: {_ollama_url}"

@mcp.tool()
async def get_ollama_models(ctx: Context) -> str:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{_ollama_url}/api/tags", timeout=10.0)
            response.raise_for_status()
            models_data = response.json()
            if "models" in models_data:
                model_list = [model["name"] for model in models_data["models"]]
                return "Available Ollama models:\n" + "\n".join(model_list)
            else: return "Error: Unexpected response from Ollama /api/tags."
    except httpx.HTTPStatusError as e:
        return f"Error: Ollama API error: {e.response.status_code}"
    except httpx.RequestError as e:
        return "Error: Failed to connect to Ollama API."
    except Exception as e:
        return f"Error: An unexpected error: {e!s}"

@mcp.tool()
async def render_image(ctx: Context, file_path: str = "render.png") -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("render_scene", {"output_path": file_path})
        if result and result.get("rendered"):
            # Use the actual output path returned from Blender
            actual_file_path = result.get("output_path")
            if not actual_file_path:
                return "Error: Blender rendered but did not return an output path."
            try:
                with open(actual_file_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    ctx.add_image(Image(data=encoded_string))  # Add image to the context
                    return "Image Rendered Successfully."
            except FileNotFoundError:
                return f"Error: Blender rendered to '{actual_file_path}', but the file was not found by the server."
            except Exception as exception:
                return f"Blender rendered, but the image could not be read: {exception!s}"
        else:
            return f"Error: Rendering failed with result: {result}"
    except Exception as e:
        return f"Error: {e!s}"

def main():
    """Run the MCP server."""
    parser = argparse.ArgumentParser(description="BlenderMCP Server")
    # Set global variables from command-line arguments
    global _ollama_url, _ollama_model

    parser.add_argument("--ollama-url", type=str, default=_ollama_url,
                        help="URL of the Ollama server")
    parser.add_argument("--ollama-model", type=str, default=_ollama_model,
                        help="Default Ollama model to use")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for the MCP server to listen on")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Host for the MCP server to listen on")
    parser.add_argument("--transport", type=str, default="streamable-http",
                        choices=["streamable-http", "sse", "stdio"],
                        help="MCP transport protocol (streamable-http recommended for remote/ChatGPT)")

    args = parser.parse_args()

    _ollama_url = args.ollama_url
    _ollama_model = args.ollama_model

    # mcp >=1.x: host/port are configured via settings; transport selected in run()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
