import bpy
import json
import threading
import socket
import time
import requests
import tempfile
from bpy.props import StringProperty, IntProperty
import traceback
import os
import shutil
try:
    import mathutils
except Exception:
    mathutils = None

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (0, 2),  # Updated version
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to local AI models via MCP",  # Updated description
    "category": "Interface",
}

class BlenderMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.client = None
        self.command_queue = []
        self.buffer = b''

    def start(self):
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.setblocking(False)
            bpy.app.timers.register(self._process_server, persistent=True)
            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False
        if hasattr(bpy.app.timers, "unregister"):
            if bpy.app.timers.is_registered(self._process_server):
                bpy.app.timers.unregister(self._process_server)
        if self.socket:
            self.socket.close()
        if self.client:
            self.client.close()
        self.socket = None
        self.client = None
        print("BlenderMCP server stopped")

    def _process_server(self):
        if not self.running:
            return None

        try:
            if not self.client and self.socket:
                try:
                    self.client, address = self.socket.accept()
                    self.client.setblocking(False)
                    print(f"Connected to client: {address}")
                except BlockingIOError:
                    pass
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")

            if self.client:
                try:
                    try:
                        data = self.client.recv(8192)
                        if data:
                            self.buffer += data
                            try:
                                command = json.loads(self.buffer.decode('utf-8'))
                                self.buffer = b''
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                self.client.sendall(response_json.encode('utf-8'))
                            except json.JSONDecodeError:
                                pass
                        else:
                            print("Client disconnected")
                            self.client.close()
                            self.client = None
                            self.buffer = b''
                    except BlockingIOError:
                        pass
                    except Exception as e:
                        print(f"Error receiving data: {str(e)}")
                        self.client.close()
                        self.client = None
                        self.buffer = b''

                except Exception as e:
                    print(f"Error with client: {str(e)}")
                    if self.client:
                        self.client.close()
                        self.client = None
                    self.buffer = b''

        except Exception as e:
            print(f"Server error: {str(e)}")

        return 0.1

    def execute_command(self, command):
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})
            if cmd_type in [
                "create_object",
                "create_camera",
                "create_light",
                "setup_three_point_lighting",
                "modify_object",
                "delete_object",
                "duplicate_object",
                "select_object",
                "add_modifier",
                "apply_modifier",
                "import_model",
                "export_model",
                "cleanup_scene",
                "set_keyframe",
                "create_turntable_animation",
            ]:
                if not bpy.context.screen or not bpy.context.screen.areas:
                    return {"status": "error", "message": "Suitable 'VIEW_3D' context not found for command execution."}

                view_3d_areas = [area for area in bpy.context.screen.areas if area.type == 'VIEW_3D']
                if not view_3d_areas:
                    return {"status": "error", "message": "Suitable 'VIEW_3D' context not found for command execution."}

                override = bpy.context.copy()
                override['area'] = view_3d_areas[0]
                with bpy.context.temp_override(**override):
                    return self._execute_command_internal(command)
            else:
                return self._execute_command_internal(command)
        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        cmd_type = command.get("type")
        params = command.get("params", {})

        if cmd_type == "get_polyhaven_status":
            return {"status": "success", "result": self.get_polyhaven_status()}

        handlers = {
            "get_blender_mcp_status": self.get_blender_mcp_status,
            "get_scene_info": self.get_scene_info,
            "list_objects": self.list_objects,
            "select_object": self.select_object,
            "rename_object": self.rename_object,
            "duplicate_object": self.duplicate_object,
            "hide_object": self.hide_object,
            "show_object": self.show_object,
            "create_object": self.create_object,
            "create_camera": self.create_camera,
            "create_light": self.create_light,
            "setup_three_point_lighting": self.setup_three_point_lighting,
            "modify_object": self.modify_object,
            "delete_object": self.delete_object,
            "get_object_info": self.get_object_info,
            "execute_code": self.execute_code,
            "set_material": self.set_material,
            "get_polyhaven_status": self.get_polyhaven_status,
            "set_render_engine": self.set_render_engine,
            "set_render_resolution": self.set_render_resolution,
            "list_modifiers": self.list_modifiers,
            "add_modifier": self.add_modifier,
            "set_modifier_property": self.set_modifier_property,
            "apply_modifier": self.apply_modifier,
            "remove_modifier": self.remove_modifier,
            "create_geometry_nodes_modifier": self.create_geometry_nodes_modifier,
            "scatter_objects_on_surface": self.scatter_objects_on_surface,
            "list_materials": self.list_materials,
            "create_principled_material": self.create_principled_material,
            "assign_material": self.assign_material,
            "import_model": self.import_model,
            "export_model": self.export_model,
            "save_blend_file": self.save_blend_file,
            "open_blend_file": self.open_blend_file,
            "validate_scene": self.validate_scene,
            "cleanup_scene": self.cleanup_scene,
            "set_frame_range": self.set_frame_range,
            "set_keyframe": self.set_keyframe,
            "create_turntable_animation": self.create_turntable_animation,
            "render_scene": self.render_scene
        }

        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
                "set_texture": self.set_texture,
            }
            handlers.update(polyhaven_handlers)

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}


    def get_simple_info(self):
        return {
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "scene_name": bpy.context.scene.name,
            "object_count": len(bpy.context.scene.objects)
        }

    def _object_summary(self, obj):
        return {
            "name": obj.name,
            "type": obj.type,
            "location": [round(float(obj.location.x), 4),
                         round(float(obj.location.y), 4),
                         round(float(obj.location.z), 4)],
            "rotation": [round(float(obj.rotation_euler.x), 4),
                         round(float(obj.rotation_euler.y), 4),
                         round(float(obj.rotation_euler.z), 4)],
            "scale": [round(float(obj.scale.x), 4),
                      round(float(obj.scale.y), 4),
                      round(float(obj.scale.z), 4)],
            "visible": bool(obj.visible_get()),
            "hidden_viewport": bool(obj.hide_viewport),
            "hidden_render": bool(obj.hide_render),
            "collection": obj.users_collection[0].name if obj.users_collection else None,
        }

    def get_blender_mcp_status(self):
        active = bpy.context.view_layer.objects.active
        selected = [obj.name for obj in bpy.context.selected_objects]
        addon_server = getattr(bpy.types, "blendermcp_server", None)
        return {
            "addon_version": ".".join(str(v) for v in bl_info.get("version", ())),
            "addon_running": bool(addon_server and addon_server.running),
            "addon_host": self.host,
            "addon_port": self.port,
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "blend_file": bpy.data.filepath or None,
            "scene_name": bpy.context.scene.name,
            "object_count": len(bpy.context.scene.objects),
            "selected_objects": selected,
            "active_object": active.name if active else None,
            "materials_count": len(bpy.data.materials),
            "polyhaven_enabled": bool(bpy.context.scene.blendermcp_use_polyhaven),
            "render_engine": bpy.context.scene.render.engine,
            "render_resolution": [
                bpy.context.scene.render.resolution_x,
                bpy.context.scene.render.resolution_y,
                bpy.context.scene.render.resolution_percentage,
            ],
        }

    def get_scene_info(self):
        try:
            print("Getting scene info...")
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def list_objects(self, include_hidden=True, limit=100):
        objects = []
        for obj in bpy.context.scene.objects:
            if not include_hidden and (obj.hide_viewport or obj.hide_render or not obj.visible_get()):
                continue
            objects.append(self._object_summary(obj))
            if len(objects) >= int(limit):
                break
        return {
            "count": len(objects),
            "total_scene_objects": len(bpy.context.scene.objects),
            "objects": objects,
        }

    def select_object(self, name, extend=False, make_active=True):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        if not extend:
            bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        if make_active:
            bpy.context.view_layer.objects.active = obj
        return {
            "selected": [item.name for item in bpy.context.selected_objects],
            "active_object": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        }

    def rename_object(self, old_name, new_name):
        obj = bpy.data.objects.get(old_name)
        if not obj:
            raise ValueError(f"Object not found: {old_name}")
        original_name = obj.name
        obj.name = new_name
        if hasattr(obj.data, "name"):
            obj.data.name = f"{obj.name}_data"
        return {"old_name": original_name, "new_name": obj.name}

    def duplicate_object(self, name, new_name=None, linked=False, offset=(0, 0, 0)):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        duplicate = obj.copy()
        source_data = getattr(obj, "data", None)
        if source_data is not None:
            duplicate.data = source_data if linked else source_data.copy()
        if new_name:
            duplicate.name = new_name
        duplicate.location = obj.location
        duplicate.location.x += float(offset[0])
        duplicate.location.y += float(offset[1])
        duplicate.location.z += float(offset[2])
        target_collection = obj.users_collection[0] if obj.users_collection else bpy.context.collection
        target_collection.objects.link(duplicate)
        bpy.ops.object.select_all(action='DESELECT')
        duplicate.select_set(True)
        bpy.context.view_layer.objects.active = duplicate
        return {
            "source": obj.name,
            "duplicate": self._object_summary(duplicate),
            "linked_data": bool(linked),
        }

    def hide_object(self, name, hide_render=True):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        obj.hide_viewport = True
        if hide_render:
            obj.hide_render = True
        return self._object_summary(obj)

    def show_object(self, name):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        obj.hide_viewport = False
        obj.hide_render = False
        return self._object_summary(obj)
    
    def render_scene(self, output_path=None, resolution_x=None, resolution_y=None):
        """Render the current scene"""
        try:
            if resolution_x is not None:
                bpy.context.scene.render.resolution_x = int(resolution_x)

            if resolution_y is not None:
                bpy.context.scene.render.resolution_y = int(resolution_y)

            if output_path:
                # Use absolute path and ensure directory exists.
                output_path = bpy.path.abspath(output_path)
                output_dir = os.path.dirname(output_path)
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                bpy.context.scene.render.filepath = output_path
            else: # If path not given save to a temp dir
                output_path = os.path.join(tempfile.gettempdir(),"render.png")
                bpy.context.scene.render.filepath = output_path


            # Render the scene
            bpy.ops.render.render(write_still=True) #Always write still even if no path given

            return {
                "rendered": True,
                "output_path": output_path ,
                "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
            }
        except Exception as e:
            print(f"Error in render_scene: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def create_object(self, type="CUBE", name=None, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1)):
        bpy.ops.object.select_all(action='DESELECT')
        if type == "CUBE":
            bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation, scale=scale)
        elif type == "SPHERE":
            bpy.ops.mesh.primitive_uv_sphere_add(location=location, rotation=rotation, scale=scale)
        elif type == "CYLINDER":
            bpy.ops.mesh.primitive_cylinder_add(location=location, rotation=rotation, scale=scale)
        elif type == "PLANE":
            bpy.ops.mesh.primitive_plane_add(location=location, rotation=rotation, scale=scale)
        elif type == "CONE":
            bpy.ops.mesh.primitive_cone_add(location=location, rotation=rotation, scale=scale)
        elif type == "TORUS":
            bpy.ops.mesh.primitive_torus_add(location=location, rotation=rotation, scale=scale)
        elif type == "EMPTY":
            bpy.ops.object.empty_add(location=location, rotation=rotation)
        elif type == "CAMERA":
            bpy.ops.object.camera_add(location=location, rotation=rotation)
        elif type == "LIGHT":
            bpy.ops.object.light_add(type='POINT', location=location, rotation=rotation)
        else:
            raise ValueError(f"Unsupported object type: {type}")

        obj = bpy.context.active_object
        if name:
            obj.name = name

        return {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
        }

    def create_camera(self, name=None, location=(0, -6, 3), rotation=(1.109319, 0, 0), lens=None, make_active=True):
        bpy.ops.object.camera_add(location=location, rotation=rotation)
        obj = bpy.context.active_object
        if name:
            obj.name = name
        if lens is not None:
            obj.data.lens = float(lens)
        if make_active:
            bpy.context.scene.camera = obj
        result = self._object_summary(obj)
        result["lens"] = obj.data.lens
        result["active_camera"] = bpy.context.scene.camera.name if bpy.context.scene.camera else None
        return result

    def create_light(self, type="AREA", name=None, location=(0, -3, 4), rotation=(0, 0, 0), power=500.0, color=None, size=None):
        light_type = type.upper()
        if light_type not in {"POINT", "SUN", "SPOT", "AREA"}:
            raise ValueError("Light type must be POINT, SUN, SPOT, or AREA")
        bpy.ops.object.light_add(type=light_type, location=location, rotation=rotation)
        obj = bpy.context.active_object
        if name:
            obj.name = name
        obj.data.energy = float(power)
        if color and len(color) >= 3:
            obj.data.color = (float(color[0]), float(color[1]), float(color[2]))
        if size is not None and hasattr(obj.data, "size"):
            obj.data.size = float(size)
        result = self._object_summary(obj)
        result["power"] = obj.data.energy
        result["light_type"] = obj.data.type
        return result

    def setup_three_point_lighting(self, target=(0, 0, 0), key_power=600.0, fill_power=180.0, back_power=350.0):
        specs = [
            ("Key Light", "AREA", (-4, -5, 5), key_power, 4.0),
            ("Fill Light", "AREA", (4, -4, 3), fill_power, 5.0),
            ("Back Light", "POINT", (0, 4, 4), back_power, None),
        ]
        created = []
        for name, light_type, location, power, size in specs:
            if bpy.data.objects.get(name):
                bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
            light = self.create_light(
                type=light_type,
                name=name,
                location=location,
                power=power,
                size=size,
            )
            obj = bpy.data.objects[light["name"]]
            direction = mathutils.Vector(target) - obj.location if mathutils else None
            if direction:
                obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
            created.append(light)
        return {"created": created}

    def modify_object(self, name, location=None, rotation=None, scale=None, visible=None):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        if location is not None:
            obj.location = location
        if rotation is not None:
            obj.rotation_euler = rotation
        if scale is not None:
            obj.scale = scale
        if visible is not None:
            obj.hide_viewport = not visible
            obj.hide_render = not visible

        return {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
        }

    def delete_object(self, name):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        obj_name = obj.name
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.ops.object.delete()

        return {"deleted": obj_name}

    def get_object_info(self, name):
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def execute_code(self, code):
        try:
            namespace = {"bpy": bpy}
            exec(code, namespace)
            return {"executed": True}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")

    def set_material(self, object_name, material_name=None, create_if_missing=True, color=None):
        """Set or create a material for an object."""
        try:
            obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")

            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                raise ValueError(f"Object {object_name} cannot accept materials")
            if material_name:
                mat = bpy.data.materials.get(material_name)
                if not mat and create_if_missing:
                    mat = bpy.data.materials.new(name=material_name)
                    print(f"Created new material: {material_name}")
            else:
                mat_name = f"{object_name}_material"
                mat = bpy.data.materials.get(mat_name)
                if not mat:
                    mat = bpy.data.materials.new(name=mat_name)
                material_name = mat_name
                print(f"Using material: {mat_name}")

            if mat:
                if not mat.use_nodes:
                    mat.use_nodes = True
                principled = mat.node_tree.nodes.get('Principled BSDF')
                if not principled:
                    principled = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    output = mat.node_tree.nodes.get('Material Output')
                    if not output:
                        output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                    if not principled.outputs[0].links:
                         mat.node_tree.links.new(principled.outputs[0], output.inputs[0])

                if color and len(color) >= 3:
                    principled.inputs['Base Color'].default_value = (
                        color[0],
                        color[1],
                        color[2],
                        1.0 if len(color) < 4 else color[3]
                    )
                    print(f"Set material color to {color}")

            if mat:
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    obj.data.materials[0] = mat
                print(f"Assigned material {mat.name} to object {object_name}")
                return {
                    "status": "success",
                    "object": object_name,
                    "material": mat.name,
                    "color": color if color else None
                }
            else:
                raise ValueError(f"Failed to create or find material: {material_name}")
        except Exception as e:
            print(f"Error in set_material: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "object": object_name,
                "material": material_name if 'material_name' in locals() else None
            }

    def set_render_engine(self, engine="CYCLES", samples=None):
        engine = engine.upper()
        valid = {"BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"}
        if engine == "EEVEE":
            engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in valid else "BLENDER_EEVEE"
        try:
            bpy.context.scene.render.engine = engine
        except Exception:
            raise ValueError(f"Unsupported render engine: {engine}")
        if samples is not None:
            sample_count = int(samples)
            if engine == "CYCLES" and hasattr(bpy.context.scene, "cycles"):
                bpy.context.scene.cycles.samples = sample_count
            elif hasattr(bpy.context.scene, "eevee"):
                bpy.context.scene.eevee.taa_render_samples = sample_count
        return {
            "render_engine": bpy.context.scene.render.engine,
            "samples": samples,
        }

    def set_render_resolution(self, width=1920, height=1080, percentage=100):
        bpy.context.scene.render.resolution_x = int(width)
        bpy.context.scene.render.resolution_y = int(height)
        bpy.context.scene.render.resolution_percentage = int(percentage)
        return {
            "resolution": [
                bpy.context.scene.render.resolution_x,
                bpy.context.scene.render.resolution_y,
                bpy.context.scene.render.resolution_percentage,
            ]
        }

    def _get_object(self, object_name):
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")
        return obj

    def _modifier_summary(self, modifier):
        summary = {
            "name": modifier.name,
            "type": modifier.type,
            "show_viewport": bool(modifier.show_viewport),
            "show_render": bool(modifier.show_render),
        }
        for attr in ("width", "segments", "levels", "render_levels", "count", "use_clip", "operation"):
            if hasattr(modifier, attr):
                try:
                    summary[attr] = getattr(modifier, attr)
                except Exception:
                    pass
        return summary

    def list_modifiers(self, object_name):
        obj = self._get_object(object_name)
        return {
            "object": obj.name,
            "modifiers": [self._modifier_summary(mod) for mod in obj.modifiers],
        }

    def add_modifier(self, object_name, modifier_type, modifier_name=None, properties=None):
        obj = self._get_object(object_name)
        mod_type = modifier_type.upper()
        name = modifier_name or mod_type.title().replace("_", " ")
        modifier = obj.modifiers.new(name=name, type=mod_type)
        for key, value in (properties or {}).items():
            if hasattr(modifier, key):
                setattr(modifier, key, value)
            else:
                modifier[key] = value
        return {
            "object": obj.name,
            "modifier": self._modifier_summary(modifier),
        }

    def set_modifier_property(self, object_name, modifier_name, property_name, value):
        obj = self._get_object(object_name)
        modifier = obj.modifiers.get(modifier_name)
        if not modifier:
            raise ValueError(f"Modifier not found: {modifier_name}")
        if hasattr(modifier, property_name):
            setattr(modifier, property_name, value)
        else:
            modifier[property_name] = value
        return {
            "object": obj.name,
            "modifier": self._modifier_summary(modifier),
            "property": property_name,
            "value": value,
        }

    def apply_modifier(self, object_name, modifier_name):
        obj = self._get_object(object_name)
        if not obj.modifiers.get(modifier_name):
            raise ValueError(f"Modifier not found: {modifier_name}")
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=modifier_name)
        return {"object": obj.name, "applied_modifier": modifier_name}

    def remove_modifier(self, object_name, modifier_name):
        obj = self._get_object(object_name)
        modifier = obj.modifiers.get(modifier_name)
        if not modifier:
            raise ValueError(f"Modifier not found: {modifier_name}")
        obj.modifiers.remove(modifier)
        return {"object": obj.name, "removed_modifier": modifier_name}

    def create_geometry_nodes_modifier(self, object_name, modifier_name="Geometry Nodes"):
        obj = self._get_object(object_name)
        modifier = obj.modifiers.new(name=modifier_name, type='NODES')
        try:
            node_group = bpy.data.node_groups.new(f"{obj.name}_{modifier_name}", 'GeometryNodeTree')
            modifier.node_group = node_group
        except Exception as e:
            modifier["node_group_note"] = f"Node group creation skipped: {e}"
        return {
            "object": obj.name,
            "modifier": self._modifier_summary(modifier),
            "node_group": modifier.node_group.name if getattr(modifier, "node_group", None) else None,
        }

    def scatter_objects_on_surface(self, surface_object, instance_object, count=100, seed=1):
        surface = self._get_object(surface_object)
        instance = self._get_object(instance_object)
        modifier = surface.modifiers.get("Scatter Instances") or surface.modifiers.new(name="Scatter Instances", type='NODES')
        modifier["instance_object"] = instance.name
        modifier["count"] = int(count)
        modifier["seed"] = int(seed)
        modifier["preset"] = "scatter_objects_on_surface"
        return {
            "surface_object": surface.name,
            "instance_object": instance.name,
            "modifier": self._modifier_summary(modifier),
            "note": "Scatter preset metadata was stored on a Geometry Nodes modifier; node graph wiring may need Blender-version-specific refinement.",
        }

    def list_materials(self, limit=100):
        materials = []
        for mat in bpy.data.materials:
            materials.append({
                "name": mat.name,
                "use_nodes": bool(mat.use_nodes),
                "users": mat.users,
                "node_count": len(mat.node_tree.nodes) if mat.use_nodes and mat.node_tree else 0,
            })
            if len(materials) >= int(limit):
                break
        return {
            "count": len(materials),
            "total_materials": len(bpy.data.materials),
            "materials": materials,
        }

    def create_principled_material(self, material_name, base_color=None, roughness=None, metallic=None):
        mat = bpy.data.materials.get(material_name) or bpy.data.materials.new(name=material_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        principled = nodes.get('Principled BSDF') or nodes.new('ShaderNodeBsdfPrincipled')
        if base_color and len(base_color) >= 3:
            principled.inputs['Base Color'].default_value = (
                float(base_color[0]),
                float(base_color[1]),
                float(base_color[2]),
                float(base_color[3]) if len(base_color) > 3 else 1.0,
            )
        if roughness is not None and 'Roughness' in principled.inputs:
            principled.inputs['Roughness'].default_value = float(roughness)
        if metallic is not None and 'Metallic' in principled.inputs:
            principled.inputs['Metallic'].default_value = float(metallic)
        return {
            "material": mat.name,
            "use_nodes": bool(mat.use_nodes),
            "node_count": len(nodes),
        }

    def assign_material(self, object_name, material_name, slot_index=0):
        obj = self._get_object(object_name)
        mat = bpy.data.materials.get(material_name)
        if not mat:
            raise ValueError(f"Material not found: {material_name}")
        if not hasattr(obj, "data") or not hasattr(obj.data, "materials"):
            raise ValueError(f"Object cannot accept materials: {object_name}")
        index = int(slot_index)
        while len(obj.data.materials) <= index:
            obj.data.materials.append(None)
        obj.data.materials[index] = mat
        return {"object": obj.name, "material": mat.name, "slot_index": index}

    def _detect_format(self, file_path, file_format=None):
        if file_format:
            return file_format.lower().lstrip(".")
        return os.path.splitext(file_path)[1].lower().lstrip(".")

    def import_model(self, file_path, file_format=None):
        path = bpy.path.abspath(file_path)
        if not os.path.exists(path):
            raise ValueError(f"File not found: {path}")
        fmt = self._detect_format(path, file_format)
        before = set(bpy.data.objects.keys())
        if fmt in {"glb", "gltf"}:
            bpy.ops.import_scene.gltf(filepath=path)
        elif fmt == "fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        elif fmt == "obj":
            bpy.ops.import_scene.obj(filepath=path)
        elif fmt == "stl":
            bpy.ops.import_mesh.stl(filepath=path)
        elif fmt == "ply":
            bpy.ops.import_mesh.ply(filepath=path)
        elif fmt in {"usd", "usda", "usdc", "usdz"} and hasattr(bpy.ops.wm, "usd_import"):
            bpy.ops.wm.usd_import(filepath=path)
        elif fmt == "blend":
            with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
                data_to.objects = data_from.objects
            for obj in data_to.objects:
                if obj is not None:
                    bpy.context.collection.objects.link(obj)
        else:
            raise ValueError(f"Unsupported import format: {fmt}")
        imported = sorted(set(bpy.data.objects.keys()) - before)
        return {"file_path": path, "format": fmt, "imported_objects": imported}

    def export_model(self, file_path, file_format=None, selected_only=False):
        path = bpy.path.abspath(file_path)
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        fmt = self._detect_format(path, file_format)
        if fmt in {"glb", "gltf"}:
            bpy.ops.export_scene.gltf(filepath=path, use_selection=bool(selected_only))
        elif fmt == "fbx":
            bpy.ops.export_scene.fbx(filepath=path, use_selection=bool(selected_only))
        elif fmt == "obj":
            bpy.ops.export_scene.obj(filepath=path, use_selection=bool(selected_only))
        elif fmt == "stl":
            bpy.ops.export_mesh.stl(filepath=path, use_selection=bool(selected_only))
        elif fmt == "ply":
            bpy.ops.export_mesh.ply(filepath=path, use_selection=bool(selected_only))
        elif fmt in {"usd", "usda", "usdc"} and hasattr(bpy.ops.wm, "usd_export"):
            bpy.ops.wm.usd_export(filepath=path, selected_objects_only=bool(selected_only))
        else:
            raise ValueError(f"Unsupported export format: {fmt}")
        return {"file_path": path, "format": fmt, "selected_only": bool(selected_only), "exists": os.path.exists(path)}

    def save_blend_file(self, file_path=None):
        path = bpy.path.abspath(file_path) if file_path else bpy.data.filepath
        if not path:
            raise ValueError("No file path provided and the current blend file has not been saved.")
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return {"file_path": path, "saved": True}

    def open_blend_file(self, file_path):
        path = bpy.path.abspath(file_path)
        if not os.path.exists(path):
            raise ValueError(f"File not found: {path}")
        bpy.ops.wm.open_mainfile(filepath=path)
        return {"file_path": path, "opened": True}

    def validate_scene(self):
        missing_textures = []
        for image in bpy.data.images:
            path = bpy.path.abspath(image.filepath) if getattr(image, "filepath", "") else ""
            if path and not image.packed_file and not os.path.exists(path):
                missing_textures.append({"image": image.name, "path": path})
        zero_scale = [
            obj.name for obj in bpy.context.scene.objects
            if abs(obj.scale.x) == 0 or abs(obj.scale.y) == 0 or abs(obj.scale.z) == 0
        ]
        mesh_count = sum(1 for obj in bpy.context.scene.objects if obj.type == 'MESH')
        camera_count = sum(1 for obj in bpy.context.scene.objects if obj.type == 'CAMERA')
        light_count = sum(1 for obj in bpy.context.scene.objects if obj.type == 'LIGHT')
        warnings = []
        if camera_count == 0:
            warnings.append("No camera in scene.")
        if light_count == 0:
            warnings.append("No light in scene.")
        if missing_textures:
            warnings.append(f"{len(missing_textures)} missing texture(s).")
        if zero_scale:
            warnings.append(f"{len(zero_scale)} object(s) have zero scale.")
        return {
            "ok": not warnings,
            "warnings": warnings,
            "object_count": len(bpy.context.scene.objects),
            "mesh_count": mesh_count,
            "camera_count": camera_count,
            "light_count": light_count,
            "missing_textures": missing_textures,
            "zero_scale_objects": zero_scale,
            "render_engine": bpy.context.scene.render.engine,
            "resolution": [
                bpy.context.scene.render.resolution_x,
                bpy.context.scene.render.resolution_y,
                bpy.context.scene.render.resolution_percentage,
            ],
        }

    def cleanup_scene(self, remove_unused_data=True, remove_empty_collections=True):
        removed_collections = []
        if remove_empty_collections:
            for collection in list(bpy.data.collections):
                if not collection.objects and not collection.children:
                    removed_collections.append(collection.name)
                    bpy.data.collections.remove(collection)
        purge_runs = 0
        if remove_unused_data:
            for _ in range(3):
                try:
                    bpy.ops.outliner.orphans_purge(do_recursive=True)
                    purge_runs += 1
                except Exception:
                    break
        return {
            "removed_empty_collections": removed_collections,
            "orphan_purge_runs": purge_runs,
            "remaining_objects": len(bpy.context.scene.objects),
            "remaining_materials": len(bpy.data.materials),
        }

    def set_frame_range(self, start=1, end=120, current=None):
        bpy.context.scene.frame_start = int(start)
        bpy.context.scene.frame_end = int(end)
        if current is not None:
            bpy.context.scene.frame_set(int(current))
        return {
            "frame_start": bpy.context.scene.frame_start,
            "frame_end": bpy.context.scene.frame_end,
            "frame_current": bpy.context.scene.frame_current,
        }

    def set_keyframe(self, object_name, frame, data_path="location"):
        obj = self._get_object(object_name)
        obj.keyframe_insert(data_path=data_path, frame=int(frame))
        return {"object": obj.name, "frame": int(frame), "data_path": data_path}

    def create_turntable_animation(self, object_name, start=1, end=120, axis="Z"):
        obj = self._get_object(object_name)
        start_frame = int(start)
        end_frame = int(end)
        axis = axis.upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("Axis must be X, Y, or Z")
        self.set_frame_range(start_frame, end_frame, start_frame)
        index = {"X": 0, "Y": 1, "Z": 2}[axis]
        original = list(obj.rotation_euler)
        obj.rotation_euler[index] = original[index]
        obj.keyframe_insert(data_path="rotation_euler", frame=start_frame)
        obj.rotation_euler[index] = original[index] + 6.283185307179586
        obj.keyframe_insert(data_path="rotation_euler", frame=end_frame)
        if obj.animation_data and obj.animation_data.action:
            for fcurve in obj.animation_data.action.fcurves:
                for keyframe in fcurve.keyframe_points:
                    keyframe.interpolation = 'LINEAR'
        return {
            "object": obj.name,
            "axis": axis,
            "frame_start": start_frame,
            "frame_end": end_frame,
            "interpolation": "LINEAR",
        }
    def get_polyhaven_categories(self, asset_type):
        """Get categories for a specific asset type from Polyhaven"""
        try:
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}

            response = requests.get(f"https://api.polyhaven.com/categories/{asset_type}")
            if response.status_code == 200:
                return {"categories": response.json()}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def search_polyhaven_assets(self, asset_type=None, categories=None):
        """Search for assets from Polyhaven with optional filtering"""
        try:
            url = "https://api.polyhaven.com/assets"
            params = {}

            if asset_type and asset_type != "all":
                if asset_type not in ["hdris", "textures", "models"]:
                    return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                params["type"] = asset_type

            if categories:
                params["categories"] = categories

            response = requests.get(url, params=params)
            if response.status_code == 200:
                assets = response.json()
                limited_assets = {}
                for i, (key, value) in enumerate(assets.items()):
                    if i >= 20:
                        break
                    limited_assets[key] = value

                return {"assets": limited_assets, "total_count": len(assets), "returned_count": len(limited_assets)}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def download_polyhaven_asset(self, asset_id, asset_type, resolution="1k", file_format=None):
        """Downloads and imports a PolyHaven asset."""
        try:
            files_response = requests.get(f"https://api.polyhaven.com/files/{asset_id}")
            if files_response.status_code != 200:
                return {"error": f"Failed to get asset files: {files_response.status_code}"}

            files_data = files_response.json()

            if asset_type == "hdris":
                if not file_format:
                    file_format = "hdr"
                if "hdri" in files_data and resolution in files_data["hdri"] and file_format in files_data["hdri"][resolution]:
                    file_info = files_data["hdri"][resolution][file_format]
                    file_url = file_info["url"]

                    tmp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                            response = requests.get(file_url)
                            if response.status_code != 200:
                                return {"error": f"Failed to download HDRI: {response.status_code}"}
                            tmp_file.write(response.content)
                            tmp_path = tmp_file.name

                        if not bpy.data.worlds:
                            bpy.data.worlds.new("World")
                        world = bpy.data.worlds[0]
                        world.use_nodes = True
                        node_tree = world.node_tree
                        for node in node_tree.nodes:
                            node_tree.nodes.remove(node)
                        tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
                        tex_coord.location = (-800, 0)
                        mapping = node_tree.nodes.new(type='ShaderNodeMapping')
                        mapping.location = (-600, 0)
                        env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
                        env_tex.location = (-400, 0)
                        env_tex.image = bpy.data.images.load(tmp_path)
                        if file_format.lower() == 'exr':
                            try:
                                env_tex.image.colorspace_settings.name = 'Linear'
                            except:
                                env_tex.image.colorspace_settings.name = 'Non-Color'
                        else:
                            for color_space in ['Linear', 'Linear Rec.709', 'Non-Color']:
                                try:
                                    env_tex.image.colorspace_settings.name = color_space
                                    break
                                except:
                                    continue
                        background = node_tree.nodes.new(type='ShaderNodeBackground')
                        background.location = (-200, 0)
                        output = node_tree.nodes.new(type='ShaderNodeOutputWorld')
                        output.location = (0, 0)
                        node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
                        node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
                        node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
                        node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])

                        bpy.context.scene.world = world

                        return {
                            "success": True,
                            "message": f"HDRI {asset_id} imported successfully",
                            "image_name": env_tex.image.name
                        }
                    except Exception as e:
                        return {"error": f"Failed to set up HDRI: {str(e)}"}
                    finally:
                        if tmp_path and os.path.exists(tmp_path):
                            os.remove(tmp_path)
                else:
                    return {"error": f"Resolution/format unavailable."}

            elif asset_type == "textures":
                if not file_format:
                    file_format = "jpg"

                downloaded_maps = {}
                try:
                    for map_type in files_data:
                        if map_type not in ["blend", "gltf"]:
                            if resolution in files_data[map_type] and file_format in files_data[map_type][resolution]:
                                file_info = files_data[map_type][resolution][file_format]
                                file_url = file_info["url"]

                                with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                                    response = requests.get(file_url)
                                    if response.status_code == 200:
                                        tmp_file.write(response.content)
                                        tmp_path = tmp_file.name
                                        image = bpy.data.images.load(tmp_path)
                                        image.name = f"{asset_id}_{map_type}.{file_format}"
                                        image.pack()
                                        if map_type in ['color', 'diffuse', 'albedo']:
                                            try:
                                                image.colorspace_settings.name = 'sRGB'
                                            except:
                                                pass
                                        else:
                                            try:
                                                image.colorspace_settings.name = 'Non-Color'
                                            except:
                                                pass
                                        downloaded_maps[map_type] = image
                                        try:
                                            os.unlink(tmp_path)
                                        except:
                                            pass

                    if not downloaded_maps:
                        return {"error": f"No texture maps found."}

                    mat = bpy.data.materials.new(name=asset_id)
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    for node in nodes:
                        nodes.remove(node)
                    output = nodes.new(type='ShaderNodeOutputMaterial')
                    output.location = (300, 0)
                    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                    principled.location = (0, 0)
                    links.new(principled.outputs[0], output.inputs[0])
                    tex_coord = nodes.new(type='ShaderNodeTexCoord')
                    tex_coord.location = (-800, 0)
                    mapping = nodes.new(type='ShaderNodeMapping')
                    mapping.location = (-600, 0)
                    mapping.vector_type = 'TEXTURE'
                    links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
                    x_pos = -400
                    y_pos = 300

                    for map_type, image in downloaded_maps.items():
                        tex_node = nodes.new(type='ShaderNodeTexImage')
                        tex_node.location = (x_pos, y_pos)
                        tex_node.image = image
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            try:
                                tex_node.image.colorspace_settings.name = 'sRGB'
                            except:
                                pass
                        else:
                            try:
                                tex_node.image.colorspace_settings.name = 'Non-Color'
                            except:
                                pass
                        links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])

                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                        elif map_type.lower() in ['roughness', 'rough']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                        elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                        elif map_type.lower() in ['normal', 'nor']:
                            normal_map = nodes.new(type='ShaderNodeNormalMap')
                            normal_map.location = (x_pos + 200, y_pos)
                            links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                        elif map_type in ['displacement', 'disp', 'height']:
                            disp_node = nodes.new(type='ShaderNodeDisplacement')
                            disp_node.location = (x_pos + 200, y_pos - 200)
                            links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                            links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                        y_pos -= 250
                    return {
                        "success": True,
                        "message": f"Texture {asset_id} imported as material",
                        "material": mat.name,
                        "maps": list(downloaded_maps.keys())
                    }
                except Exception as e:
                    return {"error": f"Failed to process textures: {str(e)}"}

            elif asset_type == "models":
                if not file_format:
                    file_format = "gltf"
                if file_format in files_data and resolution in files_data[file_format]:
                    file_info = files_data[file_format][resolution][file_format]
                    file_url = file_info["url"]
                    temp_dir = tempfile.mkdtemp()
                    main_file_path = ""
                    try:
                        main_file_name = file_url.split("/")[-1]
                        main_file_path = os.path.join(temp_dir, main_file_name)
                        response = requests.get(file_url)
                        if response.status_code != 200:
                            return {"error": f"Failed to download model: {response.status_code}"}
                        with open(main_file_path, "wb") as f:
                            f.write(response.content)
                        if "include" in file_info and file_info["include"]:
                            for include_path, include_info in file_info["include"].items():
                                include_url = include_info["url"]
                                include_file_path = os.path.join(temp_dir, include_path)
                                os.makedirs(os.path.dirname(include_file_path), exist_ok=True)
                                include_response = requests.get(include_url)
                                if include_response.status_code == 200:
                                    with open(include_file_path, "wb") as f:
                                        f.write(include_response.content)
                                else:
                                    print(f"Failed to download included file: {include_path}")
                        if file_format == "gltf" or file_format == "glb":
                            bpy.ops.import_scene.gltf(filepath=main_file_path)
                        elif file_format == "fbx":
                            bpy.ops.import_scene.fbx(filepath=main_file_path)
                        elif file_format == "obj":
                            bpy.ops.import_scene.obj(filepath=main_file_path)
                        elif file_format == "blend":
                            with bpy.data.libraries.load(main_file_path, link=False) as (data_from, data_to):
                                data_to.objects = data_from.objects
                            for obj in data_to.objects:
                                if obj is not None:
                                    bpy.context.collection.objects.link(obj)
                        else:
                            return {"error": f"Unsupported model format: {file_format}"}
                        imported_objects = [obj.name for obj in bpy.context.selected_objects]

                        return {
                            "success": True,
                            "message": f"Model {asset_id} imported successfully",
                            "imported_objects": imported_objects
                        }
                    except Exception as e:
                        return {"error": f"Failed to import model: {str(e)}"}
                    finally:
                        try:
                            shutil.rmtree(temp_dir)
                        except:
                            print(f"Failed to clean up: {temp_dir}")
                else:
                    return {"error": f"Format/resolution unavailable."}
            else:
                return {"error": f"Unsupported asset type: {asset_type}"}
        except Exception as e:
            return {"error": f"Failed to download asset: {str(e)}"}

    def set_texture(self, object_name, texture_id):
        """Apply a previously downloaded Polyhaven texture."""
        try:
            obj = bpy.data.objects.get(object_name)
            if not obj:
                return {"error": f"Object not found: {object_name}"}
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                return {"error": f"Object {object_name} cannot accept materials"}

            texture_images = {}
            for img in bpy.data.images:
                if img.name.startswith(texture_id + "_"):
                    map_type = img.name.split('_')[-1].split('.')[0]
                    img.reload()
                    if map_type.lower() in ['color', 'diffuse', 'albedo']:
                        try:
                            img.colorspace_settings.name = 'sRGB'
                        except:
                            pass
                    else:
                        try:
                            img.colorspace_settings.name = 'Non-Color'
                        except:
                            pass
                    if not img.packed_file:
                        img.pack()
                    texture_images[map_type] = img
                    print(f"Loaded: {map_type} - {img.name}")
                    print(f"Size: {img.size[0]}x{img.size[1]}")
                    print(f"Colorspace: {img.colorspace_settings.name}")
                    print(f"Format: {img.file_format}")
                    print(f"Packed: {bool(img.packed_file)}")

            if not texture_images:
                return {"error": f"No images found for: {texture_id}."}

            new_mat_name = f"{texture_id}_material_{object_name}"
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                bpy.data.materials.remove(existing_mat)

            new_mat = bpy.data.materials.new(name=new_mat_name)
            new_mat.use_nodes = True
            nodes = new_mat.node_tree.nodes
            links = new_mat.node_tree.links
            nodes.clear()
            output = nodes.new(type='ShaderNodeOutputMaterial')
            output.location = (600, 0)
            principled = nodes.new(type='ShaderNodeBsdfPrincipled')
            principled.location = (300, 0)
            links.new(principled.outputs[0], output.inputs[0])
            tex_coord = nodes.new(type='ShaderNodeTexCoord')
            tex_coord.location = (-800, 0)
            mapping = nodes.new(type='ShaderNodeMapping')
            mapping.location = (-600, 0)
            mapping.vector_type = 'TEXTURE'
            links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
            x_pos = -400
            y_pos = 300

            for map_type, image in texture_images.items():
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.location = (x_pos, y_pos)
                tex_node.image = image

                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    try:
                        tex_node.image.colorspace_settings.name = 'sRGB'
                    except:
                        pass
                else:
                    try:
                        tex_node.image.colorspace_settings.name = 'Non-Color'
                    except:
                        pass
                links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                elif map_type.lower() in ['roughness', 'rough']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                elif map_type.lower() in ['normal', 'nor', 'dx', 'gl']:
                    normal_map = nodes.new(type='ShaderNodeNormalMap')
                    normal_map.location = (x_pos + 200, y_pos)
                    links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                    links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                elif map_type.lower() in ['displacement', 'disp', 'height']:
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (x_pos + 200, y_pos - 200)
                    disp_node.inputs['Scale'].default_value = 0.1
                    links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])

                y_pos -= 250

            texture_nodes = {}
            for node in nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break
            for map_name in ['color', 'diffuse', 'albedo']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Base Color'])
                    print(f"Connected {map_name} to Base Color")
                    break
            for map_name in ['roughness', 'rough']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Roughness'])
                    print(f"Connected {map_name} to Roughness")
                    break

            for map_name in ['metallic', 'metalness', 'metal']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Metallic'])
                    print(f"Connected {map_name} to Metallic")
                    break
            for map_name in ['gl', 'dx', 'nor']:
                if map_name in texture_nodes:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (100, 100)
                    links.new(texture_nodes[map_name].outputs['Color'], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], principled.inputs['Normal'])
                    print(f"Connected {map_name} to Normal")
                    break
            for map_name in ['displacement', 'disp', 'height']:
                if map_name in texture_nodes:
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (300, -200)
                    disp_node.inputs['Scale'].default_value = 0.1
                    links.new(texture_nodes[map_name].outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                    print(f"Connected {map_name} to Displacement")
                    break
            if 'arm' in texture_nodes:
                separate_rgb = nodes.new(type='ShaderNodeSeparateRGB')
                separate_rgb.location = (-200, -100)
                links.new(texture_nodes['arm'].outputs['Color'], separate_rgb.inputs['Image'])
                if not any(map_name in texture_nodes for map_name in ['roughness', 'rough']):
                    links.new(separate_rgb.outputs['G'], principled.inputs['Roughness'])
                    print("Connected ARM.G to Roughness")
                if not any(map_name in texture_nodes for map_name in ['metallic', 'metalness', 'metal']):
                    links.new(separate_rgb.outputs['B'], principled.inputs['Metallic'])
                    print("Connected ARM.B to Metallic")
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break
                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(separate_rgb.outputs['R'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected ARM.R to AO mix with Base Color")

            if 'ao' in texture_nodes:
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8

                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)

                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(texture_nodes['ao'].outputs['Color'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected AO to mix with Base Color")

            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)

            obj.data.materials.append(new_mat)
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.context.view_layer.update()
            texture_maps = list(texture_images.keys())

            material_info = {
                "name": new_mat.name,
                "has_nodes": new_mat.use_nodes,
                "node_count": len(new_mat.node_tree.nodes),
                "texture_nodes": []
            }

            for node in new_mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    connections = []
                    for output in node.outputs:
                        for link in output.links:
                            connections.append(f"{output.name} → {link.to_node.name}.{link.to_socket.name}")

                    material_info["texture_nodes"].append({
                        "name": node.name,
                        "image": node.image.name,
                        "colorspace": node.image.colorspace_settings.name,
                        "connections": connections
                    })

            return {
                "success": True,
                "message": f"Created new material and applied texture {texture_id} to {object_name}",
                "material": new_mat.name,
                "maps": texture_maps,
                "material_info": material_info
            }

        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}

    def get_polyhaven_status(self):
        enabled = bpy.context.scene.blendermcp_use_polyhaven
        if enabled:
            return {"enabled": True, "message": "PolyHaven integration is enabled and ready to use."}
        else:
            return {
                "enabled": False,
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection"""
        }

class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "blendermcp_port")
        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")

        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Start MCP Server")
        else:
            layout.operator("blendermcp.stop_server", text="Stop MCP Server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")

class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Local AI"  # Updated label
    bl_description = "Start the BlenderMCP server to connect with a local AI model" # Updated description

    def execute(self, context):
        scene = context.scene
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = True
        return {'FINISHED'}

class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection" # Updated
    bl_description = "Stop Server" # Updated

    def execute(self, context):
        scene = context.scene
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server
        scene.blendermcp_server_running = False
        return {'FINISHED'}

def register():
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535
    )
    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )
    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )
    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)
    print("BlenderMCP addon registered")

def unregister():
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_use_polyhaven
    print("BlenderMCP addon unregistered")

if __name__ == "__main__":
    register()
