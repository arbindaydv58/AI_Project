"""Built-in tools. Importing this package registers all of them in jarvis.registry.TOOLS.

The import order below is the order in which the tools are offered to the model.
"""

from . import (  # noqa: F401
    basic,
    files,
    shell,
    web_tools,
    memory_tools,
    assistant_tools,
    protocol_tools,
    watcher_tools,
)
