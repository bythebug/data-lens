"""
Seeds the database with a demo account and three sample datasets.
Idempotent: safe to run multiple times.

Demo credentials:
  user_id : 1  (hardcoded in the frontend X-User-Id header)
  email   : demo@data-lens.app
"""
import io
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from db.models import Dataset, User  # noqa: E402
from ingestion.pipeline import ingest_dataset  # noqa: E402

DEMO_USER_ID = 1
DEMO_EMAIL = "demo@data-lens.app"


# ── CSV helpers ────────────────────────────────────────────────────────────────

def _make_csv(headers: list[str], rows: list[list]) -> io.BytesIO:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return io.BytesIO(buf.getvalue().encode())


# ── Dataset generators ─────────────────────────────────────────────────────────

def _gen_sales() -> tuple[list[str], list[list]]:
    """500 rows of daily sales across regions, categories, and products."""
    rng = random.Random(42)
    headers = ["date", "region", "category", "product", "revenue", "quantity", "discount_pct"]
    categories = {
        "Electronics": ["Laptop", "Smartphone", "Tablet", "Monitor", "Headphones"],
        "Clothing":    ["Jacket", "Sneakers", "Shirt", "Denim", "Cap"],
        "Food":        ["Coffee Beans", "Green Tea", "Energy Bar", "Juice", "Granola"],
        "Books":       ["Sci-Fi Novel", "Biography", "History", "Tech Guide", "Art Book"],
        "Home":        ["Floor Lamp", "Throw Pillow", "Area Rug", "Armchair", "Succulent"],
    }
    regions = ["North", "South", "East", "West"]
    start = date(2023, 1, 1)
    rows = []
    for _ in range(500):
        d = start + timedelta(days=rng.randint(0, 699))
        region = rng.choice(regions)
        cat = rng.choice(list(categories.keys()))
        product = rng.choice(categories[cat])
        qty = rng.randint(1, 50)
        unit_price = rng.uniform(8.0, 600.0)
        discount = rng.choice([0, 5, 10, 15, 20])
        revenue = round(qty * unit_price * (1 - discount / 100), 2)
        rows.append([d.isoformat(), region, cat, product, revenue, qty, discount])
    rows.sort(key=lambda r: r[0])
    return headers, rows


def _gen_employees() -> tuple[list[str], list[list]]:
    """150 employee records with numeric and date columns for rich stats."""
    rng = random.Random(99)
    headers = ["name", "department", "role", "salary", "age", "tenure_years",
               "hire_date", "performance_score"]
    dept_roles = {
        "Engineering": ["Software Engineer", "Senior Engineer", "Tech Lead", "Architect"],
        "Sales":       ["Account Executive", "Sales Manager", "SDR", "VP Sales"],
        "Marketing":   ["Marketing Manager", "Content Writer", "Designer", "SEO Analyst"],
        "HR":          ["HR Manager", "Recruiter", "HR Coordinator", "HRBP"],
        "Finance":     ["Financial Analyst", "Accountant", "Controller", "CFO"],
        "Operations":  ["Ops Manager", "Project Manager", "Coordinator", "VP Ops"],
    }
    dept_base_salary = {
        "Engineering": 125_000, "Finance": 95_000, "Sales": 82_000,
        "Marketing": 78_000, "HR": 72_000, "Operations": 88_000,
    }
    first_names = [
        "Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henry",
        "Iris", "James", "Karen", "Leo", "Maya", "Nick", "Olivia", "Paul",
        "Quinn", "Rachel", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
        "Yara", "Zoe", "Aaron", "Beth", "Chris", "Diana", "Evan", "Fiona",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "Lee", "White",
        "Harris", "Martin", "Thompson", "Young", "King", "Wright", "Scott",
    ]
    rows = []
    for _ in range(150):
        name = f"{rng.choice(first_names)} {rng.choice(last_names)}"
        dept = rng.choice(list(dept_roles))
        role = rng.choice(dept_roles[dept])
        base = dept_base_salary[dept]
        salary = round(base * rng.uniform(0.70, 1.55) / 1000) * 1000
        age = rng.randint(23, 62)
        tenure = rng.randint(0, min(age - 22, 18))
        hire_year = 2024 - tenure
        hire_date = date(hire_year, rng.randint(1, 12), rng.randint(1, 28))
        perf = round(rng.uniform(2.0, 5.0), 1)
        rows.append([name, dept, role, salary, age, tenure, hire_date.isoformat(), perf])
    return headers, rows


def _gen_web_traffic() -> tuple[list[str], list[list]]:
    """~1 800 rows of daily traffic per channel — good for time-series queries."""
    rng = random.Random(77)
    headers = ["date", "channel", "page", "sessions", "bounce_rate",
               "avg_duration_sec", "conversions"]
    channels = ["Organic", "Paid Search", "Social", "Direct", "Email", "Referral"]
    pages = ["/home", "/pricing", "/features", "/blog", "/docs", "/signup", "/contact"]
    channel_multiplier = {
        "Organic": 1.0, "Paid Search": 2.5, "Social": 0.7,
        "Direct": 1.2, "Email": 0.4, "Referral": 0.3,
    }
    rows = []
    start = date(2023, 1, 1)
    for i in range(365):
        d = start + timedelta(days=i)
        trend = 1 + i / 365 * 0.5          # 50 % growth over the year
        seasonality = 1 + 0.2 * abs((i % 7) - 3) / 3  # weekly bump
        for channel in rng.sample(channels, rng.randint(4, 6)):
            page = rng.choice(pages)
            base = rng.randint(80, 800)
            sessions = max(1, int(base * channel_multiplier[channel] * trend * seasonality
                                  * rng.uniform(0.8, 1.2)))
            bounce = round(rng.uniform(0.25, 0.75), 2)
            duration = rng.randint(25, 320)
            conversions = int(sessions * rng.uniform(0.005, 0.04))
            rows.append([d.isoformat(), channel, page, sessions, bounce, duration, conversions])
    rows.sort(key=lambda r: r[0])
    return headers, rows


# ── Main ───────────────────────────────────────────────────────────────────────

SAMPLE_DATASETS = [
    ("Sales 2023–2024",       _gen_sales),
    ("Employee Directory",    _gen_employees),
    ("Website Traffic 2023",  _gen_web_traffic),
]


def seed() -> None:
    db = SessionLocal()
    try:
        # ── Demo user (id=1 matches the hardcoded X-User-Id in the frontend) ──
        existing_user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if existing_user:
            user_id = existing_user.id
            print(f"Demo user already exists (id={user_id}), checking datasets…")
        else:
            db.execute(
                text("INSERT INTO users (id, email) VALUES (:id, :email) ON CONFLICT (id) DO NOTHING"),
                {"id": DEMO_USER_ID, "email": DEMO_EMAIL},
            )
            # Advance the sequence so future auto-generated ids don't collide
            db.execute(
                text("SELECT setval(pg_get_serial_sequence('users', 'id'), :val, true)"),
                {"val": DEMO_USER_ID},
            )
            db.commit()
            user_id = DEMO_USER_ID
            print(f"Created demo user: {DEMO_EMAIL} (id={user_id})")

        # ── Sample datasets ────────────────────────────────────────────────────
        existing_names = {
            d.name
            for d in db.query(Dataset).filter(Dataset.user_id == user_id).all()
        }

        for ds_name, gen_fn in SAMPLE_DATASETS:
            if ds_name in existing_names:
                print(f"  '{ds_name}' already exists, skipping.")
                continue
            headers, rows = gen_fn()
            csv_bytes = _make_csv(headers, rows)
            ds = ingest_dataset(db=db, user_id=user_id, file_stream=csv_bytes, dataset_name=ds_name)
            print(f"  Seeded '{ds_name}': {ds.row_count} rows (id={ds.id})")

        print("Demo seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
