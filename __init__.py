"""ComfyUI entrypoint for the Console Node custom node pack."""

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from .comfyui_console_node.bootstrap import setup
    from .comfyui_console_node.nodes import ConsoleNodeExtension

    setup()
    return ConsoleNodeExtension()
