-- Preserve category at confirmation time so natural-language order queries can filter it.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS category_snapshot VARCHAR(64);
