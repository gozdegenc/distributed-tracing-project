import asyncio
import random
import uuid

from fastapi import HTTPException
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

transactions: list[dict] = []


async def process_payment(order_id: str, customer_id: str, amount: float) -> dict:
    tracer = trace.get_tracer("order-service")
    with tracer.start_as_current_span("fraud-check") as fraud_span:
        await asyncio.sleep(random.uniform(0.02, 0.08))
        fraud_score = random.uniform(0, 1)
        is_fraudulent = fraud_score > 0.95
        fraud_span.set_attribute("fraud.score",     round(fraud_score, 3))
        fraud_span.set_attribute("fraud.flagged",   is_fraudulent)
        fraud_span.set_attribute("fraud.threshold", 0.95)
        if is_fraudulent:
            fraud_span.set_status(Status(StatusCode.ERROR, "Fraud detected"))
            transactions.append({
                "transaction_id": "BLOCKED",
                "order_id":       order_id,
                "customer_id":    customer_id,
                "amount":         amount,
                "status":         "fraud_blocked",
            })
            raise HTTPException(status_code=402, detail="Ödeme sahtekarlık tespiti nedeniyle engellendi")

    with tracer.start_as_current_span("bank-api-call") as bank_span:
        bank_latency = random.uniform(0.1, 0.4)
        await asyncio.sleep(bank_latency)
        bank_failure = random.random() < 0.10
        bank_span.set_attribute("bank.latency_ms",       round(bank_latency * 1000))
        bank_span.set_attribute("bank.gateway",          "SimBank API")
        bank_span.set_attribute("bank.failure_injected", bank_failure)
        if bank_failure:
            bank_span.set_status(Status(StatusCode.ERROR, "Bank API error"))
            transactions.append({
                "transaction_id": "FAILED",
                "order_id":       order_id,
                "customer_id":    customer_id,
                "amount":         amount,
                "status":         "bank_error",
            })
            raise HTTPException(status_code=402, detail="Banka API hatası - lütfen tekrar deneyin")

    with tracer.start_as_current_span("record-transaction") as rec_span:
        await asyncio.sleep(random.uniform(0.005, 0.015))
        transaction_id = str(uuid.uuid4())[:8].upper()
        transactions.append({
            "transaction_id": transaction_id,
            "order_id":       order_id,
            "customer_id":    customer_id,
            "amount":         amount,
            "status":         "success",
        })
        rec_span.set_attribute("transaction.id",     transaction_id)
        rec_span.set_attribute("transaction.saved",  True)
        rec_span.set_attribute("transactions.total", len(transactions))

    return {"transaction_id": transaction_id, "status": "success"}
