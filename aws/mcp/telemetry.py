"""OpenTelemetry wiring: send the MCP server's tool spans to the Foundry project's Application Insights.

Same setup as the agent (aws/agent/telemetry.py): Azure Monitor exporter straight from Lambda.
"""

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Emit the latest GenAI semantic conventions (gen_ai.operation.name=invoke_agent/chat/execute_tool, ...).
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")

AGENT_NAME = os.environ.get("SERVICE_NAME", "guachinches-mcp")

_provider = TracerProvider(
    resource=Resource.create(
        {
            "service.name": AGENT_NAME,
            "cloud.provider": "aws",
            "cloud.platform": "aws_lambda",
            "cloud.region": os.environ.get("AWS_REGION", "local"),
        }
    )
)

_connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _connection_string:
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

    _provider.add_span_processor(BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=_connection_string)))

trace.set_tracer_provider(_provider)
tracer = trace.get_tracer(AGENT_NAME)


def flush() -> None:
    """Lambda freezes the sandbox after the response, so push pending spans before returning."""
    _provider.force_flush(timeout_millis=5000)
