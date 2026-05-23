#tracing.py - Tüm servislerin kullandığı ortak OpenTelemetry yapılandırması
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor


def setup_tracing(service_name: str, app=None):
    
    #OpenTelemetry + Jaeger tracing kurulumu. Her mikroservis başlarken bu fonksiyonu çağırır.
    
    jaeger_endpoint = os.getenv(
        "JAEGER_ENDPOINT",
        "http://localhost:4318/v1/traces"
    )

    #Servis kaynağı (Jaeger UI'da görünecek servis adı)
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": "demo"
    })

    #OTLP exporter → Jaeger'a trace gönderir
    otlp_exporter = OTLPSpanExporter(endpoint=jaeger_endpoint)

    #TracerProvider → tüm span'leri yöneten ana sağlayıcı
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    #Global trace provider'ı ayarla
    trace.set_tracer_provider(provider)

    #FastAPI otomatik instrumentasyon (HTTP istek/yanıtlarını otomatik izler)
    if app:
        FastAPIInstrumentor.instrument_app(app)

    #HTTPX otomatik instrumentasyon (dışarı giden HTTP çağrılarını izler)
    HTTPXClientInstrumentor().instrument()

    return trace.get_tracer(service_name)
