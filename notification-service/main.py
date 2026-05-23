import os
import asyncio
import random
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="Notification Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
Instrumentator().instrument(app).expose(app)

# ── Tracing Kurulumu ──────────────────────────────────────────────────────────
JAEGER_ENDPOINT = os.getenv("JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")
resource = Resource.create({"service.name": "notification-service", "service.version": "1.0.0"})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=JAEGER_ENDPOINT)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("notification-service")
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

notifications_sent = []


class NotificationRequest(BaseModel):
    customer_id: str
    order_id:    str
    message:     str


@app.get("/", response_class=HTMLResponse)
async def root():
    return """<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Notification Service</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0b1120;color:#e2e8f0;min-height:100vh}
header{background:linear-gradient(90deg,#7c2d12,#9a3412);padding:18px 32px;display:flex;align-items:center;gap:14px;border-bottom:1px solid #9a3412;box-shadow:0 2px 20px rgba(0,0,0,.5)}
.logo{width:36px;height:36px;background:#ea580c;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
header h1{font-size:19px;font-weight:700}header p{font-size:12px;color:#fdba74;margin-top:2px}
.badge{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;margin-left:auto;background:#431407aa;color:#fdba74;border:1px solid #c2410c}
.main{max-width:950px;margin:0 auto;padding:28px 24px;display:grid;gap:22px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}
@media(max-width:700px){.row3{grid-template-columns:1fr}}
.stat{background:#131f35;border:1px solid #1e3a5f;border-radius:12px;padding:16px 18px}
.stat-label{font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px}
.stat-value{font-size:28px;font-weight:800;margin-top:5px}
.stat-sub{font-size:11px;color:#64748b;margin-top:4px}
.card{background:#131f35;border:1px solid #1e3a5f;border-radius:14px;padding:22px}
.card-title{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.9px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #1e3a5f;display:flex;align-items:center;gap:8px}
.notif-list{display:grid;gap:10px}
.notif-item{background:#0b1120;border:1px solid #1e3a5f;border-radius:10px;padding:14px 16px;display:grid;grid-template-columns:auto 1fr auto;align-items:start;gap:12px}
.notif-icon{width:38px;height:38px;background:#431407;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;margin-top:1px}
.notif-order{font-size:12px;color:#64748b;margin-top:3px}
.channel-list{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.ch{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600}
.ch-email{background:#052e1655;color:#86efac;border:1px solid #166534}
.ch-sms  {background:#1e3a8a44;color:#93c5fd;border:1px solid #1d4ed8}
.notif-time{font-size:11px;color:#64748b;white-space:nowrap}
.empty{text-align:center;color:#475569;padding:32px;font-size:14px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid #1e3a5f;background:#1a2942;color:#cbd5e1}
.btn:hover{background:#2a3f5f}
</style></head><body>
<header>
  <div class="logo">🔔</div>
  <div><h1>Notification Service</h1><p>Bildirim gönderim mikro servisi · port 8004</p></div>
  <span class="badge">Running</span>
</header>
<div class="main">
  <div class="row3">
    <div class="stat"><div class="stat-label">Toplam Bildirim</div><div class="stat-value" id="s-total">—</div><div class="stat-sub">Gönderilen</div></div>
    <div class="stat"><div class="stat-label">E-posta</div><div class="stat-value" style="color:#4ade80" id="s-email">—</div><div class="stat-sub">Email gönderimi</div></div>
    <div class="stat"><div class="stat-label">SMS</div><div class="stat-value" style="color:#60a5fa" id="s-sms">—</div><div class="stat-sub">SMS gönderimi</div></div>
  </div>
  <div class="card">
    <div class="card-title">🔔 Bildirim Geçmişi <button class="btn" style="margin-left:auto" onclick="load()">↻ Yenile</button></div>
    <div class="notif-list" id="notif-list"><div class="empty">Yükleniyor…</div></div>
  </div>
  <div class="card">
    <div class="card-title">🔗 Bağlantılar</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <a href="http://localhost:8000" style="color:#60a5fa;text-decoration:none;background:#1a2942;border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">← API Gateway</a>
      <a href="http://localhost:16686" target="_blank" style="color:#60a5fa;text-decoration:none;background:#1a2942;border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">🔍 Jaeger UI</a>
      <a href="/docs" target="_blank" style="color:#60a5fa;text-decoration:none;background:#1a2942;border:1px solid #1e3a5f;padding:8px 16px;border-radius:8px;font-size:13px">📄 Swagger Docs</a>
    </div>
  </div>
</div>
<script>
let counter=0;
async function load(){
  try{
    const r=await fetch('/notifications');const d=await r.json();
    const notifs=(d.notifications||[]).slice().reverse();
    document.getElementById('s-total').textContent=notifs.length;
    document.getElementById('s-email').textContent=notifs.filter(n=>n.channels?.includes('email')).length;
    document.getElementById('s-sms').textContent=notifs.filter(n=>n.channels?.includes('sms')).length;
    if(!notifs.length){document.getElementById('notif-list').innerHTML='<div class="empty">Henüz bildirim gönderilmedi</div>';return;}
    document.getElementById('notif-list').innerHTML=notifs.map((n,i)=>`
      <div class="notif-item">
        <div class="notif-icon">🔔</div>
        <div>
          <div style="font-size:13px;font-weight:600">${n.customer_id} · Sipariş Onayı</div>
          <div class="notif-order">${n.order_id}</div>
          <div class="channel-list">
            ${(n.channels||[]).includes('email')?'<span class="ch ch-email">✉ E-posta</span>':''}
            ${(n.channels||[]).includes('sms')  ?'<span class="ch ch-sms">💬 SMS</span>'   :''}
          </div>
        </div>
        <div class="notif-time">Gönderildi</div>
      </div>`).join('');
  }catch(e){document.getElementById('notif-list').innerHTML=`<div class="empty">Hata: ${e.message}</div>`}
}
load();setInterval(load,8000);
</script>
</body></html>"""


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "notification-service"}


@app.post("/send")
async def send_notification(req: NotificationRequest):
    """Bildirim gönderme - e-posta ve SMS kanallarını simüle eder."""
    with tracer.start_as_current_span("send-notification") as span:
        span.set_attribute("notification.customer_id", req.customer_id)
        span.set_attribute("notification.order_id",    req.order_id)
        span.set_attribute("notification.channels",    "email,sms")

        #E-posta Gönderimi
        with tracer.start_as_current_span("send-email") as email_span:
            await asyncio.sleep(random.uniform(0.05, 0.15))
            email_span.set_attribute("email.provider",   "SimMailer")
            email_span.set_attribute("email.recipient",  f"{req.customer_id}@example.com")
            email_span.set_attribute("email.subject",    f"Sipariş Onayı - {req.order_id}")
            email_span.set_attribute("email.sent",       True)

        #SMS Gönderimi
        with tracer.start_as_current_span("send-sms") as sms_span:
            await asyncio.sleep(random.uniform(0.02, 0.08))
            sms_span.set_attribute("sms.provider",  "SimSMS")
            sms_span.set_attribute("sms.sent",      True)

        notifications_sent.append({
            "customer_id": req.customer_id,
            "order_id":    req.order_id,
            "channels":    ["email", "sms"]
        })

        span.set_attribute("notifications.total", len(notifications_sent))
        span.set_status(Status(StatusCode.OK))

        return {
            "status":      "sent",
            "customer_id": req.customer_id,
            "channels":    ["email", "sms"]
        }


@app.get("/notifications")
async def get_notifications():
    return {"notifications": notifications_sent, "total": len(notifications_sent)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
