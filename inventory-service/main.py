import os
import asyncio
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import extract
from prometheus_fastapi_instrumentator import Instrumentator

JAEGER_ENDPOINT = os.getenv("JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")

#In-memory veri deposu
_products: dict[str, dict] = {}
_settings: dict[str, str]  = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_data():
    global _products, _settings
    ts = _now()
    _products = {
        "PROD-001": {"id": "PROD-001", "name": "Laptop",            "price": 14999.00, "category": "Bilgisayar", "icon": "💻", "stock": 100, "active": True,  "created_at": ts, "updated_at": ts},
        "PROD-002": {"id": "PROD-002", "name": "Kablosuz Mouse",    "price":   299.00, "category": "Aksesuar",   "icon": "🖱️",  "stock":  50, "active": True, "created_at": ts, "updated_at": ts},
        "PROD-003": {"id": "PROD-003", "name": "Mekanik Klavye",    "price":   799.00, "category": "Aksesuar",   "icon": "⌨️", "stock": 200, "active": True, "created_at": ts, "updated_at": ts},
        "PROD-004": {"id": "PROD-004", "name": "4K Monitör",        "price":  4999.00, "category": "Ekran",      "icon": "🖥️",   "stock":   5, "active": True, "created_at": ts, "updated_at": ts},
        "PROD-005": {"id": "PROD-005", "name": "Bluetooth Kulaklık","price":   599.00, "category": "Ses",        "icon": "🎧",  "stock":  75, "active": True, "created_at": ts, "updated_at": ts},
    }
    _settings = {
        "theme.bg":      "#0b1120",
        "theme.surface": "#131f35",
        "theme.border":  "#1e3a5f",
        "theme.accent":  "#3b82f6",
        "theme.text":    "#e2e8f0",
        "theme.muted":   "#64748b",
        "site.title":    "E-Ticaret Sipariş Sistemi",
        "site.subtitle": "Distributed Tracing Demo",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_data()
    yield


app = FastAPI(title="Inventory Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
Instrumentator().instrument(app).expose(app)

#OpenTelemetry
resource = Resource.create({"service.name": "inventory-service", "service.version": "2.0.0"})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=JAEGER_ENDPOINT)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("inventory-service")
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()


#Modeller
class InventoryCheckRequest(BaseModel):
    product_id: str
    quantity:   int

class ProductCreate(BaseModel):
    id:       str
    name:     str
    price:    float
    category: str = ""
    icon:     str = "📦"
    stock:    int = 0

class ProductUpdate(BaseModel):
    name:     Optional[str]   = None
    price:    Optional[float] = None
    category: Optional[str]   = None
    icon:     Optional[str]   = None
    active:   Optional[bool]  = None

class StockAdjust(BaseModel):
    delta: int  # pozitif = artır, negatif = azalt

class SettingUpdate(BaseModel):
    value: str


#HTML Ana Sayfa
@app.get("/", response_class=HTMLResponse)
async def root():
    products = sorted(_products.values(), key=lambda p: p["id"])
    total_stock = sum(p["stock"] for p in products)
    out_of_stock = sum(1 for p in products if p["stock"] == 0)

    rows_html = ""
    for p in products:
        qty = p["stock"]
        col = "color:#ef4444" if qty == 0 else ("color:#f59e0b" if qty <= 5 else "color:#22c55e")
        status = "TÜKENDİ" if qty == 0 else (f"Son {qty}" if qty <= 5 else str(qty))
        rows_html += (
            f'<tr><td><span style="font-family:monospace;font-size:11px;background:#1e3a8a44;'
            f'color:#93c5fd;border:1px solid #1d4ed844;padding:2px 8px;border-radius:4px">{p["id"]}</span></td>'
            f'<td>{p["icon"]} {p["name"]}</td>'
            f'<td style="color:#94a3b8">{p["category"]}</td>'
            f'<td style="color:#4ade80;font-weight:600">'
            f'&#8378;{float(p["price"]):,.2f}</td>'
            f'<td style="{col};font-weight:700">{status}</td>'
            f'<td><span style="background:{"#052e1655" if p["active"] else "#1c0a0055"};'
            f'color:{"#4ade80" if p["active"] else "#f87171"};'
            f'border:1px solid {"#166534" if p["active"] else "#7f1d1d"};'
            f'padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">'
            f'{"Aktif" if p["active"] else "Pasif"}</span></td></tr>'
        )

    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Inventory Service</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0b1120;color:#e2e8f0;min-height:100vh}}
header{{background:linear-gradient(90deg,#064e3b,#065f46);padding:18px 32px;display:flex;align-items:center;
  gap:14px;border-bottom:1px solid #065f46;box-shadow:0 2px 20px rgba(0,0,0,.5)}}
.logo{{width:36px;height:36px;background:#10b981;border-radius:8px;display:flex;align-items:center;
  justify-content:center;font-size:18px;flex-shrink:0}}
header h1{{font-size:19px;font-weight:700}}header p{{font-size:12px;color:#6ee7b7;margin-top:2px}}
.badge{{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase;
  margin-left:auto;background:#052e1644;color:#86efac;border:1px solid #166534}}
.main{{max-width:1100px;margin:0 auto;padding:28px 24px;display:grid;gap:22px}}
.row3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}}
@media(max-width:700px){{.row3{{grid-template-columns:1fr}}}}
.stat{{background:#131f35;border:1px solid #1e3a5f;border-radius:12px;padding:18px 20px}}
.stat-label{{font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px}}
.stat-value{{font-size:30px;font-weight:800;margin-top:6px}}
.stat-sub{{font-size:12px;color:#64748b;margin-top:5px}}
.card{{background:#131f35;border:1px solid #1e3a5f;border-radius:14px;padding:22px}}
.card-title{{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.9px;
  margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #1e3a5f;display:flex;align-items:center;gap:8px}}
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:9px 12px;color:#64748b;font-size:11px;font-weight:600;text-transform:uppercase;
  letter-spacing:.5px;border-bottom:1px solid #1e3a5f}}
td{{padding:11px 12px;border-bottom:1px solid #ffffff08}}
tr:last-child td{{border-bottom:none}}tr:hover td{{background:#ffffff04}}
.btn{{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:7px;font-size:12px;
  font-weight:600;cursor:pointer;background:#1a2942;color:#cbd5e1;border:1px solid #1e3a5f}}
.btn:hover{{background:#2a3f5f}}
</style></head><body>
<header>
  <div class="logo">&#128230;</div>
  <div><h1>Inventory Service</h1><p>Stok yonetimi mikro servisi · port 8002 · In-Memory</p></div>
  <span class="badge">Running</span>
</header>
<div class="main">
  <div class="row3">
    <div class="stat"><div class="stat-label">Toplam Urun</div>
      <div class="stat-value">{len(products)}</div><div class="stat-sub">Aktif urunler</div></div>
    <div class="stat"><div class="stat-label">Toplam Stok</div>
      <div class="stat-value" style="color:#4ade80">{total_stock}</div>
      <div class="stat-sub">Tum urunlerin toplami</div></div>
    <div class="stat"><div class="stat-label">Stokta Yok</div>
      <div class="stat-value" style="color:#f87171">{out_of_stock}</div>
      <div class="stat-sub">Tukenen urun sayisi</div></div>
  </div>
  <div class="card">
    <div class="card-title">&#128230; Urun Stok Durumu
      <a href="http://localhost:8000/admin" style="margin-left:auto;color:#60a5fa;text-decoration:none;
        background:#1a2942;border:1px solid #1e3a5f;padding:5px 12px;border-radius:6px;font-size:11px;font-weight:600">
        &#9881; Admin Paneli
      </a>
    </div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Urun ID</th><th>Urun Adi</th><th>Kategori</th><th>Fiyat</th><th>Stok</th><th>Durum</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>
  </div>
  <div class="card">
    <div class="card-title">&#128279; Baglanti</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <a href="http://localhost:8000" style="color:#60a5fa;text-decoration:none;background:#1a2942;
        border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">&#8592; Ana Dashboard</a>
      <a href="http://localhost:8000/admin" style="color:#60a5fa;text-decoration:none;background:#1a2942;
        border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">&#9881; Admin Paneli</a>
      <a href="/docs" target="_blank" style="color:#60a5fa;text-decoration:none;background:#1a2942;
        border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">&#128196; Swagger Docs</a>
    </div>
  </div>
</div>
</body></html>"""


#Sağlık
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "inventory-service", "storage": "in-memory"}


#Stok Kontrolü
@app.post("/check")
async def check_inventory(req: InventoryCheckRequest, request: Request):
    ctx = extract(dict(request.headers))

    with tracer.start_as_current_span("inventory-check", context=ctx) as span:
        span.set_attribute("product.id",        req.product_id)
        span.set_attribute("product.requested", req.quantity)

        with tracer.start_as_current_span("db-query-stock") as db_span:
            await asyncio.sleep(random.uniform(0.01, 0.04))
            product = _products.get(req.product_id)
            db_span.set_attribute("db.operation", "SELECT")
            db_span.set_attribute("db.table", "products")

        if product is None or not product["active"]:
            span.set_status(Status(StatusCode.ERROR, "Product not found"))
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")

        current_stock = product["stock"]
        available = current_stock >= req.quantity
        span.set_attribute("inventory.available",     available)
        span.set_attribute("inventory.current_stock", current_stock)

        if available:
            with tracer.start_as_current_span("reserve-stock") as res_span:
                await asyncio.sleep(random.uniform(0.005, 0.02))
                product["stock"] -= req.quantity
                product["updated_at"] = _now()
                res_span.set_attribute("stock.reserved",  req.quantity)
                res_span.set_attribute("stock.remaining", product["stock"])
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.ERROR, "Insufficient stock"))

    return {
        "product_id":    req.product_id,
        "available":     available,
        "current_stock": current_stock,
        "requested":     req.quantity,
    }


#Stok Listesi
@app.get("/stock")
async def get_stock():
    return {"stock": {pid: p["stock"] for pid, p in _products.items() if p["active"]}}


#Ürün Listesi
@app.get("/products")
async def list_products():
    fields = ("id", "name", "price", "category", "icon", "stock", "active")
    return {"products": [{f: p[f] for f in fields} for p in sorted(_products.values(), key=lambda x: x["id"])]}


#Ürün Ekle
@app.post("/products", status_code=201)
async def create_product(product: ProductCreate):
    if product.id in _products:
        raise HTTPException(status_code=409, detail=f"Ürün zaten var: {product.id}")
    ts = _now()
    _products[product.id] = {
        "id": product.id, "name": product.name, "price": product.price,
        "category": product.category, "icon": product.icon, "stock": product.stock,
        "active": True, "created_at": ts, "updated_at": ts,
    }
    return {"message": "Ürün eklendi", "id": product.id}


#Ürün Güncelle
@app.put("/products/{product_id}")
async def update_product(product_id: str, data: ProductUpdate):
    if product_id not in _products:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Güncellenecek alan yok")
    _products[product_id].update(updates)
    _products[product_id]["updated_at"] = _now()
    return {"message": "Ürün güncellendi", "id": product_id}


#Stok Ayarla
@app.post("/products/{product_id}/stock")
async def adjust_stock(product_id: str, adj: StockAdjust):
    product = _products.get(product_id)
    if not product or not product["active"]:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    new_stock = product["stock"] + adj.delta
    if new_stock < 0:
        raise HTTPException(
            status_code=400,
            detail=f"Yetersiz stok. Mevcut: {product['stock']}, azaltılmak istenen: {abs(adj.delta)}",
        )
    previous = product["stock"]
    product["stock"] = new_stock
    product["updated_at"] = _now()
    return {"product_id": product_id, "previous": previous, "new_stock": new_stock, "delta": adj.delta}


#Ürün Sil
@app.delete("/products/{product_id}")
async def delete_product(product_id: str):
    if product_id not in _products:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    _products[product_id]["active"] = False
    _products[product_id]["updated_at"] = _now()
    return {"message": "Ürün devre dışı bırakıldı", "id": product_id}


#Ayarlar
@app.get("/settings")
async def get_settings():
    return {"settings": dict(_settings)}


@app.put("/settings/{key}")
async def update_setting(key: str, data: SettingUpdate):
    _settings[key] = data.value
    return {"key": key, "value": data.value}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
