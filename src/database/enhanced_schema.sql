CREATE TABLE suppliers (
    supplier_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    contact_info TEXT,
    lead_time INTEGER
);

CREATE TABLE price_history (
    part_id VARCHAR(50),
    price DECIMAL(10,2),
    effective_date TIMESTAMP,
    FOREIGN KEY (part_id) REFERENCES spare_parts(part_id)
);

CREATE TABLE alerts (
    alert_id SERIAL PRIMARY KEY,
    part_id VARCHAR(50),
    alert_type VARCHAR(50),
    created_at TIMESTAMP,
    resolved_at TIMESTAMP
);
