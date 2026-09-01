-- Run this in Supabase SQL editor (Project -> SQL Editor -> New query) before starting the app.

create table if not exists products (
  sku text primary key,
  name text not null,
  category text not null,
  attribute text,
  price numeric not null,
  stock int not null default 0,
  delivery_days int not null default 2,
  brand text,
  product_type text
);

create table if not exists orders (
  id uuid primary key default gen_random_uuid(),
  session_id text not null,
  gate_token text not null,
  sku text not null,
  quantity int not null,
  amount numeric not null,
  razorpay_order_id text,
  status text not null default 'created',
  created_at timestamptz default now()
);

create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  session_id text not null,
  step text not null,
  payload jsonb,
  created_at timestamptz default now()
);

-- Durable gate tokens: survives a backend restart mid-flow, unlike an
-- in-memory dict. Also holds the OTP challenge for high-value orders.
create table if not exists gate_tokens (
  token text primary key,
  sku text not null,
  quantity int not null,
  amount numeric not null,
  requires_otp boolean not null default false,
  otp_code text,
  otp_verified boolean not null default false,
  expires_at timestamptz,
  created_at timestamptz default now()
);

create index if not exists idx_audit_session on audit_log(session_id);
create index if not exists idx_orders_session on orders(session_id);

-- Seed data representing the electronics catalog
insert into products (sku, name, category, attribute, price, stock, delivery_days, brand, product_type) values
  ('SKU-001-07', 'Apple Laptops Series 7', 'Computers & Accessories', '16-inch | 16GB RAM | 1TB SSD', 46811, 76, 2, 'Apple', 'Laptops'),
  ('SKU-001-08', 'Samsung Laptops Series 8', 'Computers & Accessories', '15.6-inch | 32GB RAM | 1TB SSD', 47045, 96, 4, 'Samsung', 'Laptops'),
  ('SKU-001-09', 'Microsoft Laptops Series 9', 'Computers & Accessories', '14-inch | 16GB RAM | 2TB SSD', 47279, 25, 6, 'Microsoft', 'Laptops'),
  ('SKU-001-10', 'LG Laptops Series 10', 'Computers & Accessories', '16-inch | 32GB RAM | 2TB SSD', 47513, 45, 1, 'LG', 'Laptops'),
  ('SKU-002-01', 'HP Desktop Computers Series 1', 'Computers & Accessories', 'Variant 1 | Premium Quality', 35580, 64, 5, 'HP', 'Desktops'),
  ('SKU-002-02', 'Dell Desktop Computers Series 2', 'Computers & Accessories', 'Variant 2 | Premium Quality', 35814, 84, 7, 'Dell', 'Desktops')
on conflict (sku) do nothing;
