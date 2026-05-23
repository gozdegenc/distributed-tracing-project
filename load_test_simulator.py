import asyncio
import httpx
import random
import time
import argparse
from datetime import datetime


BASE_URL = "http://localhost:8000"

#Ürün kataloğu
PRODUCTS = [
    ("PROD-001", 14999.00),
    ("PROD-002",   299.00),
    ("PROD-003",   799.00),
    ("PROD-005",   599.00),
]

stats = {
    "total": 0,
    "success": 0,
    "error": 0,
    "latencies": []
}


async def create_order(client: httpx.AsyncClient, user_id: int):
    product_id, unit_price = random.choice(PRODUCTS)
    quantity = random.randint(1, 3)
    total_price = round(unit_price * quantity, 2)

    payload = {
        "customer_id": f"CUST-{user_id:04d}",
        "product_id": product_id,
        "quantity": quantity,
        "total_price": total_price,
    }
    start = time.time()
    try:
        #Adım 1: Siparişi başlat
        resp = await client.post(
            f"{BASE_URL}/api/v1/orders/initiate", json=payload, timeout=10.0
        )
        if resp.status_code != 200:
            latency = (time.time() - start) * 1000
            stats["total"] += 1
            stats["error"] += 1
            stats["latencies"].append(latency)
            print(f"❌ [{user_id:03d}] initiate HTTP {resp.status_code} | {latency:.0f}ms")
            return

        order_id = resp.json()["order_id"]

        #Adım 2: Ödemeyi onayla
        resp2 = await client.post(
            f"{BASE_URL}/api/v1/orders/{order_id}/pay", timeout=15.0
        )
        latency = (time.time() - start) * 1000
        stats["total"] += 1
        stats["latencies"].append(latency)

        if resp2.status_code == 200:
            stats["success"] += 1
            data = resp2.json()
            print(f"✅ [{user_id:03d}] {data['order_id']} | {latency:.0f}ms | trace:{data['trace_id'][:8]}...")
        else:
            stats["error"] += 1
            detail = resp2.json().get("detail", "")
            print(f"❌ [{user_id:03d}] pay HTTP {resp2.status_code} {detail} | {latency:.0f}ms")
    except Exception as e:
        stats["error"] += 1
        stats["total"] += 1
        print(f"💥 [{user_id:03d}] Error: {e}")


async def run_user(user_id: int, duration: int):
    async with httpx.AsyncClient() as client:
        end_time = time.time() + duration
        while time.time() < end_time:
            await create_order(client, user_id)
            await asyncio.sleep(random.uniform(0.5, 2.0))  # think time


async def main(users: int, duration: int):
    print(f"\n🚀 Yük Testi Başlıyor")
    print(f"   Kullanıcı Sayısı: {users}")
    print(f"   Süre: {duration} saniye")
    print(f"   Başlangıç: {datetime.now().strftime('%H:%M:%S')}")
    print("─" * 60)

    start = time.time()
    tasks = [run_user(i, duration) for i in range(1, users + 1)]
    await asyncio.gather(*tasks)

    #Sonuçlar
    elapsed = time.time() - start
    latencies = sorted(stats["latencies"])
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    print("\n" + "═" * 60)
    print("📊 TEST SONUÇLARI")
    print("═" * 60)
    print(f"  Toplam İstek  : {stats['total']}")
    print(f"  Başarılı      : {stats['success']} ({stats['success']/max(stats['total'],1)*100:.1f}%)")
    print(f"  Hatalı        : {stats['error']} ({stats['error']/max(stats['total'],1)*100:.1f}%)")
    print(f"  Throughput    : {stats['total']/elapsed:.1f} req/s")
    print(f"  Latency p50   : {p50:.0f}ms")
    print(f"  Latency p95   : {p95:.0f}ms")
    print(f"  Toplam Süre   : {elapsed:.1f}s")
    print("═" * 60)
    print(f"\n💡 Jaeger UI'da trace'leri görmek için: http://localhost:16686")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Distributed Tracing Yük Testi")
    parser.add_argument("--users",    type=int, default=10,  help="Eşzamanlı kullanıcı sayısı")
    parser.add_argument("--duration", type=int, default=30,  help="Test süresi (saniye)")
    args = parser.parse_args()
    asyncio.run(main(args.users, args.duration))
