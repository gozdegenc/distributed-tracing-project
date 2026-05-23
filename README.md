# Distributed Tracing Demo Projesi
## Yazılım Mimarisi ve Entegrasyonu

> **Pattern:** Distributed Tracing
> **Senaryo:** E-Ticaret Sipariş Yönetimi Sistemi

---

## Mimari Genel Bakış

```
 [Tarayıcı / JMeter]
        │
        ▼
 ┌──────────────┐   traceparent header
 │  API Gateway │──────────────────────────────────────────────┐
 │   (:8000)    │                                              │
 └──────┬───────┘                                              ▼
        │ HTTP + traceparent                         ┌─────────────────┐
        ▼                                            │     Jaeger      │
 ┌──────────────┐   HTTP + traceparent               │  UI (:16686)    │
 │    Order     │──────────────────────────────────► │  OTLP (:4317)   │
 │   Service    │                                    └─────────────────┘
 │   (:8001)    │   (payment.py bu servis içinde)
 └──────┬───────┘
        │
        ├── HTTP + traceparent ──► ┌──────────────────────┐
        │                          │  Inventory Service    │
        │                          │     (:8002)           │
        │                          └──────────────────────┘
        │
        └── HTTP + traceparent ──► ┌──────────────────────┐
                                   │ Notification Service  │
                                   │     (:8004)           │
                                   └──────────────────────┘

    Prometheus (:9090) — tüm servislerden /metrics toplar
```

**Not:** Ödeme işlemi ayrı bir mikro servis olarak değil, Order Service içindeki
`payment.py` modülünde gerçekleştirilmektedir. Bu sayede ödeme span'leri
(`fraud-check`, `bank-api-call`, `record-transaction`) Order Service'in trace
ağacı içinde görünür.

---

## Sipariş Akışı (İki Adımlı)

Proje, gerçek e-ticaret sistemlerindeki "checkout" akışını simüle etmek için
iki adımlı bir süreç kullanır:

### Adım 1 — Siparişi Başlat (`/orders/initiate`)
1. Gateway: rate-limit ve auth kontrolü
2. Order Service: sipariş doğrulama
3. Inventory Service'e stok sorgusu (`GET /stock`) — rezervasyon **yapılmaz**
4. Sipariş `pending_payment` durumuyla kaydedilir
5. Kullanıcı `/checkout/{order_id}` sayfasına yönlendirilir

### Adım 2 — Ödemeyi Onayla (`/orders/{id}/pay`)
1. Inventory Service'e stok rezervasyonu (`POST /check`) — stok düşülür
2. Fraud kontrolü (rastgele ~%5 ihtimalle bloklanır)
3. Banka API simülasyonu (rastgele ~%10 ihtimalle başarısız olur)
4. İşlem kaydı oluşturulur
5. Notification Service'e bildirim gönderilir (e-posta + SMS)
6. Sipariş `confirmed` durumuna geçer

---

## Jaeger'da Görünen Trace Ağacı

### Adım 1 — initiate
```
gateway-initiate-order                      [api-gateway]   ~20ms
├── rate-limit-check                        [api-gateway]    ~2ms
├── auth-validation                         [api-gateway]    ~5ms
└── (httpx → order-service)
    └── initiate-order                      [order-service] ~15ms
        ├── validate-order                  [order-service]  ~10ms
        └── check-inventory-availability    [order-service]   ~5ms
            └── (GET /stock → inv-service)
```

### Adım 2 — pay
```
gateway-pay-order                           [api-gateway]  ~500ms
└── (httpx → order-service)
    └── confirm-payment-flow                [order-service] ~490ms
        ├── reserve-inventory               [order-service]  ~50ms
        │   └── inventory-check             [inv-service]    ~45ms
        │       ├── db-query-stock          [inv-service]    ~30ms
        │       └── reserve-stock           [inv-service]    ~15ms
        ├── process-payment                 [order-service] ~350ms
        │   ├── fraud-check                 [order-service]  ~50ms
        │   ├── bank-api-call               [order-service] ~280ms  ← en yavaş
        │   └── record-transaction          [order-service]  ~10ms
        └── send-notification               [order-service] ~120ms
            └── send-notification           [notif-service] ~115ms
                ├── send-email              [notif-service]  ~80ms
                └── send-sms                [notif-service]  ~40ms
```

---

## Kurulum ve Çalıştırma

### Ön Koşullar
- Docker Desktop (en az 4 GB RAM)
- Docker Compose v2+

### 1. Tüm Servisleri Başlat

```bash

# Build et ve başlat
docker-compose up --build

# Arka planda çalıştırmak için
docker-compose up --build -d
```

### 2. Servislerin Hazır Olduğunu Kontrol Et

```bash
curl http://localhost:8000/health   # API Gateway
curl http://localhost:8001/health   # Order Service
curl http://localhost:8002/health   # Inventory Service
curl http://localhost:8004/health   # Notification Service
```

### 3. Dashboard

Tarayıcıda **http://localhost:8000** adresini aç.
Sipariş formu, stok durumu, servis sağlık göstergesi ve
Jaeger/Prometheus linkleri bu sayfada yer alır.

### 4. API ile Manuel Test

```bash
# Adım 1: Sipariş oluştur
curl -X POST http://localhost:8000/api/v1/orders/initiate \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-001",
    "product_id": "PROD-001",
    "quantity": 1,
    "total_price": 14999.00
  }'
```

Response:
```json
{
  "order_id": "ORD-0001",
  "status": "pending_payment",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

```bash
# Adım 2: Ödemeyi onayla
curl -X POST http://localhost:8000/api/v1/orders/ORD-0001/pay
```

Response:
```json
{
  "order_id": "ORD-0001",
  "status": "confirmed",
  "message": "Sipariş ve ödeme başarıyla tamamlandı",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

### 5. Jaeger UI'da Trace'i Görüntüle

**http://localhost:16686** → Service: `api-gateway` → Find Traces

`trace_id` değerini Jaeger'ın arama kutusuna yapıştırarak o siparişin
tam yolculuğunu izleyebilirsin.

---

## Tracing Overhead Ölçümü

```bash
curl http://localhost:8000/api/v1/trace-overhead
```

Bu endpoint aynı iş yükünü tracing açık ve kapalı olarak çalıştırıp
farkı hesaplar. Örnek çıktı:

```json
{
  "benchmark": {
    "iterations": 100,
    "with_tracing_ms": 12.4,
    "without_tracing_ms": 10.1,
    "overhead_per_span_us": 23.0,
    "overhead_percent": 2.3
  },
  "verdict": "Tracing overhead kabul edilebilir düzeyde (<5%)"
}
```

## Servisler ve Portlar

| Servis | Port | Açıklama |
|--------|------|----------|
| API Gateway | 8000 | Ana giriş noktası, HTML dashboard |
| Order Service | 8001 | Sipariş koordinatörü + ödeme modülü |
| Inventory Service | 8002 | Stok yönetimi (in-memory) |
| Notification Service | 8004 | E-posta/SMS simülasyonu |
| Jaeger UI | 16686 | Trace görselleştirme |
| Prometheus | 9090 | Metrik toplama |

---

## Ölçülen Metrikler

| Metrik | Açıklama |
|--------|----------|
| **Tracing Overhead** | `/api/v1/trace-overhead` ile ölçülür (~%1-5) |
| **Span Count/Request** | Her tam sipariş akışında ~10-14 span |
| **Context Propagation** | W3C `traceparent` header ile servisler arası taşınır |
| **Hata Tespiti** | Fraud/banka hatalarında Jaeger'da kırmızı span görünür |
| **Servis Bağımlılık Haritası** | Jaeger → System Architecture görünümü |

---

## Proje Yapısı

```
distributed-tracing-project/
├── docker-compose.yml
├── api-gateway/
│   ├── main.py          # Gateway, rate-limit, HTML dashboard, checkout sayfaları
│   ├── requirements.txt
│   └── Dockerfile
├── order-service/
│   ├── main.py          # Sipariş akışı koordinatörü
│   ├── payment.py       # Fraud kontrolü, banka API, işlem kaydı
│   ├── tracing.py       # OpenTelemetry setup yardımcısı
│   ├── requirements.txt
│   └── Dockerfile
├── inventory-service/
│   ├── main.py          # Stok kontrolü ve rezervasyonu (in-memory)
│   ├── requirements.txt
│   └── Dockerfile
├── notification-service/
│   ├── main.py          # E-posta/SMS simülasyonu
│   ├── requirements.txt
│   └── Dockerfile
├── jmeter-tests/
│   └── distributed-tracing-test-plan.jmx
└── monitoring/
    └── prometheus.yml
```
