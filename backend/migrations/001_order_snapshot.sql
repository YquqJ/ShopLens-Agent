-- Apply to an existing PostgreSQL database before deploying the real-source order flow.
-- Fresh databases receive the same columns from SQLAlchemy metadata.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_intent_id VARCHAR(64);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_number VARCHAR(32);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS title_snapshot VARCHAR(200) NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS source_url VARCHAR(512);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS image_url_snapshot VARCHAR(512);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS catalog_price NUMERIC(10, 2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_fee NUMERIC(10, 2);

UPDATE orders
SET order_intent_id = 'legacy-' || id::text
WHERE order_intent_id IS NULL;

UPDATE orders
SET order_number = 'LEGACY-' || id::text
WHERE order_number IS NULL;

ALTER TABLE orders ALTER COLUMN order_intent_id SET NOT NULL;
ALTER TABLE orders ALTER COLUMN order_number SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_order_intent_id ON orders(order_intent_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_order_number ON orders(order_number);
