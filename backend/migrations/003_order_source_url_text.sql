-- Product redirect URLs may contain long tracking parameters.
ALTER TABLE orders ALTER COLUMN source_url TYPE TEXT;
