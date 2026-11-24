from datetime import datetime, timedelta
import pytest

import main
from orders import (
    build_order_report,
    cancel_order,
    change_order_status,
    requires_manual_refund,
    validate_order_cancellation,
)


def make_order(
    order_id="ORD-0100",
    status="NEW",
    payment_method="CARD",
    created_at="2026-11-20T10:00:00",
):
    """Pomocnicza funkcja (fabryka) tworząca przykładowe zamówienie do testów."""
    return {
        "id": order_id,
        "customer_id": "CUS-001",
        "created_at": created_at,
        "status": status,
        "payment_method": payment_method,
        "delivery_method": "COURIER",
        "items": [
            {
                "product_id": "PRD-001",
                "product_name": "Klawiatura",
                "quantity": 1,
                "unit_price": 329.90,
            }
        ],
        "total": 329.90,
    }


def test_cancel_new_order_allowed_regardless_of_age():
    """AC2 + AC7: Zamówienie NEW można anulować nawet po wielu dniach bez utraty danych."""
    order = make_order(status="NEW", created_at="2026-11-01T08:00:00")
    original_keys = set(order.keys())
    now = datetime(2026, 11, 20, 12, 0, 0)

    result = cancel_order(order, current_time=now)

    assert order["status"] == "CANCELLED"
    assert set(order.keys()) == original_keys
    assert order["customer_id"] == "CUS-001"
    assert order["total"] == 329.90
    assert result["previous_status"] == "NEW"
    assert result["requires_manual_refund"] is False


def test_cancel_paid_order_at_exact_30_minute_boundary():
    """AC3 (graniczny): PAID dokładnie po 30:00 można jeszcze anulować."""
    order = make_order(status="PAID", created_at="2026-11-20T10:00:00")
    now = datetime(2026, 11, 20, 10, 30, 0)

    result = cancel_order(order, current_time=now)

    assert order["status"] == "CANCELLED"
    assert result["requires_manual_refund"] is True


def test_cancel_paid_order_rejected_after_30_minutes_and_1_second():
    """AC3 (graniczny/negatywny): PAID po 30:01 zostaje odrzucone bez zmian."""
    order = make_order(status="PAID", created_at="2026-11-20T10:00:00")
    now = datetime(2026, 11, 20, 10, 30, 1)

    with pytest.raises(ValueError, match="30 min"):
        cancel_order(order, current_time=now)

    assert order["status"] == "PAID"


def test_cancel_processing_order_at_exact_15_minute_boundary():
    """AC4 (graniczny): PROCESSING dokładnie po 15:00 można jeszcze anulować."""
    order = make_order(status="PROCESSING", created_at="2026-11-20T10:00:00")
    now = datetime(2026, 11, 20, 10, 15, 0)

    result = cancel_order(order, current_time=now)

    assert order["status"] == "CANCELLED"
    assert result["previous_status"] == "PROCESSING"


def test_cancel_processing_order_rejected_after_15_minutes_and_1_second():
    """AC4 (graniczny/negatywny): PROCESSING po 15:01 zostaje odrzucone bez zmian."""
    order = make_order(status="PROCESSING", created_at="2026-11-20T10:00:00")
    now = datetime(2026, 11, 20, 10, 15, 1)

    with pytest.raises(ValueError, match="15 min"):
        cancel_order(order, current_time=now)

    assert order["status"] == "PROCESSING"


@pytest.mark.parametrize("status", ["SHIPPED", "DELIVERED", "CANCELLED"])
def test_cancel_disallowed_statuses_rejected_without_mutation(status):
    """AC5: Niedozwolone statusy rzucają ValueError i nie zmieniają danych."""
    order = make_order(status=status)

    with pytest.raises(ValueError):
        cancel_order(order, current_time=datetime(2026, 11, 20, 10, 5, 0))

    assert order["status"] == status


@pytest.mark.parametrize(
    "status,payment_method,expected",
    [
        ("PAID", "CARD", True),
        ("PAID", "TRANSFER", True),
        ("PAID", "CASH_ON_DELIVERY", False),
        ("PROCESSING", "CARD", True),
        ("PROCESSING", "TRANSFER", True),
        ("PROCESSING", "CASH_ON_DELIVERY", False),
        ("NEW", "CARD", False),
        ("NEW", "TRANSFER", False),
        ("NEW", "CASH_ON_DELIVERY", False),
    ],
)
def test_requires_manual_refund_rules(status, payment_method, expected):
    """AC8: Weryfikacja wszystkich kombinacji statusu i metody płatności."""
    assert requires_manual_refund(status, payment_method) is expected


def test_validate_order_cancellation_invalid_created_at():
    """Obsługa uszkodzonej daty created_at w rekordzie."""
    order = make_order(status="PAID", created_at="niepoprawna-data")

    with pytest.raises(ValueError, match="datę utworzenia"):
        validate_order_cancellation(order)


def test_cli_cancellation_aborted_when_not_confirmed(monkeypatch, capsys):
    """AC6: Brak potwierdzenia (N) w CLI przerywa operację i nie zapisuje zmian."""
    orders = [make_order(order_id="ORD-0100", status="NEW")]
    saved = {"called": False}

    inputs = iter(["ORD-0100", "N"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(main, "load_orders", lambda: orders)
    monkeypatch.setattr(main, "save_orders", lambda data: saved.__setitem__("called", True))

    main.handle_order_cancellation()
    output = capsys.readouterr().out

    assert orders[0]["status"] == "NEW"
    assert saved["called"] is False
    assert "przerwane" in output


def test_cli_cancellation_invalid_id_and_missing_order(monkeypatch, capsys):
    """AC1: Błędny format ID oraz nieistniejące zamówienie w CLI."""
    monkeypatch.setattr("builtins.input", lambda _: "123")
    main.handle_order_cancellation()
    assert "nieprawidłowy format" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda _: "ORD-9999")
    monkeypatch.setattr(main, "load_orders", lambda: [])
    main.handle_order_cancellation()
    assert "Nie znaleziono zamówienia" in capsys.readouterr().out


def test_regression_report_and_status_change_after_cancellation():
    """AC9: Anulowane zamówienie jest widoczne w raporcie i zablokowane w change_order_status."""
    order = make_order(order_id="ORD-0100", status="NEW")
    cancel_order(order)

    report = build_order_report([order])
    assert report["status_counts"]["CANCELLED"] == 1

    with pytest.raises(ValueError):
        change_order_status(order, "PAID")
