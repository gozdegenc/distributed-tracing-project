import os
import asyncio
import time
import json
import pathlib
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.propagate import inject
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator

JAEGER_ENDPOINT = os.getenv("JAEGER_ENDPOINT",         "http://jaeger:4318/v1/traces")
ORDER_SERVICE   = os.getenv("ORDER_SERVICE_URL",        "http://order-service:8001")
INVENTORY_URL   = os.getenv("INVENTORY_SERVICE_URL",    "http://inventory-service:8002")
NOTIF_URL       = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8004")

app = FastAPI(title="API Gateway", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
Instrumentator().instrument(app).expose(app)

resource = Resource.create({"service.name": "api-gateway", "service.version": "1.0.0"})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=JAEGER_ENDPOINT)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("api-gateway")
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

request_counts: dict = {}
RATE_LIMIT = 100

PRODUCT_CATALOG = {
    "PROD-001": {"name": "Laptop",               "price": 14999.00, "category": "Bilgisayar", "icon": "💻"},
    "PROD-002": {"name": "Kablosuz Mouse",        "price":   299.00, "category": "Aksesuar",   "icon": "🖱️"},
    "PROD-003": {"name": "Mekanik Klavye",        "price":   799.00, "category": "Aksesuar",   "icon": "⌨️"},
    "PROD-004": {"name": "4K Monitör",            "price":  4999.00, "category": "Ekran",      "icon": "🖥️"},
    "PROD-005": {"name": "Bluetooth Kulaklık",    "price":   599.00, "category": "Ses",        "icon": "🎧"},
}


def check_rate_limit(client_ip: str) -> bool:
    now = int(time.time() / 60)
    key = f"{client_ip}:{now}"
    request_counts[key] = request_counts.get(key, 0) + 1
    return request_counts[key] <= RATE_LIMIT


class OrderRequest(BaseModel):
    customer_id: str
    product_id:  str
    quantity:    int
    total_price: float


#Sağlık & Katalog 
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "api-gateway"}


@app.get("/api/v1/products")
async def get_products():
    return {"products": PRODUCT_CATALOG}


@app.get("/api/v1/services/health")
async def all_services_health():
    services = {
        "order-service":        f"{ORDER_SERVICE}/health",
        "inventory-service":    f"{INVENTORY_URL}/health",
        "notification-service": f"{NOTIF_URL}/health",
    }
    results = {}
    async with httpx.AsyncClient() as client:
        for name, url in services.items():
            try:
                r = await client.get(url, timeout=3.0)
                results[name] = {"status": "healthy" if r.status_code == 200 else "unhealthy"}
            except Exception as e:
                results[name] = {"status": "unreachable", "error": str(e)}
    return results


#Sipariş API'leri
@app.post("/api/v1/orders/initiate")
async def initiate_order(order: OrderRequest, request: Request):
    """Adım 1: Siparişi başlat — stok kontrolü, pending_payment kaydı."""
    client_ip = request.client.host if request.client else "unknown"

    if order.product_id not in PRODUCT_CATALOG:
        raise HTTPException(status_code=400, detail=f"Geçersiz ürün: {order.product_id}")

    expected = round(PRODUCT_CATALOG[order.product_id]["price"] * order.quantity, 2)
    if round(order.total_price, 2) != expected:
        raise HTTPException(status_code=400, detail=f"Fiyat geçersiz. Beklenen: ₺{expected}")

    with tracer.start_as_current_span("gateway-initiate-order") as span:
        span.set_attribute("gateway.client_ip", client_ip)

        with tracer.start_as_current_span("rate-limit-check") as rl:
            await asyncio.sleep(0.002)
            if not check_rate_limit(client_ip):
                rl.set_status(Status(StatusCode.ERROR))
                raise HTTPException(status_code=429, detail="Rate limit aşıldı")

        with tracer.start_as_current_span("auth-validation") as auth:
            await asyncio.sleep(0.005)
            auth.set_attribute("auth.valid", True)

        headers = {"Content-Type": "application/json"}
        inject(headers)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ORDER_SERVICE}/orders/initiate",
                json=order.model_dump(),
                headers=headers,
                timeout=15.0,
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Hata"))

        span.set_status(Status(StatusCode.OK))
        return resp.json()


@app.post("/api/v1/orders/{order_id}/pay")
async def pay_order(order_id: str):
    """Adım 2: Ödemeyi onayla — payment + notification + confirm."""
    with tracer.start_as_current_span("gateway-pay-order") as span:
        span.set_attribute("order.id", order_id)
        headers = {}
        inject(headers)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ORDER_SERVICE}/orders/{order_id}/pay",
                headers=headers,
                timeout=30.0,
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Ödeme hatası"))
        span.set_status(Status(StatusCode.OK))
        return resp.json()


@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: str):
    async with httpx.AsyncClient() as client:
        headers = {}
        inject(headers)
        resp = await client.get(f"{ORDER_SERVICE}/orders/{order_id}", headers=headers, timeout=10.0)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return resp.json()


@app.get("/api/v1/orders")
async def list_orders():
    async with httpx.AsyncClient() as client:
        headers = {}
        inject(headers)
        resp = await client.get(f"{ORDER_SERVICE}/orders", headers=headers, timeout=10.0)
    data = resp.json()
    for order in data.get("orders", []):
        pid = order.get("product_id", "")
        p = PRODUCT_CATALOG.get(pid, {})
        order["product_name"] = p.get("name", pid)
        order["product_icon"] = p.get("icon", "📦")
    return data


#Trace Overhead Ölçümü
@app.get("/api/v1/trace-overhead")
async def trace_overhead_benchmark():
    """
    Tracing'in sisteme getirdiği ek maliyeti (overhead) ölçer.
    Aynı iş yükünü tracing açık ve kapalı olarak çalıştırır, farkı hesaplar.
    Bu endpoint Distributed Tracing'e özgü performans metriğidir.
    """
    import time

    iterations = 100

    #Tracing AÇIK — her iterasyonda bir span oluşturuluyor
    start_with = time.perf_counter()
    for i in range(iterations):
        with tracer.start_as_current_span(f"overhead-benchmark-{i}") as span:
            span.set_attribute("iteration", i)
            span.set_attribute("benchmark.type", "overhead-measurement")
            await asyncio.sleep(0)  # I/O switch — gerçekçi async simülasyonu
    elapsed_with = (time.perf_counter() - start_with) * 1000  # ms

    #Tracing KAPALI — span oluşturmadan aynı iş yükü 
    start_without = time.perf_counter()
    for i in range(iterations):
        await asyncio.sleep(0)
    elapsed_without = (time.perf_counter() - start_without) * 1000  # ms

    overhead_ms       = elapsed_with - elapsed_without
    overhead_per_span = overhead_ms / iterations
    overhead_pct      = (overhead_ms / elapsed_without * 100) if elapsed_without > 0 else 0

    return {
        "benchmark": {
            "iterations":          iterations,
            "with_tracing_ms":     round(elapsed_with, 3),
            "without_tracing_ms":  round(elapsed_without, 3),
            "overhead_total_ms":   round(overhead_ms, 3),
            "overhead_per_span_us": round(overhead_per_span * 1000, 2),  # microseconds
            "overhead_percent":    round(overhead_pct, 2),
        },
        "verdict": (
            "✅ Tracing overhead kabul edilebilir düzeyde (<5%)"
            if overhead_pct < 5 else
            "⚠️ Tracing overhead yüksek (>5%) — sampling oranı düşürülmeli"
        ),
        "recommendation": (
            "100% sampling production'da önerilmez. "
            "Jaeger'ın adaptive sampling özelliği ile %1-10 arası önerilir."
        ),
    }


# HTML SAYFALAR

_BASE_STYLE = """
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b1120;--surface:#131f35;--surface2:#1a2942;--border:#1e3a5f;
  --text:#e2e8f0;--muted:#64748b;--accent:#3b82f6;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:linear-gradient(90deg,#0f2460,#1a3a7a);padding:18px 32px;display:flex;
  align-items:center;gap:14px;border-bottom:1px solid var(--border);box-shadow:0 2px 20px rgba(0,0,0,.5)}
.logo{width:36px;height:36px;background:var(--accent);border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
header h1{font-size:19px;font-weight:700}
header p{font-size:12px;color:#93c5fd;margin-top:2px}
.badge{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;
  letter-spacing:.4px;text-transform:uppercase}
.badge-blue{background:#1e3a8a44;color:#93c5fd;border:1px solid #1d4ed8}
.badge-green{background:#052e1644;color:#86efac;border:1px solid #166534}
.badge-yellow{background:#42200644;color:#fcd34d;border:1px solid #92400e}
.main{max-width:1200px;margin:0 auto;padding:28px 24px;display:grid;gap:24px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:22px}
@media(max-width:900px){.row2,.row3{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px}
.card-title{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;
  letter-spacing:.9px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px}
.stat-label{font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.6px}
.stat-value{font-size:28px;font-weight:800;margin-top:6px}
.stat-sub{font-size:12px;color:var(--muted);margin-top:5px}
label{font-size:12px;color:#94a3b8;font-weight:600;display:block;margin-bottom:5px;letter-spacing:.3px}
input,select{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px;
  padding:9px 12px;color:var(--text);font-size:14px;outline:none;transition:border-color .2s;appearance:none}
input:focus,select:focus{border-color:var(--accent)}
input[readonly]{color:var(--muted);cursor:default}
input[readonly]:focus{border-color:var(--border)}
select option{background:var(--surface)}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:500px){.form-grid{grid-template-columns:1fr}}
.qty-row{display:flex;align-items:center;gap:8px}
.qty-btn{width:32px;height:32px;background:var(--surface2);border:1px solid var(--border);
  border-radius:6px;color:var(--text);font-size:16px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;user-select:none}
.qty-btn:hover{background:#2a3f5f}
.qty-input{text-align:center;width:60px!important;flex-shrink:0}
.price-display{background:var(--bg);border:1px solid var(--border);border-radius:8px;
  padding:9px 12px;font-size:15px;color:#4ade80;font-weight:700}
.stock-badge{display:inline-flex;align-items:center;gap:5px;
  padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;margin-top:6px}
.stock-ok{background:#052e1644;color:#86efac;border:1px solid #166534}
.stock-warn{background:#42200644;color:#fcd34d;border:1px solid #92400e}
.stock-zero{background:#1c0a0044;color:#fca5a5;border:1px solid #7f1d1d}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:10px 22px;border-radius:8px;font-size:14px;font-weight:600;
  cursor:pointer;border:none;transition:opacity .15s,transform .1s}
.btn:active{transform:scale(.97)}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover:not(:disabled){background:#1d4ed8}
.btn-primary:disabled{opacity:.45;cursor:not-allowed;transform:none}
.btn-ghost{background:var(--surface2);color:#cbd5e1;border:1px solid var(--border)}
.btn-ghost:hover{background:#2a3f5f}
.btn-sm{padding:6px 14px;font-size:12px}
.btn-green{background:#15803d;color:#fff}
.btn-green:hover:not(:disabled){background:#166534}
.btn-green:disabled{opacity:.45;cursor:not-allowed}
.form-actions{display:flex;gap:10px;margin-top:14px}
.alert{border-radius:8px;padding:12px 16px;margin-top:14px;font-size:13px;display:none;line-height:1.6}
.alert-success{background:#052e1655;border:1px solid #166534;color:#86efac}
.alert-error  {background:#1c0a0055;border:1px solid #7f1d1d;color:#fca5a5}
.alert a{color:#60a5fa;text-decoration:none;font-weight:600}
.alert a:hover{text-decoration:underline}
.alert code{background:#ffffff12;padding:1px 6px;border-radius:4px;font-family:monospace}
.stock-list{display:grid;gap:8px}
.stock-item{background:var(--bg);border:1px solid var(--border);border-radius:9px;
  padding:11px 14px;display:flex;align-items:center;gap:12px}
.stock-item.out-of-stock{border-color:#7f1d1d44;opacity:.6}
.si-icon{width:36px;height:36px;background:#1a2942;border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.si-info{flex:1}
.si-name{font-size:13px;font-weight:600}
.si-id{font-size:11px;color:var(--muted)}
.si-bar{height:4px;background:var(--border);border-radius:2px;margin-top:6px}
.si-fill{height:100%;border-radius:2px;transition:width .4s}
.si-qty{font-size:15px;font-weight:700;min-width:70px;text-align:right}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:9px 12px;color:var(--muted);font-size:11px;font-weight:600;
  text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:11px 12px;border-bottom:1px solid #ffffff08;vertical-align:middle}
tr:last-child td{border-bottom:none}tr:hover td{background:#ffffff04}
.tag{display:inline-block;padding:2px 9px;border-radius:5px;font-size:11px;font-weight:700;
  background:#1e3a8a44;color:#93c5fd;border:1px solid #1d4ed844;font-family:monospace}
.chip{display:inline-block;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600}
.chip-green{background:#052e1655;color:#4ade80;border:1px solid #166534}
.chip-yellow{background:#42200644;color:#fcd34d;border:1px solid #92400e}
.chip-red  {background:#1c0a0055;color:#f87171;border:1px solid #7f1d1d}
.trace-link{color:var(--accent);text-decoration:none;font-size:12px;font-weight:600}
.trace-link:hover{text-decoration:underline}
.empty-state{text-align:center;color:var(--muted);padding:36px 0;font-size:14px}
.svc-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.svc{background:var(--surface2);border:1px solid var(--border);border-radius:9px;
  padding:10px 12px;display:flex;align-items:center;gap:10px}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;transition:background .3s}
.dot-ok{background:var(--green);box-shadow:0 0 8px #22c55e66}
.dot-err{background:var(--red);box-shadow:0 0 8px #ef444466}
.dot-loading{background:var(--muted)}
.svc-name{font-size:13px;font-weight:500}
.svc-port{font-size:11px;color:var(--muted)}
.ext-links{display:flex;gap:10px;flex-wrap:wrap}
.ext-link{flex:1;min-width:130px;background:var(--surface2);border:1px solid var(--border);
  border-radius:10px;padding:12px 14px;color:var(--text);text-decoration:none;
  display:flex;align-items:center;gap:10px;transition:border-color .2s}
.ext-link:hover{border-color:var(--accent);background:#1e3a5f55}
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{width:14px;height:14px;border:2px solid #ffffff33;border-top-color:#fff;
  border-radius:50%;animation:spin .6s linear infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.pulse{animation:pulse 1.5s ease-in-out infinite}
</style>
"""


#Ana Dashboard
@app.get("/", response_class=HTMLResponse)
async def root():
    prods_json = json.dumps({
        k: {"name": v["name"], "price": v["price"], "icon": v["icon"]}
        for k, v in PRODUCT_CATALOG.items()
    })
    return f"""<!DOCTYPE html>
<html lang="tr"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>E-Ticaret Dashboard</title>{_BASE_STYLE}
</head><body>
<header>
  <div class="logo">🛒</div>
  <div><h1>E-Ticaret Sipariş Sistemi</h1>
       <p>Distributed Tracing Demo</p></div>
  <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
    <span class="badge badge-blue">OpenTelemetry</span>
    <span class="badge badge-blue">Jaeger</span>
    <span class="badge badge-green" id="sys-badge">&#9679; Kontrol ediliyor</span>
  </div>
</header>
<div class="main">

  <!-- İstatistikler -->
  <div class="row3">
    <div class="stat"><div class="stat-label">Toplam Sipariş</div>
      <div class="stat-value" id="st-total">—</div><div class="stat-sub">Tüm zamanlar</div></div>
    <div class="stat"><div class="stat-label">Onaylanan</div>
      <div class="stat-value" style="color:#4ade80" id="st-ok">—</div>
      <div class="stat-sub" id="st-rate">Başarı oranı</div></div>
    <div class="stat"><div class="stat-label">Aktif Servis</div>
      <div class="stat-value" id="st-svc">—</div><div class="stat-sub">4 servis izleniyor</div></div>
  </div>

  <!-- Servisler + Araçlar -->
  <div class="row2">
    <div class="card"><div class="card-title">⚡ Servis Durumları</div>
      <div class="svc-grid">
        <div class="svc"><div class="dot dot-loading" id="d-gw"></div>
          <div><div class="svc-name">API Gateway</div><div class="svc-port">:8000</div></div></div>
        <div class="svc"><div class="dot dot-loading" id="d-ord"></div>
          <div><a href="http://localhost:8001" style="color:inherit;text-decoration:none"><div class="svc-name">Order Service</div><div class="svc-port">:8001</div></a></div></div>
        <div class="svc"><div class="dot dot-loading" id="d-inv"></div>
          <div><a href="http://localhost:8002" style="color:inherit;text-decoration:none"><div class="svc-name">Inventory Service</div><div class="svc-port">:8002</div></a></div></div>
        <div class="svc"><div class="dot dot-loading" id="d-not"></div>
          <div><a href="http://localhost:8004" style="color:inherit;text-decoration:none"><div class="svc-name">Notification Service</div><div class="svc-port">:8004</div></a></div></div>
        <div class="svc"><div class="dot dot-loading" id="d-jgr"></div>
          <div><div class="svc-name">Jaeger UI</div><div class="svc-port">:16686</div></div></div>
      </div>
    </div>
    <div class="card"><div class="card-title">🔗 İzleme Araçları</div>
      <div class="ext-links">
        <a class="ext-link" href="http://localhost:16686" target="_blank">
          <span style="font-size:20px">🔍</span>
          <div><div style="font-size:13px;font-weight:600">Jaeger UI</div>
               <div style="font-size:11px;color:var(--muted)">Trace görselleştirme</div></div>
        </a>
        <a class="ext-link" href="http://localhost:9090" target="_blank">
          <span style="font-size:20px">📊</span>
          <div><div style="font-size:13px;font-weight:600">Prometheus</div>
               <div style="font-size:11px;color:var(--muted)">Metrik izleme</div></div>
        </a>
        <a class="ext-link" href="/docs" target="_blank">
          <span style="font-size:20px">📄</span>
          <div><div style="font-size:13px;font-weight:600">Swagger API</div>
               <div style="font-size:11px;color:var(--muted)">API dokümantasyon</div></div>
        </a>
        <a class="ext-link" href="/tests">
          <span style="font-size:20px">🧪</span>
          <div><div style="font-size:13px;font-weight:600">JMeter Test Panel</div>
               <div style="font-size:11px;color:var(--muted)">Performans test arayüzü</div></div>
        </a>
      </div>
    </div>
  </div>

  <!-- Sipariş Formu + Stok -->
  <div class="row2">
    <div class="card"><div class="card-title">🛍️ Yeni Sipariş</div>
      <div class="form-grid" style="margin-bottom:14px">
        <div>
          <label>MÜŞTERİ ID</label>
          <input id="f-cust" type="text" value="CUST-001" placeholder="CUST-001"/>
        </div>
        <div>
          <label>ÜRÜN</label>
          <select id="f-prod" onchange="onProd()"></select>
          <div id="f-stock-badge"></div>
        </div>
        <div>
          <label>ADET</label>
          <div class="qty-row">
            <div class="qty-btn" onclick="chgQty(-1)">−</div>
            <input class="qty-input" id="f-qty" type="number" value="1" min="1" oninput="updPrice()"/>
            <div class="qty-btn" onclick="chgQty(1)">+</div>
          </div>
        </div>
        <div>
          <label>TOPLAM TUTAR</label>
          <div class="price-display" id="f-price-disp">₺0,00</div>
          <input type="hidden" id="f-price"/>
        </div>
      </div>
      <div class="form-actions">
        <button class="btn btn-primary" id="ord-btn" onclick="startOrder()">
          <span id="ord-txt">Sipariş Ver →</span>
        </button>
        <button class="btn btn-ghost" onclick="resetForm()">Temizle</button>
      </div>
      <div class="alert alert-error" id="f-err"></div>
    </div>

    <div class="card"><div class="card-title">📦 Stok Durumu
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="loadStock()">↻</button>
    </div>
      <div class="stock-list" id="stock-list"><div class="empty-state pulse">Yükleniyor…</div></div>
    </div>
  </div>

  <!-- Sipariş Listesi -->
  <div class="card"><div class="card-title">📋 Sipariş Listesi
    <span style="margin-left:4px;font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0" id="ord-count"></span>
    <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="loadOrders()">↻ Yenile</button>
  </div>
    <div class="tbl-wrap"><table>
      <thead><tr>
        <th>Sipariş No</th><th>Müşteri</th><th>Ürün</th><th>Adet</th>
        <th>Tutar</th><th>Durum</th><th>Trace</th>
      </tr></thead>
      <tbody id="ord-body"><tr><td colspan="7" class="empty-state">Yükleniyor…</td></tr></tbody>
    </table></div>
  </div>
</div>

<script>
const PRODS={prods_json};
let stock={{}};

function buildProdSelect(){{
  const s=document.getElementById('f-prod');
  s.innerHTML='';
  for(const[id,p] of Object.entries(PRODS)){{
    const q=stock[id]??'?';const out=q===0;
    const o=document.createElement('option');
    o.value=id;
    o.textContent=`${{p.icon}} ${{p.name}} — ₺${{p.price.toLocaleString('tr-TR')}}  (stok: ${{q}})`;
    o.disabled=out;
    s.appendChild(o);
  }}
  const first=s.querySelector('option:not([disabled])');
  if(first){{s.value=first.value;}}
  onProd();
}}

function onProd(){{updPrice();updStockBadge();}}

function updPrice(){{
  const pid=document.getElementById('f-prod').value;
  const qty=parseInt(document.getElementById('f-qty').value)||1;
  const p=PRODS[pid];if(!p)return;
  const tot=p.price*qty;
  document.getElementById('f-price-disp').textContent='₺'+tot.toLocaleString('tr-TR',{{minimumFractionDigits:2}});
  document.getElementById('f-price').value=tot.toFixed(2);
}}

function updStockBadge(){{
  const pid=document.getElementById('f-prod').value;
  const q=stock[pid];
  const el=document.getElementById('f-stock-badge');
  if(q===undefined){{el.innerHTML='';return;}}
  if(q===0) el.innerHTML='<div class="stock-badge stock-zero">✗ Stokta yok</div>';
  else if(q<=5) el.innerHTML=`<div class="stock-badge stock-warn">⚠ Son ${{q}} adet!</div>`;
  else el.innerHTML=`<div class="stock-badge stock-ok">✓ ${{q}} adet mevcut</div>`;
}}

function chgQty(d){{
  const inp=document.getElementById('f-qty');
  const pid=document.getElementById('f-prod').value;
  const mx=stock[pid]||99;
  inp.value=Math.max(1,Math.min(mx,parseInt(inp.value||1)+d));
  updPrice();
}}

async function loadStock(){{
  try{{
    const r=await fetch('http://localhost:8002/stock',{{signal:AbortSignal.timeout(4000)}});
    const d=await r.json();stock=d.stock||{{}};
  }}catch{{stock={{}};}}
  buildProdSelect();
  const MAX={{'PROD-001':100,'PROD-002':50,'PROD-003':200,'PROD-004':5,'PROD-005':75}};
  const list=document.getElementById('stock-list');
  if(!Object.keys(stock).length){{list.innerHTML='<div class="empty-state">Stok servisi yanıt vermiyor</div>';return;}}
  list.innerHTML=Object.entries(stock).map(([id,q])=>{{
    const p=PRODS[id]||{{name:id,icon:'📦'}};
    const mx=MAX[id]||100;const pct=Math.round(q/mx*100);
    const col=q===0?'#ef4444':q<=5?'#f59e0b':'#22c55e';
    return `<div class="stock-item ${{q===0?'out-of-stock':''}}">
      <div class="si-icon">${{p.icon}}</div>
      <div class="si-info">
        <div class="si-name">${{p.name}}</div><div class="si-id">${{id}}</div>
        <div class="si-bar"><div class="si-fill" style="width:${{pct}}%;background:${{col}}"></div></div>
      </div>
      <div class="si-qty" style="color:${{col}}">${{q===0?'TÜKENDİ':q+' adet'}}</div>
    </div>`;
  }}).join('');
}}

async function loadOrders(){{
  try{{
    const r=await fetch('/api/v1/orders');const d=await r.json();
    const orders=(d.orders||[]).slice().reverse();
    document.getElementById('ord-count').textContent=`(${{orders.length}} sipariş)`;
    const ok=orders.filter(o=>o.status==='confirmed').length;
    document.getElementById('st-total').textContent=orders.length;
    document.getElementById('st-ok').textContent=ok;
    document.getElementById('st-rate').textContent=orders.length?`%${{Math.round(ok/orders.length*100)}} başarı`:'Henüz sipariş yok';
    if(!orders.length){{document.getElementById('ord-body').innerHTML='<tr><td colspan="7" class="empty-state">Henüz sipariş yok</td></tr>';return;}}
    document.getElementById('ord-body').innerHTML=orders.map(o=>{{
      const chip=o.status==='confirmed'?'<span class="chip chip-green">✓ Onaylandı</span>':
        o.status==='pending_payment'?'<span class="chip chip-yellow">⏳ Ödeme Bekliyor</span>':
        `<span class="chip chip-red">${{o.status}}</span>`;
      const payLink=o.status==='pending_payment'?`<a href="/checkout/${{o.order_id}}" style="color:#fbbf24;text-decoration:none;font-size:12px;font-weight:600">💳 Ödeme Yap</a>`:'';
      return `<tr>
        <td><span class="tag">${{o.order_id}}</span></td>
        <td>${{o.customer_id}}</td>
        <td>${{o.product_icon||'📦'}} ${{o.product_name||o.product_id}}</td>
        <td style="text-align:center">${{o.quantity}}</td>
        <td style="font-weight:600;color:#4ade80">₺${{Number(o.total_price).toLocaleString('tr-TR',{{minimumFractionDigits:2}})}}</td>
        <td>${{chip}} ${{payLink}}</td>
        <td>${{o.trace_id?`<a class="trace-link" href="http://localhost:16686/trace/${{o.trace_id}}" target="_blank">🔍 Trace</a>`:'—'}}</td>
      </tr>`;
    }}).join('');
  }}catch(e){{document.getElementById('ord-body').innerHTML=`<tr><td colspan="7" class="empty-state">Hata: ${{e.message}}</td></tr>`;}}
}}

async function startOrder(){{
  const btn=document.getElementById('ord-btn');
  const txt=document.getElementById('ord-txt');
  const errEl=document.getElementById('f-err');
  errEl.style.display='none';
  const cust=document.getElementById('f-cust').value.trim();
  const pid=document.getElementById('f-prod').value;
  const qty=parseInt(document.getElementById('f-qty').value);
  const total=parseFloat(document.getElementById('f-price').value);
  if(!cust){{errEl.innerHTML='⚠ Müşteri ID boş olamaz.';errEl.style.display='block';return;}}
  if(stock[pid]===0){{errEl.innerHTML='⚠ Bu ürün stokta yok.';errEl.style.display='block';return;}}
  if(stock[pid]!==undefined&&qty>stock[pid]){{errEl.innerHTML=`⚠ Yetersiz stok. Mevcut: ${{stock[pid]}} adet.`;errEl.style.display='block';return;}}
  btn.disabled=true;txt.innerHTML='<div class="spinner"></div> Kontrol ediliyor…';
  try{{
    const r=await fetch('/api/v1/orders/initiate',{{
      method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{customer_id:cust,product_id:pid,quantity:qty,total_price:total}}),
    }});
    const data=await r.json();
    if(r.ok){{
      // Ödeme sayfasına yönlendir
      window.location.href=`/checkout/${{data.order_id}}`;
    }}else{{
      errEl.innerHTML=`✗ ${{data.detail||JSON.stringify(data)}}`;
      errEl.style.display='block';
    }}
  }}catch(e){{errEl.innerHTML=`✗ Bağlantı hatası: ${{e.message}}`;errEl.style.display='block';}}
  finally{{btn.disabled=false;txt.textContent='Sipariş Ver →';}}
}}

function resetForm(){{
  document.getElementById('f-cust').value='CUST-001';
  document.getElementById('f-qty').value='1';
  document.getElementById('f-err').style.display='none';
  onProd();
}}

async function checkHealth(){{
  const checks=[
    {{id:'d-gw', url:'/health'}},
    {{id:'d-ord',url:'http://localhost:8001/health'}},
    {{id:'d-inv',url:'http://localhost:8002/health'}},
    {{id:'d-not',url:'http://localhost:8004/health'}},
    {{id:'d-jgr',url:'http://localhost:16686/'}},
  ];
  let up=0;
  await Promise.all(checks.map(async({{id,url}})=>{{
    const dot=document.getElementById(id);
    try{{const r=await fetch(url,{{signal:AbortSignal.timeout(3000)}});
      if(r.ok){{dot.className='dot dot-ok';up++;}}else dot.className='dot dot-err';
    }}catch{{dot.className='dot dot-err';}}
  }}));
  document.getElementById('st-svc').textContent=up;
  const b=document.getElementById('sys-badge');
  b.textContent=up>=4?'● Tüm sistemler çalışıyor':`● ${{up}}/5 aktif`;
  b.className=up>=4?'badge badge-green':'badge badge-yellow';
}}

(async()=>{{await loadStock();await loadOrders();checkHealth();
  setInterval(checkHealth,15000);setInterval(loadOrders,10000);}})();
</script>
</body></html>"""


#Checkout Sayfası
@app.get("/checkout/{order_id}", response_class=HTMLResponse)
async def checkout_page(order_id: str):  # noqa: F811
    return f"""<!DOCTYPE html>
<html lang="tr"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Ödeme — {order_id}</title>{_BASE_STYLE}
<style>
.checkout-wrap{{max-width:860px;margin:0 auto;padding:32px 24px;display:grid;grid-template-columns:1fr 1fr;gap:28px}}
@media(max-width:640px){{.checkout-wrap{{grid-template-columns:1fr}}}}
.order-summary{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:24px;height:fit-content}}
.os-title{{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid var(--border)}}
.os-product{{display:flex;align-items:center;gap:14px;margin-bottom:20px}}
.os-icon{{width:52px;height:52px;background:#1a2942;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:26px;flex-shrink:0}}
.os-pname{{font-size:15px;font-weight:700}}
.os-pid{{font-size:12px;color:var(--muted);margin-top:2px}}
.os-row{{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid #ffffff08;font-size:13px}}
.os-row:last-child{{border-bottom:none}}
.os-label{{color:var(--muted)}}
.os-val{{font-weight:600}}
.os-total{{background:#0b1120;border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-top:16px;display:flex;justify-content:space-between;align-items:center}}
.os-total-label{{font-size:13px;color:var(--muted)}}
.os-total-val{{font-size:22px;font-weight:800;color:#4ade80}}
.pay-card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:24px}}
.pc-title{{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid var(--border)}}
.sim-card{{background:linear-gradient(135deg,#1e3a8a,#1d4ed8);border-radius:12px;padding:20px;margin-bottom:20px;position:relative;overflow:hidden}}
.sim-card::before{{content:'';position:absolute;top:-40px;right:-40px;width:160px;height:160px;background:rgba(255,255,255,.05);border-radius:50%}}
.sim-card-num{{font-size:17px;font-weight:700;letter-spacing:3px;color:#fff;margin-bottom:14px;font-family:monospace}}
.sim-card-bottom{{display:flex;justify-content:space-between;align-items:flex-end}}
.sim-label{{font-size:10px;color:#93c5fd;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}}
.sim-val{{font-size:13px;color:#fff;font-weight:600}}
.sim-badge{{background:rgba(255,255,255,.15);border-radius:6px;padding:4px 10px;font-size:12px;color:#fff;font-weight:700}}
.pay-field{{margin-bottom:14px}}
.pay-row{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}}
.pay-amount{{background:#0b1120;border:2px solid #166534;border-radius:8px;padding:11px 14px;font-size:15px;font-weight:700;color:#4ade80;display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
.pay-amount-label{{font-size:11px;color:var(--muted);font-weight:400}}
.lock-icon{{font-size:18px}}
.security-row{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}
.sec-chip{{background:#0b1120;border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:11px;color:var(--muted);display:flex;align-items:center;gap:4px}}
.steps{{display:flex;align-items:center;gap:0;margin-bottom:28px;padding:0 4px}}
.step{{flex:1;text-align:center;position:relative}}
.step-dot{{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;margin:0 auto 6px}}
.step-done{{background:#166534;color:#4ade80;border:2px solid #166534}}
.step-active{{background:#1d4ed8;color:#fff;border:2px solid #3b82f6}}
.step-idle{{background:var(--surface2);color:var(--muted);border:2px solid var(--border)}}
.step-label{{font-size:11px;color:var(--muted);font-weight:500}}
.step-line{{position:absolute;top:14px;left:50%;width:100%;height:2px;background:var(--border);z-index:-1}}
.step:last-child .step-line{{display:none}}
</style>
</head><body>
<header>
  <div class="logo">💳</div>
  <div><h1>Güvenli Ödeme</h1><p>Sipariş No: {order_id}</p></div>
  <div style="margin-left:auto"><span class="badge badge-yellow">⏳ Ödeme Bekleniyor</span></div>
</header>

<div style="max-width:860px;margin:0 auto;padding:24px 24px 0">
  <!-- Adım göstergesi -->
  <div class="steps">
    <div class="step">
      <div class="step-line"></div>
      <div class="step-dot step-done">✓</div>
      <div class="step-label">Sipariş</div>
    </div>
    <div class="step">
      <div class="step-line"></div>
      <div class="step-dot step-active">2</div>
      <div class="step-label">Ödeme</div>
    </div>
    <div class="step">
      <div class="step-dot step-idle">3</div>
      <div class="step-label">Onay</div>
    </div>
  </div>
</div>

<div class="checkout-wrap" id="main-wrap">
  <!-- Sipariş Özeti -->
  <div class="order-summary" id="order-summary">
    <div class="os-title">🧾 Sipariş Özeti</div>
    <div class="empty-state pulse">Yükleniyor…</div>
  </div>

  <!-- Ödeme Formu -->
  <div class="pay-card">
    <div class="pc-title">💳 Kart Bilgileri</div>

    <div class="sim-card">
      <div class="sim-card-num">4242 4242 4242 4242</div>
      <div class="sim-card-bottom">
        <div>
          <div class="sim-label">Kart Sahibi</div>
          <div class="sim-val" id="card-holder">TEST KULLANICI</div>
        </div>
        <div>
          <div class="sim-label">Son Kullanma</div>
          <div class="sim-val">12/28</div>
        </div>
        <div class="sim-badge">SİMÜLASYON</div>
      </div>
    </div>

    <div class="pay-field">
      <label>KART ÜZERİNDEKİ İSİM</label>
      <input id="card-name" type="text" value="Test Kullanıcı" oninput="document.getElementById('card-holder').textContent=this.value.toUpperCase()||'TEST KULLANICI'"/>
    </div>
    <div class="pay-field">
      <label>KART NUMARASI</label>
      <input type="text" value="4242 4242 4242 4242" readonly/>
    </div>
    <div class="pay-row">
      <div><label>SON KULLANMA</label><input type="text" value="12/28" readonly/></div>
      <div><label>CVV</label><input type="text" value="•••" readonly/></div>
    </div>

    <div class="pay-amount">
      <div>
        <div class="pay-amount-label">ÖDENECEK TUTAR</div>
        <div id="pay-amount-val">Yükleniyor…</div>
      </div>
      <div class="lock-icon">🔒</div>
    </div>

    <button class="btn btn-green" style="width:100%;font-size:15px;padding:13px" id="pay-btn" onclick="doPayment()">
      <span id="pay-txt">🔒 Ödemeyi Onayla</span>
    </button>

    <div class="security-row">
      <div class="sec-chip">🔒 256-bit SSL</div>
      <div class="sec-chip">🛡 Fraud Koruması</div>
      <div class="sec-chip">⚡ Anlık İşlem</div>
    </div>

    <div class="alert alert-error" id="pay-err"></div>
  </div>
</div>

<script>
const ORDER_ID='{order_id}';
const ICONS={{'PROD-001':'💻','PROD-002':'🖱️','PROD-003':'⌨️','PROD-004':'🖥️','PROD-005':'🎧'}};

async function loadOrder(){{
  try{{
    const r=await fetch(`/api/v1/orders/${{ORDER_ID}}`);
    if(!r.ok)throw new Error('Sipariş bulunamadı');
    const o=await r.json();
    const icon=ICONS[o.product_id]||'📦';
    const total='₺'+Number(o.total_price).toLocaleString('tr-TR',{{minimumFractionDigits:2}});
    document.getElementById('order-summary').innerHTML=`
      <div class="os-title">🧾 Sipariş Özeti</div>
      <div class="os-product">
        <div class="os-icon">${{icon}}</div>
        <div><div class="os-pname">${{o.product_name||o.product_id}}</div>
             <div class="os-pid">${{o.product_id}}</div></div>
      </div>
      <div class="os-row"><span class="os-label">Sipariş No</span><span class="os-val">${{o.order_id}}</span></div>
      <div class="os-row"><span class="os-label">Müşteri</span><span class="os-val">${{o.customer_id}}</span></div>
      <div class="os-row"><span class="os-label">Adet</span><span class="os-val">${{o.quantity}} adet</span></div>
      <div class="os-row"><span class="os-label">Birim Fiyat</span><span class="os-val">₺${{(o.total_price/o.quantity).toLocaleString('tr-TR',{{minimumFractionDigits:2}})}}</span></div>
      <div class="os-total">
        <div><div class="os-total-label">Toplam Tutar</div></div>
        <div class="os-total-val">${{total}}</div>
      </div>`;
    document.getElementById('pay-amount-val').textContent=total;
    document.getElementById('pay-amount-val').style.fontSize='20px';
    document.getElementById('pay-amount-val').style.fontWeight='800';
  }}catch(e){{
    document.getElementById('order-summary').innerHTML=`<div class="empty-state" style="color:#f87171">Sipariş yüklenemedi: ${{e.message}}</div>`;
  }}
}}

async function doPayment(){{
  const btn=document.getElementById('pay-btn');
  const txt=document.getElementById('pay-txt');
  const errEl=document.getElementById('pay-err');
  errEl.style.display='none';
  btn.disabled=true;
  txt.innerHTML='<div class="spinner"></div> Ödeme işleniyor…';
  try{{
    const r=await fetch(`/api/v1/orders/${{ORDER_ID}}/pay`,{{method:'POST',headers:{{'Content-Type':'application/json'}}}});
    const d=await r.json();
    if(r.ok){{
      window.location.href=`/confirmation/${{ORDER_ID}}?trace=${{d.trace_id}}`;
    }}else{{
      errEl.innerHTML=`✗ ${{d.detail||'Ödeme başarısız'}}`;
      errEl.style.display='block';
      btn.disabled=false;
      txt.textContent='🔒 Tekrar Dene';
    }}
  }}catch(e){{
    errEl.innerHTML=`✗ Bağlantı hatası: ${{e.message}}`;
    errEl.style.display='block';
    btn.disabled=false;
    txt.textContent='🔒 Ödemeyi Onayla';
  }}
}}

loadOrder();
</script>
</body></html>"""


#Confirmation Sayfası
@app.get("/confirmation/{order_id}", response_class=HTMLResponse)
async def confirmation_page(order_id: str, trace: str = ""):
    if trace:
        trace_card = (
            '<div class="conf-card">'
            '<div class="conf-row"><span class="conf-label">Trace ID</span>'
            '<span class="conf-val" style="font-family:monospace;font-size:12px">'
            + trace +
            '</span></div>'
            '<div class="conf-row"><span class="conf-label">Jaeger Trace</span>'
            '<a href="http://localhost:16686/trace/' + trace + '" target="_blank" '
            'style="color:#60a5fa;font-weight:600;font-size:13px">&#128269; Trace Görüntüle &#8594;</a>'
            '</div></div>'
        )
        trace_btn = (
            '<a href="http://localhost:16686/trace/' + trace + '" '
            'class="btn btn-ghost" target="_blank">&#128269; Jaeger Trace</a>'
        )
    else:
        trace_card = ""
        trace_btn = ""
    return f"""<!DOCTYPE html>
<html lang="tr"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Sipariş Onaylandı — {order_id}</title>{_BASE_STYLE}
<style>
.conf-wrap{{max-width:640px;margin:48px auto;padding:0 24px;text-align:center}}
.conf-icon{{font-size:72px;margin-bottom:20px}}
@keyframes pop{{0%{{transform:scale(.5);opacity:0}}80%{{transform:scale(1.1)}}100%{{transform:scale(1);opacity:1}}}}
.conf-icon{{animation:pop .5s ease-out}}
.conf-title{{font-size:28px;font-weight:800;color:#4ade80;margin-bottom:10px}}
.conf-sub{{font-size:15px;color:var(--muted);margin-bottom:32px}}
.conf-card{{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:28px;margin-bottom:24px;text-align:left}}
.conf-row{{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #ffffff08;font-size:14px}}
.conf-row:last-child{{border-bottom:none}}
.conf-label{{color:var(--muted)}}
.conf-val{{font-weight:600}}
.conf-actions{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}}
.steps{{display:flex;align-items:center;gap:0;margin:0 auto 32px;max-width:400px;padding:0 4px}}
.step{{flex:1;text-align:center;position:relative}}
.step-dot{{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;margin:0 auto 6px}}
.step-done{{background:#166534;color:#4ade80;border:2px solid #166534}}
.step-label{{font-size:11px;color:var(--muted);font-weight:500}}
.step-line{{position:absolute;top:14px;left:50%;width:100%;height:2px;background:#166534;z-index:-1}}
.step:last-child .step-line{{display:none}}
</style>
</head><body>
<header>
  <div class="logo">✅</div>
  <div><h1>Sipariş Onaylandı</h1><p>Sipariş No: {order_id}</p></div>
  <div style="margin-left:auto"><span class="badge badge-green">✓ Tamamlandı</span></div>
</header>

<div class="conf-wrap">
  <div class="steps">
    <div class="step"><div class="step-line"></div>
      <div class="step-dot step-done">✓</div><div class="step-label">Sipariş</div></div>
    <div class="step"><div class="step-line"></div>
      <div class="step-dot step-done">✓</div><div class="step-label">Ödeme</div></div>
    <div class="step">
      <div class="step-dot step-done">✓</div><div class="step-label">Onay</div></div>
  </div>

  <div class="conf-icon">✅</div>
  <div class="conf-title">Siparişiniz Onaylandı!</div>
  <div class="conf-sub">Ödemeniz başarıyla alındı. E-posta ve SMS bildirimi gönderildi.</div>

  <div class="conf-card" id="detail-card">
    <div style="text-align:center;color:var(--muted);padding:20px">Yükleniyor…</div>
  </div>

  {trace_card}

  <div class="conf-actions">
    <a href="/" class="btn btn-primary">&#8592; Ana Sayfaya Dön</a>
    <a href="http://localhost:8001" class="btn btn-ghost" target="_blank">&#128230; Order Service</a>
    {trace_btn}
  </div>
</div>

<script>
async function load(){{
  try{{
    const r=await fetch(`/api/v1/orders/{order_id}`);const o=await r.json();
    const ICONS={{'PROD-001':'💻','PROD-002':'🖱️','PROD-003':'⌨️','PROD-004':'🖥️','PROD-005':'🎧'}};
    const icon=ICONS[o.product_id]||'📦';
    document.getElementById('detail-card').innerHTML=`
      <div class="conf-row"><span class="conf-label">Sipariş No</span><span class="conf-val" style="font-family:monospace">${{o.order_id}}</span></div>
      <div class="conf-row"><span class="conf-label">Ürün</span><span class="conf-val">${{icon}} ${{o.product_name||o.product_id}}</span></div>
      <div class="conf-row"><span class="conf-label">Müşteri</span><span class="conf-val">${{o.customer_id}}</span></div>
      <div class="conf-row"><span class="conf-label">Adet</span><span class="conf-val">${{o.quantity}} adet</span></div>
      <div class="conf-row"><span class="conf-label">Tutar</span><span class="conf-val" style="color:#4ade80;font-size:17px">₺${{Number(o.total_price).toLocaleString('tr-TR',{{minimumFractionDigits:2}})}}</span></div>
      <div class="conf-row"><span class="conf-label">Durum</span><span class="conf-val" style="color:#4ade80">✓ Onaylandı</span></div>`;
  }}catch{{}}
}}
load();
</script>
</body></html>"""


@app.get("/tests", response_class=HTMLResponse)
async def test_panel():
    html_path = pathlib.Path(__file__).parent / "jmeter-tests" / "test-panel.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"/>
<title>Test Paneli</title>{_BASE_STYLE}</head><body>
<header><div class="logo">🧪</div>
<div><h1>JMeter Test Paneli</h1><p>test-panel.html bulunamadı</p></div></header>
<div class="main" style="max-width:600px">
  <div class="card">
    <div class="card-title">⚠ Dosya Bulunamadı</div>
    <p style="color:var(--muted);font-size:14px">jmeter-tests/test-panel.html eksik.</p>
    <div style="margin-top:14px;display:flex;gap:10px">
      <a href="/" class="btn btn-ghost">← Ana Sayfa</a>
    </div>
  </div>
</div></body></html>"""



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
