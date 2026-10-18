-- Duplicate identical dimension records violate declared uniqueness, even if categories agree.
INSERT INTO raw_products SELECT * FROM raw_products WHERE product_id = 10
