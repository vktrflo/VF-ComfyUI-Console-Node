import asyncio
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def make_fake_comfy_api():
    class ComfyNode:
        pass

    class ComfyExtension:
        async def get_node_list(self):
            return []

    class NodeOutput:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    def Schema(**fields):
        return types.SimpleNamespace(**fields)

    io = types.SimpleNamespace(ComfyNode=ComfyNode, Schema=Schema, NodeOutput=NodeOutput)
    latest = types.ModuleType("comfy_api.latest")
    latest.ComfyExtension = ComfyExtension
    latest.io = io
    package = types.ModuleType("comfy_api")
    package.latest = latest
    return package, latest


@pytest.fixture
def nodes_module(monkeypatch):
    package, latest = make_fake_comfy_api()
    monkeypatch.setitem(sys.modules, "comfy_api", package)
    monkeypatch.setitem(sys.modules, "comfy_api.latest", latest)
    sys.modules.pop("comfyui_console_node.nodes", None)
    module = importlib.import_module("comfyui_console_node.nodes")
    yield module, latest
    sys.modules.pop("comfyui_console_node.nodes", None)


def test_schema_shape(nodes_module):
    module, _ = nodes_module
    schema = module.ConsoleLogViewer.define_schema()
    assert schema.node_id == "ConsoleLogViewer"
    assert schema.display_name == "🌀 VF Console Log Viewer"
    assert schema.category == "utils/debug"
    assert schema.is_output_node is True
    assert schema.inputs == []
    assert schema.outputs == []


def test_execute_returns_empty_output(nodes_module):
    module, _ = nodes_module
    result = module.ConsoleLogViewer.execute()
    assert result.args == ()
    assert result.kwargs == {}


def test_extension_lists_node(nodes_module):
    module, latest = nodes_module
    extension = module.ConsoleNodeExtension()
    assert isinstance(extension, latest.ComfyExtension)
    node_list = asyncio.run(extension.get_node_list())
    assert node_list == [module.ConsoleLogViewer]


def test_entrypoint_shape(monkeypatch):
    package, latest = make_fake_comfy_api()
    monkeypatch.setitem(sys.modules, "comfy_api", package)
    monkeypatch.setitem(sys.modules, "comfy_api.latest", latest)
    spec = importlib.util.spec_from_file_location(
        "console_node_custom_node", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert callable(module.comfy_entrypoint)
    assert module.WEB_DIRECTORY == "./web"
