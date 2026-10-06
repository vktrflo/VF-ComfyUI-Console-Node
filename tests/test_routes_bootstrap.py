import importlib.util


def test_routes_import_without_comfyui():
    spec = importlib.util.find_spec("comfyui_console_node.routes")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.register_routes() is False  # no ComfyUI 'server' module here
