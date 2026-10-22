# 03 – Data Model

> Mọi bảng có `created_at`, `updated_at` (timestamptz). Dùng Alembic migration.

## Cấu hình & nguồn
```sql
CREATE TABLE connections (
  id SERIAL PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'gitlab',
  base_url TEXT NOT NULL, token_encrypted TEXT NOT NULL, webhook_secret_encrypted TEXT
);
CREATE TABLE projects (
  id SERIAL PRIMARY KEY, connection_id INT REFERENCES connections(id),
  external_id BIGINT NOT NULL, path_with_namespace TEXT NOT NULL, default_branch TEXT,
  review_enabled BOOLEAN DEFAULT true, post_mr_comment BOOLEAN DEFAULT false,
  allow_external_llm BOOLEAN DEFAULT true, last_synced_at TIMESTAMPTZ,
  UNIQUE (connection_id, external_id)
);
```

## Raw layer
```sql
CREATE TABLE raw_gitlab (
  id BIGSERIAL PRIMARY KEY, project_id INT REFERENCES projects(id),
  endpoint TEXT NOT NULL, params JSONB, payload JSONB NOT NULL, fetched_at TIMESTAMPTZ
);
```

## Identity (xem 05)
```sql
CREATE TABLE teams (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, parent_id INT REFERENCES teams(id));
CREATE TABLE users (id SERIAL PRIMARY KEY, full_name TEXT, primary_email TEXT UNIQUE, active BOOLEAN DEFAULT true);
CREATE TABLE team_members (team_id INT REFERENCES teams(id), user_id INT REFERENCES users(id),
  role TEXT, joined_at DATE, left_at DATE, PRIMARY KEY (team_id, user_id, joined_at));
CREATE TABLE accounts (  -- một người có nhiều account/email
  id SERIAL PRIMARY KEY, kind TEXT NOT NULL,       -- 'gitlab_user' | 'git_email'
  external_key TEXT NOT NULL, display_name TEXT, user_id INT REFERENCES users(id),
  UNIQUE (kind, external_key)
);
```

## Domain layer
```sql
CREATE TABLE commits (
  sha TEXT PRIMARY KEY, project_id INT REFERENCES projects(id),
  author_account_id INT REFERENCES accounts(id), authored_at TIMESTAMPTZ,
  message TEXT, additions INT, deletions INT, files_changed INT, is_merge BOOLEAN
);
CREATE TABLE merge_requests (
  id BIGSERIAL PRIMARY KEY, project_id INT REFERENCES projects(id), iid INT NOT NULL,
  title TEXT, author_account_id INT REFERENCES accounts(id), state TEXT,
  source_branch TEXT, target_branch TEXT, created_at_src TIMESTAMPTZ,
  first_review_at TIMESTAMPTZ, merged_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
  additions INT, deletions INT, review_rounds INT, head_sha TEXT,
  UNIQUE (project_id, iid)
);
CREATE TABLE mr_commits (mr_id BIGINT REFERENCES merge_requests(id), sha TEXT REFERENCES commits(sha), PRIMARY KEY (mr_id, sha));
CREATE TABLE mr_notes (
  id BIGINT PRIMARY KEY, mr_id BIGINT REFERENCES merge_requests(id),
  author_account_id INT REFERENCES accounts(id), body TEXT, is_system BOOLEAN, created_at_src TIMESTAMPTZ
);
CREATE TABLE pipelines (
  id BIGINT PRIMARY KEY, project_id INT REFERENCES projects(id), sha TEXT, ref TEXT,
  status TEXT, duration_sec INT, created_at_src TIMESTAMPTZ, finished_at_src TIMESTAMPTZ
);
```

## Review
```sql
CREATE TABLE reviews (
  id BIGSERIAL PRIMARY KEY,
  target_type TEXT NOT NULL CHECK (target_type IN ('mr','commit')),
  mr_id BIGINT REFERENCES merge_requests(id), commit_sha TEXT REFERENCES commits(sha),
  reviewed_sha TEXT NOT NULL,              -- head sha tại thời điểm review
  author_user_id INT REFERENCES users(id), -- người chịu trách nhiệm chính
  status TEXT NOT NULL,                    -- queued|running|done|failed|skipped|superseded
  score NUMERIC(5,2), loc_reviewed INT, summary TEXT,
  model TEXT, rubric_version TEXT, prompt_tokens INT, completion_tokens INT,
  cost_usd NUMERIC(10,4), error TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  UNIQUE (target_type, mr_id, commit_sha, reviewed_sha, rubric_version)
);
CREATE TABLE review_findings (
  id BIGSERIAL PRIMARY KEY, review_id BIGINT REFERENCES reviews(id) ON DELETE CASCADE,
  source TEXT NOT NULL DEFAULT 'ai',       -- ai|semgrep|sonar
  severity TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low','info')),
  category TEXT NOT NULL,                  -- security|bug|performance|maintainability|testing|style|docs
  rule_id TEXT, file_path TEXT, line_start INT, line_end INT,
  title TEXT, message TEXT, suggestion TEXT, code_excerpt TEXT, confidence NUMERIC(3,2),
  fingerprint TEXT,                        -- hash(file, rule/category, normalized code) để dedupe
  status TEXT DEFAULT 'open'               -- open|fixed|disputed|accepted_risk|false_positive
);
CREATE TABLE finding_disputes (
  id BIGSERIAL PRIMARY KEY, finding_id BIGINT REFERENCES review_findings(id),
  raised_by INT REFERENCES users(id), reason TEXT,
  decision TEXT, decided_by INT REFERENCES users(id), decided_at TIMESTAMPTZ
);
```

## Metrics (tổng hợp – xem 08)
```sql
CREATE TABLE metrics_user_period (
  user_id INT REFERENCES users(id), period_type TEXT CHECK (period_type IN ('week','month')),
  period_start DATE, reviews_count INT, avg_score NUMERIC(5,2), loc_changed INT,
  critical INT, high INT, medium INT, low INT, info INT,
  weighted_penalty NUMERIC(10,2), defect_density NUMERIC(8,3),
  mr_count INT, avg_review_time_h NUMERIC(8,2), avg_mr_size INT, avg_review_rounds NUMERIC(5,2),
  build_success_rate NUMERIC(5,2), quality_index NUMERIC(5,2),
  PRIMARY KEY (user_id, period_type, period_start)
);
CREATE TABLE metrics_team_period (LIKE metrics_user_period INCLUDING ALL);
-- metrics_team_period: thay user_id bằng team_id (agent sửa DDL khi implement)
```

## Checklist
- [ ] Migration khởi tạo toàn bộ bảng
- [ ] Index: `commits(project_id, authored_at)`, `merge_requests(project_id, merged_at)`, `review_findings(review_id, severity)`, `reviews(author_user_id, finished_at)`
- [ ] Mã hoá token bằng Fernet (`APP_ENCRYPTION_KEY`)
- [ ] Seed dữ liệu mẫu cho dev (`make seed`)
