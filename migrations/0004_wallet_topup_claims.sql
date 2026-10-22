CREATE TABLE WalletTopupClaims(
  user_id INTEGER NOT NULL,
  claim_day TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(user_id, claim_day),
  FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE
);
