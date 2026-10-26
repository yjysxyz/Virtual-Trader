ALTER TABLE Users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE Users ADD COLUMN last_seen_at TEXT;
ALTER TABLE RechargeOrders ADD COLUMN cash_amount REAL NOT NULL DEFAULT 0;

CREATE TABLE StockSwaps(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_user_id INTEGER NOT NULL,
  to_user_id INTEGER NOT NULL,
  from_code TEXT NOT NULL,
  from_quantity INTEGER NOT NULL,
  to_code TEXT NOT NULL,
  to_quantity INTEGER NOT NULL,
  message TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  responded_at TEXT,
  FOREIGN KEY(from_user_id) REFERENCES Users(id) ON DELETE CASCADE,
  FOREIGN KEY(to_user_id) REFERENCES Users(id) ON DELETE CASCADE,
  FOREIGN KEY(from_code) REFERENCES Stocks(code),
  FOREIGN KEY(to_code) REFERENCES Stocks(code)
);

CREATE INDEX idx_stock_swaps_status ON StockSwaps(status, id);
CREATE INDEX idx_stock_swaps_from ON StockSwaps(from_user_id, id);
CREATE INDEX idx_stock_swaps_to ON StockSwaps(to_user_id, id);
