"""ComfyUI V3 node: an on-canvas viewer for the server console log."""

from comfy_api.latest import ComfyExtension, io


class ConsoleLogViewer(io.ComfyNode):
    """UI-only terminal mirror; the widget streams over SSE independently."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ConsoleLogViewer",
            display_name="Console Log Viewer",
            category="utils/debug",
            inputs=[],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls):
        return io.NodeOutput()


class ConsoleNodeExtension(ComfyExtension):
    async def get_node_list(self):
        return [ConsoleLogViewer]
