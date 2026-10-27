"""Change signals (docs/specs/19 §4.1): deterministic, from paths and added lines only."""

from app.review.prepare import parse_diff
from app.review.signals import detect_signals


def diff(path: str, *added: str, removed: str = "") -> list:  # type: ignore[type-arg]
    body = "".join(f"+{line}\n" for line in added)
    if removed:
        body += f"-{removed}\n"
    return [parse_diff(path, f"@@ -1,1 +1,{len(added) + 1} @@\n import x\n{body}")]


def test_paths_name_migrations_auth_money_uploads_config_jobs_and_events() -> None:
    assert detect_signals(diff("migrations/versions/001_x.py", "pass")) == {"migration"}
    assert detect_signals(diff("db/migrate/V12__add.sql", "pass")) == {"migration"}
    assert detect_signals(diff("app/auth/guard.py", "pass")) == {"auth"}
    assert detect_signals(diff("src/billing/invoice.ts", "pass")) == {"money"}
    assert detect_signals(diff("app/upload_service.py", "pass")) == {"file_upload"}
    assert detect_signals(diff(".env.production", "pass")) == {"config"}
    assert detect_signals(diff("config/database.yml", "pass")) == {"config"}
    assert detect_signals(diff("app/workers/cleanup_job.py", "pass")) == {"job"}
    assert detect_signals(diff("src/events/order_consumer.go", "pass")) == {"event"}
    assert detect_signals(diff("app/models/user.py", "name = 1")) == set()


def test_added_lines_name_endpoints_queries_calls_and_more() -> None:
    assert "endpoint" in detect_signals(diff("a.py", '@router.get("/x")'))
    assert "endpoint" in detect_signals(diff("a.py", '@app.route("/x", methods=["POST"])'))
    assert "endpoint" in detect_signals(diff("A.java", '@GetMapping("/x")'))
    assert "endpoint" in detect_signals(diff("a.go", 'http.HandleFunc("/x", h)'))
    assert "endpoint" in detect_signals(diff("a.ts", "router.post('/x', handler)"))
    assert "db_query" in detect_signals(diff("a.py", "rows = session.query(User).all()"))
    assert "db_query" in detect_signals(diff("a.py", 'cur.execute("SELECT id FROM t")'))
    assert "db_query" in detect_signals(diff("a.ts", "await prisma.user.findMany()"))
    assert "db_query" in detect_signals(diff("a.js", "const u = await User.find({id})"))
    assert "outbound_call" in detect_signals(diff("a.py", "r = requests.get(url)"))
    assert "outbound_call" in detect_signals(diff("a.py", "async with httpx.AsyncClient() as c:"))
    assert "outbound_call" in detect_signals(diff("a.ts", "const r = await fetch(url)"))
    assert "outbound_call" in detect_signals(diff("a.ts", "import axios from 'axios'"))
    assert "auth" in detect_signals(diff("a.py", "if not user.has_permission('x'):"))
    assert "auth" in detect_signals(diff("a.py", "token = request.headers['Authorization']"))
    assert "money" in detect_signals(diff("a.py", "total = amount * qty"))
    assert "money" in detect_signals(diff("a.py", "wallet.balance -= price"))
    assert "file_upload" in detect_signals(diff("a.py", "file: UploadFile = File(...)"))
    assert "file_upload" in detect_signals(diff("a.ts", "const form = new FormData()"))
    assert "config" in detect_signals(diff("a.py", "key = os.environ['X']"))
    assert "config" in detect_signals(diff("a.ts", "const k = process.env.KEY"))
    assert "job" in detect_signals(diff("a.py", "queue.enqueue(run, 1)"))
    assert "job" in detect_signals(diff("A.java", '@Scheduled(cron = "0 * * * * *")'))
    assert "event" in detect_signals(diff("a.py", "producer.publish('orders', payload)"))
    assert "event" in detect_signals(diff("a.ts", "emitter.emit('created', e)"))
    assert "migration" in detect_signals(diff("a.py", "op.add_column('t', sa.Column('x'))"))
    assert "migration" in detect_signals(diff("a.sql", "ALTER TABLE t ADD COLUMN x int"))


def test_removed_and_context_lines_do_not_count_and_results_are_a_set() -> None:
    only_removed = diff("a.py", "x = 1", removed='@router.get("/x")')
    assert detect_signals(only_removed) == set()
    both = diff("app/auth/x.py", '@router.get("/x")', "amount = 1", "r = requests.get(u)")
    assert detect_signals(both) == {"auth", "endpoint", "money", "outbound_call"}
    assert detect_signals([]) == set()
