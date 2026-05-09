import pytest
from target_code import *

def test__to_int_normal_and_edge_cases():
    assert _to_int("7") == 7
    assert _to_int(12) == 12
    assert _to_int("bad", 5) == 5
    assert _to_int(None, 9) == 9
from datetime import datetime
def test__to_price_normal_and_edge_cases():
    assert _to_price("12.5") == 12.5
    assert _to_price(3, 1.0) == 3.0
    assert _to_price(-4, 8.5) == 8.5
    assert _to_price("bad", 4.25) == 4.25
from datetime import datetime
def test__to_text_and_utc_now_behaviour():
    assert _to_text("  hello  ", "default") == "hello"
    assert _to_text("   ", "fallback") == "fallback"
    value = _utc_now()
    assert isinstance(value, str)
    assert value.endswith('+00:00') or value.endswith('Z') or '+' in value
from datetime import datetime
def test_reset_initialize_and_customer_setup():
    reset_demo_state()
    reset_result = reset_demo_state()
    assert reset_result['customers'] == 0
    assert reset_result['products'] == 0
    assert reset_result['orders'] == 0
    init_result = initialize_store()
    assert init_result['products'] == 3
    assert init_result['product_ids'] == [1, 2, 3]
    customer = register_customer(0, 'Alice')
    assert customer['customer_id'] == 1
    assert customer['name'] == 'Alice'
from datetime import datetime
def test_inventory_and_cart_flow():
    reset_demo_state()
    initialize_store()
    seeded = initialize_store([{'product_id': 9, 'name': 'Seeded', 'price': 7.5, 'stock': 4}])
    assert seeded['seeded'] is True
    assert 9 in seeded['product_ids']
    inventory = upsert_inventory(1, 2)
    assert inventory['product_id'] == 1
    assert inventory['stock'] == 52
    cart = add_to_cart(1, 1, 2)
    assert cart['accepted_quantity'] == 2
    assert cart['cart_size'] == 2
from datetime import datetime
def test_order_lifecycle_and_reports():
    reset_demo_state()
    initialize_store()
    register_customer(1, 'Alice')
    add_to_cart(1, 1, 2)
    order = create_order(1, 5)
    assert order['status'] == 'confirmed'
    assert order['subtotal'] == 198.0
    assert order['shipping_fee'] == 5.0
    assert order['total'] == 203.0
    assert calculate_order_total(order['order_id']) == 203.0
    cancelled = cancel_order(order['order_id'], 'changed mind')
    assert cancelled['status'] == 'cancelled'
    assert cancelled['cancel_reason'] == 'changed mind'
    history = get_customer_history(1)
    assert history[0]['order_id'] == order['order_id']
    report = generate_sales_report(1)
    assert report['order_count'] == 1
    assert report['confirmed_count'] == 0
    assert report['cancelled_count'] == 1
    assert report['gross_revenue'] == 0.0
from datetime import datetime
def test_failure_case_invalid_timestamp_parse():
    value = _utc_now()
    assert isinstance(value, str)
    with pytest.raises(ValueError):
        datetime.fromisoformat('not-a-timestamp')
