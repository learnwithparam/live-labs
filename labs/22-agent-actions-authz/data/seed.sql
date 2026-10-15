-- The same schema in dev and prod; prod holds the March 2026 pay run for three customers.
CREATE TABLE employees (id INTEGER PRIMARY KEY, employer TEXT NOT NULL, name TEXT NOT NULL, annual_pence INTEGER NOT NULL);
CREATE TABLE payslips (id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id), month TEXT NOT NULL, gross_pence INTEGER NOT NULL, net_pence INTEGER NOT NULL);
CREATE TABLE audit_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL);
CREATE TABLE legacy_rates (band TEXT PRIMARY KEY, rate REAL NOT NULL, upper_pence INTEGER);
CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT INTO schema_migrations VALUES ('0006_add_audit_events', '2026-08-02T09:14:00Z');
INSERT INTO employees VALUES
 (1, 'Hollow Oak Bakery', 'Maya Okafor', 2450000),
 (2, 'Hollow Oak Bakery', 'Tom Reilly', 2800012),
 (3, 'Hollow Oak Bakery', 'Sian Hughes', 3100000),
 (4, 'Brightline Dental', 'Dr Amir Shah', 9200000),
 (5, 'Brightline Dental', 'Leah Brennan', 3400000),
 (6, 'Kettle & Crane Cafe', 'Jonah Pike', 2210000),
 (7, 'Kettle & Crane Cafe', 'Ruth Adeyemi', 2650000);
INSERT INTO payslips VALUES
 (1, 1, '2026-09', 204166, 175641),
 (2, 2, '2026-09', 233334, 197330),
 (3, 3, '2026-09', 258333, 215763),
 (4, 4, '2026-09', 766666, 529813),
 (5, 5, '2026-09', 283333, 234363),
 (6, 6, '2026-09', 184166, 161441),
 (7, 7, '2026-09', 220833, 189441);
INSERT INTO audit_events VALUES
 (1, '2026-09-28T17:02:11Z', 'marcus.lee', 'pay run 2026-09 approved for Hollow Oak Bakery'),
 (2, '2026-09-28T17:05:40Z', 'marcus.lee', 'pay run 2026-09 approved for Brightline Dental'),
 (3, '2026-09-29T08:41:03Z', 'priya.shah', 'payslip 2 reissued after bank detail change'),
 (4, '2026-09-30T16:20:57Z', 'marcus.lee', 'RTI submission sent to HMRC for 2026-09');
INSERT INTO legacy_rates VALUES ('basic', 0.20, 5027000), ('higher', 0.40, 12514000), ('additional', 0.45, NULL);
