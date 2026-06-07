import sys
import os
# Add src to the path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import tempfile
import base64
from mcp.server.fastmcp import Context, Image

# Now import the server module
from blender_open_mcp import server as server_module

class TestServerTools(unittest.TestCase):

    def test_set_ollama_url(self):
        """Test the set_ollama_url function."""
        ctx = Context()
        new_url = "http://localhost:12345"

        # Run the async function
        result = asyncio.run(server_module.set_ollama_url(ctx, new_url))

        self.assertEqual(result, f"Ollama URL set to: {new_url}")
        self.assertEqual(server_module._ollama_url, new_url)

    def test_list_objects_sends_command(self):
        ctx = MagicMock()
        mock_blender_conn = MagicMock()
        mock_blender_conn.send_command.return_value = {
            "count": 1,
            "objects": [{"name": "Cube", "type": "MESH"}],
        }

        with patch('blender_open_mcp.server.get_blender_connection', return_value=mock_blender_conn):
            result = server_module.list_objects(ctx, include_hidden=False, limit=10)

        mock_blender_conn.send_command.assert_called_once_with(
            "list_objects",
            {"include_hidden": False, "limit": 10}
        )
        self.assertIn('"Cube"', result)

    def test_create_camera_sends_command(self):
        ctx = MagicMock()
        mock_blender_conn = MagicMock()
        mock_blender_conn.send_command.return_value = {
            "name": "Camera_Product",
            "type": "CAMERA",
            "active_camera": "Camera_Product",
        }

        with patch('blender_open_mcp.server.get_blender_connection', return_value=mock_blender_conn):
            result = server_module.create_camera(
                ctx,
                name="Camera_Product",
                location=[1, -5, 3],
                lens=70,
            )

        mock_blender_conn.send_command.assert_called_once_with(
            "create_camera",
            {
                "name": "Camera_Product",
                "location": [1, -5, 3],
                "rotation": [1.109319, 0, 0],
                "lens": 70,
                "make_active": True,
            }
        )
        self.assertIn('"active_camera": "Camera_Product"', result)

    def test_status_reports_offline_bridge(self):
        ctx = MagicMock()
        server_module._blender_connection = None

        with patch('blender_open_mcp.server.BlenderConnection') as connection_cls:
            probe = MagicMock()
            probe.connect.return_value = False
            connection_cls.return_value = probe
            result = server_module.get_blender_mcp_status(ctx)

        self.assertIn('"connected": false', result)
        self.assertIn("Start Blender", result)

    def test_suggest_blender_workflow_for_product_render(self):
        ctx = MagicMock()
        with patch('blender_open_mcp.server._opencrab_status', return_value={"repo_exists": False}):
            result = server_module.suggest_blender_workflow(ctx, "product render with camera lighting and Cycles")

        self.assertIn('"create_camera"', result)
        self.assertIn('"setup_three_point_lighting"', result)
        self.assertIn('"set_render_engine"', result)

    def test_add_modifier_sends_command(self):
        ctx = MagicMock()
        mock_blender_conn = MagicMock()
        mock_blender_conn.send_command.return_value = {
            "object": "Cube",
            "modifier": {"name": "Soft Bevel", "type": "BEVEL"},
        }

        with patch('blender_open_mcp.server.get_blender_connection', return_value=mock_blender_conn):
            result = server_module.add_modifier(
                ctx,
                object_name="Cube",
                modifier_type="BEVEL",
                modifier_name="Soft Bevel",
                properties={"width": 0.05},
            )

        mock_blender_conn.send_command.assert_called_once_with(
            "add_modifier",
            {
                "object_name": "Cube",
                "modifier_type": "BEVEL",
                "modifier_name": "Soft Bevel",
                "properties": {"width": 0.05},
            }
        )
        self.assertIn('"Soft Bevel"', result)

    def test_import_model_sends_command(self):
        ctx = MagicMock()
        mock_blender_conn = MagicMock()
        mock_blender_conn.send_command.return_value = {
            "file_path": "C:/tmp/model.glb",
            "format": "glb",
            "imported_objects": ["Model"],
        }

        with patch('blender_open_mcp.server.get_blender_connection', return_value=mock_blender_conn):
            result = server_module.import_model(ctx, file_path="C:/tmp/model.glb")

        mock_blender_conn.send_command.assert_called_once_with(
            "import_model",
            {"file_path": "C:/tmp/model.glb", "file_format": None}
        )
        self.assertIn('"Model"', result)

    def test_render_image_bug(self):
        """
        Test the render_image tool to demonstrate the bug.
        This test will fail before the fix and pass after.
        """
        # Create a mock context object with an add_image method
        ctx = MagicMock()
        ctx.add_image = MagicMock()
        # also mock get_image to return the added image
        def get_image():
            if ctx.add_image.call_args:
                return ctx.add_image.call_args[0][0]
            return None
        ctx.get_image = get_image


        # 1. Create a dummy image file to represent the rendered output
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_file.write(b"fake_image_data")
            correct_image_path = tmp_file.name

        # 2. Mock the Blender connection
        mock_blender_conn = MagicMock()

        # 3. Configure the mock send_command to return the correct path
        # This simulates the behavior of the addon
        mock_blender_conn.send_command.return_value = {
            "rendered": True,
            "output_path": correct_image_path,
            "resolution": [1920, 1080]
        }

        # 4. Patch get_blender_connection to return our mock
        with patch('blender_open_mcp.server.get_blender_connection', return_value=mock_blender_conn):

            # 5. Call the render_image tool
            # The bug is that it uses "render.png" instead of `correct_image_path`
            result = asyncio.run(server_module.render_image(ctx, file_path="render.png"))

            # 6. Assertions
            self.assertEqual(result, "Image Rendered Successfully.")

            # Check if the context now has an image
            ctx.add_image.assert_called_once()
            img = ctx.add_image.call_args[0][0]
            self.assertIsInstance(img, Image)

            # Verify the image data is correct
            with open(correct_image_path, "rb") as f:
                expected_data = base64.b64encode(f.read()).decode('utf-8')
            self.assertEqual(img.data, expected_data)

        # 7. Clean up the dummy file
        os.remove(correct_image_path)

if __name__ == '__main__':
    unittest.main()
