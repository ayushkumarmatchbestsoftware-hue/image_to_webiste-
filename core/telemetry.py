import importlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Mapping, Optional


_configured_logging = False
_configured_tracing = False
_tracing_enabled = False
_shutdown_complete = False


def _parse_resource_attributes() -> dict:
    attrs = {}
    raw = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
    for pair in raw.split(","):
        item = pair.strip()
        if not item:
            continue
        key, sep, value = item.partition("=")
        if sep and key.strip():
            attrs[key.strip()] = value.strip()
    return attrs


def _service_name() -> Optional[str]:
    return os.getenv("OTEL_SERVICE_NAME") or _parse_resource_attributes().get("service.name")


def _deployment_environment() -> Optional[str]:
    attrs = _parse_resource_attributes()
    return (
        attrs.get("deployment.environment")
        or os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT")
        or os.getenv("ENVIRONMENT")
        or os.getenv("NODE_ENV")
    )


def _otel_log_level() -> int:
    levels = {
        "error": logging.ERROR,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
        "verbose": logging.DEBUG,
        "all": logging.NOTSET,
    }
    return levels.get(os.getenv("OTEL_LOG_LEVEL", "info").lower(), logging.INFO)


def _log_level() -> int:
    return getattr(logging, os.getenv("LOG_LEVEL", "info").upper(), logging.INFO)


def _json_logs_enabled() -> bool:
    return (
        os.getenv("LOG_FORMAT", "").lower() == "json"
        or os.getenv("ENVIRONMENT", "").lower() == "production"
        or os.getenv("NODE_ENV", "").lower() == "production"
    )


def json_logs_enabled() -> bool:
    return _json_logs_enabled()


def _span_context():
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if not span:
            return None
        span_context = span.get_span_context()
        if not span_context or not span_context.is_valid:
            return None
        return span_context
    except Exception:
        return None


def get_log_correlation_fields() -> dict:
    fields = {}
    span_context = _span_context()
    if span_context:
        fields["trace_id"] = f"{span_context.trace_id:032x}"
        fields["span_id"] = f"{span_context.span_id:016x}"

    service_name = _service_name()
    if service_name:
        fields["service_name"] = service_name

    environment = _deployment_environment()
    if environment:
        fields["environment"] = environment

    return fields


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in ("trace_id", "span_id", "service_name", "environment"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


class _PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = {
            key: getattr(record, key, None)
            for key in ("trace_id", "span_id", "service_name", "environment")
            if getattr(record, key, None)
        }
        if fields:
            return f"{line} {json.dumps(fields, separators=(',', ':'))}"
        return line


def configure_logging() -> None:
    global _configured_logging
    if _configured_logging:
        return

    previous_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        for key, value in get_log_correlation_fields().items():
            setattr(record, key, value)
        return record

    logging.setLogRecordFactory(record_factory)

    if _json_logs_enabled():
        formatter = _JsonFormatter()
    else:
        formatter = _PrettyFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    logging.basicConfig(level=_log_level(), handlers=[handler], force=True)
    logging.getLogger("opentelemetry").setLevel(_otel_log_level())
    _configured_logging = True


def _trace_endpoint() -> Optional[str]:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return None
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/traces"):
        return endpoint
    return f"{endpoint}/v1/traces"


def _resource():
    from opentelemetry.sdk.resources import Resource

    attrs = {}
    parsed = _parse_resource_attributes()
    deployment_environment = _deployment_environment()
    if deployment_environment and "deployment.environment" not in parsed:
        attrs["deployment.environment"] = deployment_environment

    return Resource.create(attrs)


def _set_propagators() -> None:
    try:
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        set_global_textmap(
            CompositePropagator(
                [
                    TraceContextTextMapPropagator(),
                    W3CBaggagePropagator(),
                ]
            )
        )
    except Exception:
        logging.getLogger(__name__).exception("Failed to configure OpenTelemetry propagators")


def _instrument(module_name: str, class_name: str) -> None:
    try:
        module = importlib.import_module(module_name)
        instrumentor = getattr(module, class_name)()
        instrumentor.instrument()
    except ModuleNotFoundError:
        logging.getLogger(__name__).debug("OpenTelemetry instrumentor not installed: %s", module_name)
    except Exception:
        logging.getLogger(__name__).exception("Failed to instrument %s", module_name)


def _instrument_libraries() -> None:
    for module_name, class_name in (
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.pymongo", "PymongoInstrumentor"),
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.botocore", "BotocoreInstrumentor"),
    ):
        _instrument(module_name, class_name)


def setup_telemetry() -> bool:
    global _configured_tracing, _tracing_enabled
    if _configured_tracing:
        return _tracing_enabled

    # Check whether tracing will actually be used BEFORE importing anything
    # OpenTelemetry-related — every code path that would use propagators
    # (get_tracer/inject_trace_context/extract_trace_context) already checks
    # _tracing_enabled and no-ops when it's False, so there is nothing for
    # propagators to do when no endpoint is configured. Skipping the import
    # entirely in that case avoids pulling in opentelemetry.propagate/baggage
    # for a deployment that will never export a trace.
    endpoint = _trace_endpoint()
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        logging.getLogger(__name__).warning("[otel] OTEL_SDK_DISABLED=true; tracing export is disabled")
        _configured_tracing = True
        return False

    if not endpoint:
        logging.getLogger(__name__).warning(
            "[otel] OTEL_EXPORTER_OTLP_ENDPOINT is not set; tracing export is disabled"
        )
        _configured_tracing = True
        return False

    _set_propagators()
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=_resource())
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _instrument_libraries()
        _tracing_enabled = True
        logging.getLogger(__name__).info("[otel] tracing export enabled", extra={"otel_endpoint": endpoint})
    except Exception:
        logging.getLogger(__name__).exception("[otel] failed to initialize tracing; continuing without export")
        _tracing_enabled = False

    _configured_tracing = True
    return _tracing_enabled


def instrument_fastapi_app(app) -> None:
    if not _tracing_enabled:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logging.getLogger(__name__).exception("Failed to instrument FastAPI app")


def shutdown_telemetry() -> None:
    global _shutdown_complete
    if _shutdown_complete or not _tracing_enabled:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        logging.getLogger(__name__).exception("Failed to shut down OpenTelemetry")
    finally:
        _shutdown_complete = True


def get_tracer(name: str):
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return None


def inject_trace_context() -> dict:
    if not _tracing_enabled:
        return {}
    carrier = {}
    try:
        from opentelemetry.propagate import inject

        inject(carrier)
    except Exception:
        logging.getLogger(__name__).debug("Failed to inject OpenTelemetry trace context", exc_info=True)
    return carrier


def extract_trace_context(carrier: Optional[Mapping[str, str]]):
    if not _tracing_enabled or not carrier:
        return None
    try:
        from opentelemetry.propagate import extract

        return extract(carrier)
    except Exception:
        logging.getLogger(__name__).debug("Failed to extract OpenTelemetry trace context", exc_info=True)
        return None
