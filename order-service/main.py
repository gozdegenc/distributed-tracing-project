import os
import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.propagate import inject
from tracing import setup_tracing
from prometheus_fastapi_instrumentator import Instrumentator
from payment import process_payment, transactions as payment_transactions

app = FastAPI(title="Order Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
Instrumentator().instrument(app).expose(app)
tracer = setup_tracing("order-service", app)

INVENTORY_URL = os.getenv("INVENTORY_SERVICE_URL",   "http://inventory-service:8002")
NOTIF_URL     = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8004")

orders: dict = {}
order_counter = 0

PRODUCT_CATALOG = {
    "PROD-001": {"name": "Laptop",              "price": 14999.00},
    "PROD-002": {"name": "Kablosuz Mouse",       "price":   299.00},
    "PROD-003": {"name": "Mekanik Klavye",       "price":   799.00},
    "PROD-004": {"name": "4K Monitör",           "price":  4999.00},
    "PROD-005": {"name": "Bluetooth Kulaklık",   "price":   599.00},
}


class OrderRequest(BaseModel):
    customer_id: str
    product_id:  str
    quantity:    int
    total_price: float


class OrderResponse(BaseModel):
    order_id:   str
    status:     str
    message:    str
    trace_id:   str


def traced_headers() -> dict:
    h = {}
    inject(h)
    return h


#HTML Sayfası
@app.get("/", response_class=HTMLResponse)
async def root():
    return """<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Order Service</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0b1120;color:#e2e8f0;min-height:100vh}
header{background:linear-gradient(90deg,#0f2460,#1a3a7a);padding:18px 32px;display:flex;align-items:center;gap:14px;border-bottom:1px solid #1e3a5f;box-shadow:0 2px 20px rgba(0,0,0,.5)}
.logo{width:36px;height:36px;background:#3b82f6;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
header h1{font-size:19px;font-weight:700}header p{font-size:12px;color:#93c5fd;margin-top:2px}
.badge{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;margin-left:auto;background:#1e3a8a44;color:#93c5fd;border:1px solid #1d4ed8}
.main{max-width:1100px;margin:0 auto;padding:28px 24px;display:grid;gap:22px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}
@media(max-width:700px){.row3{grid-template-columns:1fr}}
.stat{background:#131f35;border:1px solid #1e3a5f;border-radius:12px;padding:18px 20px}
.stat-label{font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px}
.stat-value{font-size:30px;font-weight:800;margin-top:6px}
.stat-sub{font-size:12px;color:#64748b;margin-top:5px}
.card{background:#131f35;border:1px solid #1e3a5f;border-radius:14px;padding:22px}
.card-title{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.9px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #1e3a5f;display:flex;align-items:center;gap:8px}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:9px 12px;color:#64748b;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #1e3a5f}
td{padding:11px 12px;border-bottom:1px solid #ffffff08;vertical-align:middle}
tr:last-child td{border-bottom:none}tr:hover td{background:#ffffff04}
.tag{display:inline-block;padding:2px 8px;border-radius:5px;font-size:11px;font-weight:700;background:#1e3a8a44;color:#93c5fd;border:1px solid #1d4ed844;font-family:monospace}
.chip-green{background:#052e1655;color:#4ade80;border:1px solid #166534;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600;display:inline-block}
.chip-yellow{background:#42200644;color:#fcd34d;border:1px solid #92400e;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600;display:inline-block}
.chip-red{background:#1c0a0055;color:#f87171;border:1px solid #7f1d1d;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600;display:inline-block}
.empty{text-align:center;color:#475569;padding:32px 0;font-size:14px}
.trace-link{color:#60a5fa;text-decoration:none;font-size:12px;font-weight:600}
.trace-link:hover{text-decoration:underline}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;background:#1a2942;color:#cbd5e1;border:1px solid #1e3a5f;transition:background .15s}
.btn:hover{background:#2a3f5f}
</style></head><body>
<header>
  <div class="logo">📦</div>
  <div><h1>Order Service</h1><p>Sipariş yönetimi mikro servisi · port 8001</p></div>
  <span class="badge">Running</span>
</header>
<div class="main">
  <div class="row3">
    <div class="stat"><div class="stat-label">Toplam Sipariş</div><div class="stat-value" id="s-total">—</div><div class="stat-sub">Bellekte tutulan</div></div>
    <div class="stat"><div class="stat-label">Onaylanan</div><div class="stat-value" style="color:#4ade80" id="s-ok">—</div><div class="stat-sub" id="s-rate">Hesaplanıyor</div></div>
    <div class="stat"><div class="stat-label">Bekleyen Ödeme</div><div class="stat-value" style="color:#fcd34d" id="s-pending">—</div><div class="stat-sub">Ödeme bekleniyor</div></div>
  </div>
  <div class="card">
    <div class="card-title">📋 Tüm Siparişler <button class="btn" style="margin-left:auto" onclick="load()">↻ Yenile</button></div>
    <div class="tbl-wrap">
      <table><thead><tr><th>Sipariş No</th><th>Müşteri</th><th>Ürün</th><th>Adet</th><th>Tutar</th><th>Durum</th><th>Trace</th></tr></thead>
      <tbody id="tbody"><tr><td colspan="7" class="empty">Yükleniyor…</td></tr></tbody></table>
    </div>
  </div>
  <div class="card">
    <div class="card-title">💳 Ödeme İşlemleri <button class="btn" style="margin-left:auto" onclick="loadTx()">↻ Yenile</button></div>
    <div class="row3" style="margin-bottom:16px">
      <div class="stat"><div class="stat-label">Toplam İşlem</div><div class="stat-value" id="tx-total">—</div><div class="stat-sub">Tüm ödemeler</div></div>
      <div class="stat"><div class="stat-label">Başarılı</div><div class="stat-value" style="color:#4ade80" id="tx-ok">—</div><div class="stat-sub" id="tx-rate">Hesaplanıyor</div></div>
      <div class="stat"><div class="stat-label">Toplam Ciro</div><div class="stat-value" style="color:#a78bfa;font-size:20px" id="tx-rev">—</div><div class="stat-sub">₺ toplam</div></div>
    </div>
    <div class="tbl-wrap">
      <table><thead><tr><th>TX ID</th><th>Sipariş</th><th>Müşteri</th><th>Tutar</th><th>Durum</th></tr></thead>
      <tbody id="tx-body"><tr><td colspan="5" class="empty">Yükleniyor…</td></tr></tbody></table>
    </div>
  </div>
  <div class="card">
    <div class="card-title">🔗 Bağlantılar</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <a href="http://localhost:8000" style="color:#60a5fa;text-decoration:none;background:#1a2942;border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">← Ana Dashboard</a>
      <a href="http://localhost:16686" target="_blank" style="color:#60a5fa;text-decoration:none;background:#1a2942;border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">🔍 Jaeger UI</a>
      <a href="/docs" target="_blank" style="color:#60a5fa;text-decoration:none;background:#1a2942;border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">📄 Swagger Docs</a>
    </div>
  </div>
</div>
<script>
const P={'PROD-001':'Laptop','PROD-002':'Kablosuz Mouse','PROD-003':'Mekanik Klavye','PROD-004':'4K Monitör','PROD-005':'Bluetooth Kulaklık'};
const I={'PROD-001':'💻','PROD-002':'🖱️','PROD-003':'⌨️','PROD-004':'🖥️','PROD-005':'🎧'};
async function load(){
  try{
    const r=await fetch('/orders');const d=await r.json();
    const orders=(d.orders||[]).slice().reverse();
    const ok=orders.filter(o=>o.status==='confirmed').length;
    const pending=orders.filter(o=>o.status==='pending_payment').length;
    document.getElementById('s-total').textContent=orders.length;
    document.getElementById('s-ok').textContent=ok;
    document.getElementById('s-pending').textContent=pending;
    document.getElementById('s-rate').textContent=orders.length?`%${Math.round(ok/orders.length*100)} başarı`:'Henüz sipariş yok';
    if(!orders.length){document.getElementById('tbody').innerHTML='<tr><td colspan="7" class="empty">Henüz sipariş yok</td></tr>';return;}
    document.getElementById('tbody').innerHTML=orders.map(o=>{
      const chip=o.status==='confirmed'?'<span class="chip-green">✓ Onaylandı</span>':o.status==='pending_payment'?'<span class="chip-yellow">⏳ Ödeme Bekliyor</span>':'<span class="chip-red">'+o.status+'</span>';
      return `<tr>
        <td><span class="tag">${o.order_id}</span></td>
        <td>${o.customer_id}</td>
        <td>${I[o.product_id]||'📦'} ${P[o.product_id]||o.product_id}</td>
        <td style="text-align:center">${o.quantity}</td>
        <td style="color:#4ade80;font-weight:600">₺${Number(o.total_price).toLocaleString('tr-TR',{minimumFractionDigits:2})}</td>
        <td>${chip}</td>
        <td>${o.trace_id?`<a class="trace-link" href="http://localhost:16686/trace/${o.trace_id}" target="_blank">🔍 Trace</a>`:'—'}</td>
      </tr>`;
    }).join('');
  }catch(e){document.getElementById('tbody').innerHTML=`<tr><td colspan="7" class="empty">Hata: ${e.message}</td></tr>`}
}
async function loadTx(){
  try{
    const r=await fetch('/transactions');const d=await r.json();
    const txs=(d.transactions||[]).slice().reverse();
    document.getElementById('tx-total').textContent=d.total||0;
    document.getElementById('tx-ok').textContent=d.successful||0;
    document.getElementById('tx-rate').textContent=d.total?`%${Math.round((d.successful/d.total)*100)} başarı oranı`:'Henüz işlem yok';
    document.getElementById('tx-rev').textContent='₺'+(d.total_revenue||0).toLocaleString('tr-TR',{minimumFractionDigits:2});
    if(!txs.length){document.getElementById('tx-body').innerHTML='<tr><td colspan="5" class="empty">Henüz ödeme işlemi yok</td></tr>';return;}
    const statusChip=s=>s==='success'?'<span class="chip-green">✓ Başarılı</span>':s==='fraud_blocked'?'<span class="chip-red">🚫 Fraud</span>':'<span class="chip-red">✗ Banka Hatası</span>';
    document.getElementById('tx-body').innerHTML=txs.slice(0,20).map(t=>`<tr>
      <td><span class="tag">${t.transaction_id}</span></td>
      <td style="font-size:12px;color:#94a3b8">${t.order_id}</td>
      <td style="font-size:12px;color:#94a3b8">${t.customer_id}</td>
      <td style="color:#4ade80;font-weight:600">₺${Number(t.amount).toLocaleString('tr-TR',{minimumFractionDigits:2})}</td>
      <td>${statusChip(t.status)}</td>
    </tr>`).join('');
  }catch(e){document.getElementById('tx-body').innerHTML=`<tr><td colspan="5" class="empty">Hata: ${e.message}</td></tr>`}
}
load();loadTx();setInterval(()=>{load();loadTx();},8000);
</script></body></html>"""


#API Endpoint'leri
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "order-service"}


@app.get("/orders")
async def list_orders():
    with tracer.start_as_current_span("list-orders") as span:
        span.set_attribute("orders.count", len(orders))
        return {"orders": list(orders.values()), "total": len(orders)}


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    with tracer.start_as_current_span("get-order") as span:
        span.set_attribute("order.id", order_id)
        if order_id not in orders:
            span.set_status(Status(StatusCode.ERROR, "Order not found"))
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        return orders[order_id]


@app.post("/orders/initiate")
async def initiate_order(order: OrderRequest):
    """
    ADIM 1 — Sipariş başlat.
    Stok kontrolü yapar, sipariş kaydeder (pending_payment). Kullanıcı ödeme sayfasına yönlendirilir.
    """
    global order_counter

    with tracer.start_as_current_span("initiate-order") as root_span:
        root_span.set_attribute("order.customer_id", order.customer_id)
        root_span.set_attribute("order.product_id",  order.product_id)
        root_span.set_attribute("order.quantity",    order.quantity)
        root_span.set_attribute("order.total_price", order.total_price)

        ctx = trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, '032x')

        #Doğrulama
        with tracer.start_as_current_span("validate-order") as span:
            if order.quantity <= 0:
                raise HTTPException(status_code=400, detail="Geçersiz miktar")
            if order.total_price <= 0:
                raise HTTPException(status_code=400, detail="Geçersiz fiyat")
            span.set_attribute("validation.ok", True)
            await asyncio.sleep(0.01)

        #Stok kontrolü (rezervasyon YAPILMAZ — ödeme onayında yapılır)
        with tracer.start_as_current_span("check-inventory-availability") as span:
            span.set_attribute("product.id",       order.product_id)
            span.set_attribute("product.quantity",  order.quantity)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{INVENTORY_URL}/stock",
                    headers=traced_headers(),
                    timeout=5.0,
                )
            stock_data = resp.json().get("stock", {})
            available_qty = stock_data.get(order.product_id, 0)
            span.set_attribute("inventory.available_qty", available_qty)
            if available_qty < order.quantity:
                raise HTTPException(status_code=400, detail=f"Yetersiz stok. Mevcut: {available_qty} adet")

        #Siparişi "pending_payment" durumunda kaydet
        order_counter += 1
        order_id = f"ORD-{order_counter:04d}"
        product_name = PRODUCT_CATALOG.get(order.product_id, {}).get("name", order.product_id)

        orders[order_id] = {
            "order_id":      order_id,
            "customer_id":   order.customer_id,
            "product_id":    order.product_id,
            "product_name":  product_name,
            "quantity":      order.quantity,
            "total_price":   order.total_price,
            "status":        "pending_payment",
            "trace_id":      trace_id,
        }

        root_span.set_attribute("order.id",     order_id)
        root_span.set_attribute("order.status", "pending_payment")
        root_span.set_status(Status(StatusCode.OK))

        return {
            "order_id":     order_id,
            "status":       "pending_payment",
            "product_name": product_name,
            "total_price":  order.total_price,
            "customer_id":  order.customer_id,
            "product_id":   order.product_id,
            "quantity":     order.quantity,
            "trace_id":     trace_id,
            "message":      "Sipariş oluşturuldu. Ödeme bekleniyor.",
        }


@app.post("/orders/{order_id}/pay", response_model=OrderResponse)
async def pay_order(order_id: str):
    
    #ADIM 2 — Ödemeyi onayla. Stok rezervasyonu → ödeme işlemi → bildirim → sipariş onayı.
    
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    order_data = orders[order_id]
    if order_data["status"] != "pending_payment":
        raise HTTPException(status_code=400, detail=f"Bu sipariş zaten işleme alındı: {order_data['status']}")

    with tracer.start_as_current_span("confirm-payment-flow") as root_span:
        root_span.set_attribute("order.id",          order_id)
        root_span.set_attribute("order.customer_id", order_data["customer_id"])
        root_span.set_attribute("order.total_price", order_data["total_price"])

        ctx = trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, '032x')

        try:
            #Stok Rezervasyonu
            with tracer.start_as_current_span("reserve-inventory") as span:
                span.set_attribute("product.id",      order_data["product_id"])
                span.set_attribute("product.quantity", order_data["quantity"])
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{INVENTORY_URL}/check",
                        json={"product_id": order_data["product_id"], "quantity": order_data["quantity"]},
                        headers=traced_headers(),
                        timeout=5.0,
                    )
                inv = resp.json()
                span.set_attribute("inventory.reserved", inv.get("available", False))
                if not inv.get("available"):
                    raise HTTPException(status_code=400, detail="Stok tükendi, sipariş iptal edildi")

            #Ödeme İşlemi
            with tracer.start_as_current_span("process-payment") as span:
                span.set_attribute("payment.amount",   order_data["total_price"])
                span.set_attribute("payment.customer", order_data["customer_id"])
                pay = await process_payment(
                    order_id=order_id,
                    customer_id=order_data["customer_id"],
                    amount=order_data["total_price"],
                )
                span.set_attribute("payment.transaction_id", pay.get("transaction_id", ""))
                span.set_attribute("payment.status",         pay.get("status", ""))

            #Bildirim
            with tracer.start_as_current_span("send-notification") as span:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{NOTIF_URL}/send",
                        json={
                            "customer_id": order_data["customer_id"],
                            "order_id":    order_id,
                            "message":     f"Siparişiniz ({order_id}) onaylandı!",
                        },
                        headers=traced_headers(),
                        timeout=5.0,
                    )
                span.set_attribute("notification.sent", resp.status_code == 200)

            #Siparişi Güncelle
            orders[order_id]["status"]           = "confirmed"
            orders[order_id]["trace_id"]         = trace_id
            orders[order_id]["transaction_id"]   = pay.get("transaction_id", "")

            root_span.set_attribute("order.final_status", "confirmed")
            root_span.set_status(Status(StatusCode.OK))

            return OrderResponse(
                order_id=order_id,
                status="confirmed",
                message="Sipariş ve ödeme başarıyla tamamlandı",
                trace_id=trace_id,
            )

        except HTTPException:
            orders[order_id]["status"] = "payment_failed"
            root_span.set_status(Status(StatusCode.ERROR))
            raise
        except Exception as e:
            orders[order_id]["status"] = "payment_failed"
            root_span.set_status(Status(StatusCode.ERROR, str(e)))
            root_span.record_exception(e)
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics/summary")
async def metrics_summary():
    total     = len(orders)
    confirmed = sum(1 for o in orders.values() if o.get("status") == "confirmed")
    return {
        "total_orders":     total,
        "confirmed_orders": confirmed,
        "success_rate":     (confirmed / total * 100) if total > 0 else 0,
    }


@app.get("/transactions")
async def get_transactions():
    total    = len(payment_transactions)
    success  = sum(1 for t in payment_transactions if t.get("status") == "success")
    revenue  = sum(t["amount"] for t in payment_transactions if t.get("status") == "success")
    return {
        "transactions": payment_transactions,
        "total":        total,
        "successful":   success,
        "failed":       total - success,
        "total_revenue": round(revenue, 2),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
